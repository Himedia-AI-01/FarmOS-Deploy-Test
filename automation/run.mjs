#!/usr/bin/env node
/**
 * Web_Starter NodeJS 자동화 — 단일 .mjs 진입점.
 *
 * 책임 (plan §1):
 *   [1] 메타 로드 (bootstrap/export_meta.py)
 *   [2] DB 검증 (psql CLI 로 information_schema 조회)
 *   [3] plan §2 분기표대로 Phase 1 / Phase 2 호출
 *   [4] 재검증
 *   [5] exit code 반환
 *
 * 의존성: 없음. Node 22+ 기본 모듈(child_process, path, url) + 시스템 `psql`, `python` 만 필요.
 *
 * 실행: `node automation/run.mjs`
 *
 * 환경변수:
 *   FARMOS_PROJECT_ROOT   repo 루트(미지정 시 자동 추론)
 *   PG_HOST / PGHOST      기본 localhost
 *   PG_PORT / PGPORT      기본 5432
 *   PG_DATABASE / PGDATABASE  기본 farmos
 *   PG_USER / PGUSER      기본 postgres
 *   PG_PASSWORD / PGPASSWORD  기본 root
 *
 * Exit code:
 *   0  검증 통과 — Web_Starter.exe 가 후속 backend/frontend spawn
 *   10 컬럼 타입 drift (자동 ALTER 금지, 사용자 개입 필요)
 *   20 재검증 실패 (시드 후에도 결함)
 *   30 환경 오류 (psql/python/메타 로드 실패)
 *   1  예상치 못한 예외
 */

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ============================================================================
// 로깅
// ============================================================================
const PREFIX = "[automation]";
const info = (m) => process.stdout.write(`${PREFIX} ${m}\n`);
const warn = (m) => process.stdout.write(`${PREFIX} [WARN] ${m}\n`);
const error = (m) => process.stderr.write(`${PREFIX} [ERROR] ${m}\n`);
const section = (t) => process.stdout.write(`\n${PREFIX} === ${t} ===\n`);

// ============================================================================
// 설정
// ============================================================================
const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = process.env.FARMOS_PROJECT_ROOT || path.resolve(here, "..");

const EXIT_OK = 0;
const EXIT_COLUMN_DRIFT = 10;
const EXIT_REVERIFY_FAILED = 20;
const EXIT_ENV_ERROR = 30;
const EXIT_PHASE_FAILED = 40;
const EXIT_UNEXPECTED = 1;

const rawPort = process.env.PG_PORT || process.env.PGPORT || "5432";
const parsedPort = Number.parseInt(rawPort, 10);
if (!Number.isFinite(parsedPort)) {
  error(`PG_PORT/PGPORT 값이 유효한 숫자가 아닙니다: "${rawPort}"`);
  process.exit(EXIT_ENV_ERROR);
}

const dbConf = {
  host: process.env.PG_HOST || process.env.PGHOST || "localhost",
  port: parsedPort,
  database: process.env.PG_DATABASE || process.env.PGDATABASE || "farmos",
  user: process.env.PG_USER || process.env.PGUSER || "postgres",
  password: process.env.PG_PASSWORD || process.env.PGPASSWORD || "root",
};

// ============================================================================
// psql CLI 헬퍼
// ============================================================================
const FIELD_SEP = "\x1f"; // Unit Separator — 일반 데이터에 등장하지 않음

/** PostgreSQL 자식 프로세스에 공통으로 주입할 환경변수.
 *  서버/클라이언트 양쪽의 메시지 로케일을 영어로 강제 시도. */
function pgChildEnv() {
  return {
    ...process.env,
    PGPASSWORD: dbConf.password,
    PGCLIENTENCODING: "UTF8",
    LC_MESSAGES: "C",
    LANG: "C",
    // 서버 측 세션 옵션 — 인증 후 메시지에 영향. 인증 단계 메시지엔 무력할 수 있음.
    PGOPTIONS: "-c lc_messages=C",
  };
}

/** stderr Buffer 를 안전하게 문자열로 변환.
 *  - utf-8 로 디코드 가능하면 그대로 반환 (영문/UTF-8 한글 모두 OK)
 *  - 디코드 실패(=cp949 등 비-UTF8 바이트 포함) 시 ASCII printable + 줄바꿈만 추출.
 *    한글 부분은 사라지지만 영문 키워드("FATAL", DB 이름 등) 와 깨짐 0 보장. */
function safeDecodeStderr(buffer) {
  if (!buffer || buffer.length === 0) return "";
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer).trim();
  } catch {
    let out = "";
    let lastWasSpace = false;
    for (let i = 0; i < buffer.length; i++) {
      const b = buffer[i];
      if (b === 0x0A || b === 0x0D) {
        out += String.fromCharCode(b);
        lastWasSpace = false;
      } else if (b >= 0x20 && b <= 0x7E) {
        out += String.fromCharCode(b);
        lastWasSpace = false;
      } else if (!lastWasSpace) {
        out += " ";
        lastWasSpace = true;
      }
    }
    return out.replace(/ +/g, " ").replace(/\n +/g, "\n").trim();
  }
}

function psqlExec(sql, opts = {}) {
  const args = [
    "-X",                   // 사용자 .psqlrc 무시 — 출력 포맷이 사용자 설정에 의해 바뀌지 않도록.
    "-h", dbConf.host,
    "-p", String(dbConf.port),
    "-U", dbConf.user,
    "-d", opts.database || dbConf.database,
    "-tA",                  // tuples-only, unaligned
    "-F", FIELD_SEP,
    "-c", sql,
  ];
  // encoding 옵션을 주지 않아 Buffer 로 받는다 — stderr 만 별도 안전 디코드.
  const result = spawnSync("psql", args, { env: pgChildEnv() });
  if (result.error) {
    if (result.error.code === "ENOENT") {
      throw new Error("psql 실행 파일을 찾을 수 없습니다. PostgreSQL 의 bin 디렉터리를 PATH 에 추가하세요.");
    }
    throw result.error;
  }
  if (result.status !== 0) {
    const stderr = safeDecodeStderr(result.stderr);
    const lower = stderr.toLowerCase();
    let hint = "";
    if (lower.includes("password authentication failed") || lower.includes("authentication failed")) {
      hint = `PostgreSQL 비밀번호가 잘못됐습니다 (user=${dbConf.user}). 설정의 DbPassword 값을 확인하세요.`;
    } else if (lower.includes("does not exist") && lower.includes("database")) {
      hint = `데이터베이스 "${opts.database || dbConf.database}" 가 존재하지 않습니다. PostgreSQL 에 DB 를 먼저 생성하세요.`;
    } else if (lower.includes("connection refused") || lower.includes("could not connect")) {
      hint = `PostgreSQL 서버(${dbConf.host}:${dbConf.port}) 에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.`;
    } else if (lower.includes("role") && lower.includes("does not exist")) {
      hint = `PostgreSQL 사용자(role) "${dbConf.user}" 가 존재하지 않습니다.`;
    } else if (lower.includes("no pg_hba.conf entry")) {
      hint = `PostgreSQL pg_hba.conf 가 ${dbConf.host} 에서의 접속을 허용하지 않습니다.`;
    }
    throw new Error(
      `psql 실패 (exit=${result.status})${hint ? " — " + hint : ""}\n--- stderr ---\n${stderr}`,
    );
  }
  return (result.stdout || Buffer.alloc(0)).toString("utf-8");
}

/** 사전 체크 — 인증/연결/대상 DB 존재 여부를 시스템 DB(`postgres`) 접속으로 검사.
 *  실패 시 명확한 한글 메시지로 throw. 본격 검증 전에 호출해 사용자에게 즉시 안내. */
function preflightCheck() {
  const targetDb = dbConf.database;
  const sql = `SELECT 1 FROM pg_database WHERE datname='${targetDb.replace(/'/g, "''")}'`;
  const args = [
    "-X",                   // 사용자 .psqlrc 무시 — preflight 결과를 결정적으로 유지.
    "-h", dbConf.host,
    "-p", String(dbConf.port),
    "-U", dbConf.user,
    "-d", "postgres",
    "-tA", "-c", sql,
  ];
  const result = spawnSync("psql", args, { env: pgChildEnv() });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      throw new Error("psql 실행 파일을 찾을 수 없습니다. PostgreSQL 의 bin 디렉터리를 PATH 에 추가하세요.");
    }
    throw result.error;
  }

  if (result.status !== 0) {
    const stderr = safeDecodeStderr(result.stderr);
    const lower = stderr.toLowerCase();
    if (lower.includes("password authentication") || lower.includes("authentication failed")) {
      throw new Error(`PostgreSQL 비밀번호가 잘못됐습니다 (user=${dbConf.user}). 설정의 DbPassword 값을 확인하세요.`);
    }
    if (lower.includes("connection refused") || lower.includes("could not connect") || lower.includes("could not translate host name")) {
      throw new Error(`PostgreSQL 서버(${dbConf.host}:${dbConf.port}) 에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.`);
    }
    if (lower.includes("role") && lower.includes("does not exist")) {
      throw new Error(`PostgreSQL 사용자(role) "${dbConf.user}" 가 존재하지 않습니다.`);
    }
    if (lower.includes("no pg_hba.conf entry")) {
      throw new Error(`PostgreSQL pg_hba.conf 가 ${dbConf.host} 에서의 접속을 허용하지 않습니다.`);
    }
    throw new Error(`PostgreSQL 사전 체크 실패 (exit=${result.status})\n--- stderr ---\n${stderr}`);
  }

  const stdout = (result.stdout || Buffer.alloc(0)).toString("utf-8").trim();
  if (stdout !== "1") {
    throw new Error(
      `데이터베이스 "${targetDb}" 가 존재하지 않습니다 (host=${dbConf.host}:${dbConf.port}). ` +
      `설정의 DbName 값을 확인하거나 PostgreSQL 에 DB 를 먼저 생성하세요.`,
    );
  }

  info(`사전 체크 통과 — 인증 OK, DB "${targetDb}" 존재 확인`);
}

/** additive schema patches that are safe to apply before metadata verification.
 *  Backend startup has the same patch, but Web_Starter verifies DB shape before
 *  starting backend; without this hook teammates with an existing DB would stop
 *  at column drift and never reach the backend patch. */
function applySafeSchemaPatches() {
  section("안전 스키마 보강");
  const sql = `
DO $$
BEGIN
  IF to_regclass('public.shop_tickets') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM information_schema.columns
       WHERE table_schema='public'
         AND table_name='shop_tickets'
         AND column_name='flags'
     )
  THEN
    ALTER TABLE public.shop_tickets ADD COLUMN flags TEXT NOT NULL DEFAULT '[]';
  END IF;
END $$;
`;
  psqlExec(sql);
  info("additive schema patch 확인 완료 — shop_tickets.flags");
}

/** psql 결과를 행/필드 2차원 배열로. 빈 줄 무시.
 *  Windows psql 은 `\r\n` 으로 줄바꿈을 출력하므로 CR 도 함께 처리한다 — 그렇지 않으면
 *  테이블명 끝에 `\r` 이 붙어 비교가 모두 mismatch 가 된다. */
function psqlRows(sql, opts) {
  const out = psqlExec(sql, opts);
  return out
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => line.split(FIELD_SEP));
}

function quoteIdent(name) {
  return '"' + name.replace(/"/g, '""') + '"';
}

function quoteString(value) {
  return "'" + String(value).replace(/'/g, "''") + "'";
}

// ============================================================================
// 메타 로딩 (Python export_meta.py)
// ============================================================================
function loadMeta() {
  info("bootstrap/export_meta.py 호출 → 메타 로딩");
  const result = spawnSync("python", [path.join(projectRoot, "bootstrap", "export_meta.py")], {
    cwd: projectRoot,
    encoding: "utf-8",
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) {
    if (result.error.code === "ENOENT") {
      throw new Error("python 실행 파일을 찾을 수 없습니다.");
    }
    throw result.error;
  }
  if (result.status !== 0) {
    error(`export_meta.py exit=${result.status}\nstderr:\n${result.stderr}`);
    throw new Error("export_meta.py failed");
  }
  let meta;
  try {
    meta = JSON.parse(result.stdout);
  } catch (e) {
    error(`메타 JSON 파싱 실패: ${e.message}`);
    error(`stdout(앞 1000자):\n${result.stdout.slice(0, 1000)}`);
    throw e;
  }
  const farmosCount = Object.keys(meta.farmos.tables).length;
  const shopCount = Object.keys(meta.shoppingmall.tables).length;
  info(`메타 로드 완료 — FarmOS ${farmosCount} 테이블, ShoppingMall ${shopCount} 테이블`);
  return meta;
}

// ============================================================================
// 타입 정규화 (model vs PostgreSQL data_type)
// ============================================================================
function normalizeModelType(t) {
  const u = String(t).toUpperCase().trim();
  if (u.startsWith("VARCHAR") || u.startsWith("STRING") || u === "TEXT") return "text-like";
  if (u === "INTEGER" || u === "INT") return "integer";
  if (u === "BIGINT") return "bigint";
  if (u === "BOOLEAN") return "boolean";
  if (u === "DATE") return "date";
  if (u.startsWith("DATETIME") || u.startsWith("TIMESTAMP")) return "timestamp";
  if (u === "JSONB") return "jsonb";
  if (u === "JSON") return "json";
  if (u === "FLOAT" || u === "REAL" || u === "DOUBLE_PRECISION") return "numeric";
  return u.toLowerCase();
}

function normalizePgType(t) {
  const u = String(t).toLowerCase().trim();
  if (["character varying", "varchar", "text", "char", "character"].includes(u)) return "text-like";
  if (u === "integer") return "integer";
  if (u === "bigint") return "bigint";
  if (u === "boolean") return "boolean";
  if (u === "date") return "date";
  if (u.includes("timestamp")) return "timestamp";
  if (u === "jsonb") return "jsonb";
  if (u === "json") return "json";
  if (["real", "double precision", "numeric"].includes(u)) return "numeric";
  return u;
}

// ============================================================================
// DB 검증
// ============================================================================
function verifyDatabase(meta) {
  const tableMetaByName = {
    ...meta.farmos.tables,
    ...meta.shoppingmall.tables,
  };
  const expectedTables = Object.keys(tableMetaByName);

  // 1) 현재 DB 테이블 목록
  const dbTables = new Set(
    psqlRows("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").map((r) => r[0]),
  );

  const missingTables = expectedTables.filter((t) => !dbTables.has(t));
  const presentExpected = expectedTables.filter((t) => dbTables.has(t));
  const columnIssues = [];
  const rowDeficits = [];

  if (presentExpected.length > 0) {
    // 2) 컬럼 정보 일괄 조회
    const tableList = presentExpected.map(quoteString).join(",");
    const colRows = psqlRows(
      `SELECT table_name, column_name, data_type, is_nullable
       FROM information_schema.columns
       WHERE table_schema='public' AND table_name IN (${tableList})`,
    );
    const dbColsByTable = new Map();
    for (const [tn, cn, dt, nn] of colRows) {
      let m = dbColsByTable.get(tn);
      if (!m) {
        m = new Map();
        dbColsByTable.set(tn, m);
      }
      m.set(cn, { type: dt, nullable: nn === "YES" });
    }

    for (const tableName of presentExpected) {
      const tableMeta = tableMetaByName[tableName];
      if (!tableMeta) continue;
      const dbCols = dbColsByTable.get(tableName) || new Map();
      for (const expectedCol of tableMeta.columns) {
        const dbCol = dbCols.get(expectedCol.name);
        if (!dbCol) {
          columnIssues.push({
            table: tableName,
            column: expectedCol.name,
            kind: "missing",
            detail: `model: ${expectedCol.type}, nullable=${expectedCol.nullable}`,
          });
          continue;
        }
        const mf = normalizeModelType(expectedCol.type);
        const df = normalizePgType(dbCol.type);
        if (mf !== df) {
          columnIssues.push({
            table: tableName,
            column: expectedCol.name,
            kind: "type_mismatch",
            detail: `model=${expectedCol.type}(${mf}) vs db=${dbCol.type}(${df})`,
          });
        }
      }
    }

    // 3) row 수 검증 — 각 테이블 COUNT(*) (Python 측 ready_row_counts/expected_row_counts 합산)
    //
    // 현재 meta.farmos.expected_row_counts 는 사실상 {"users": 2} 하나뿐이므로,
    // FarmOS 쪽 row 검증은 실질적으로 users 테이블만 확인한다.
    //
    // 의도적으로 이 루프에서 제외되는 메타값 (plan §3.4):
    //   - meta.farmos.post_pesticide_min_row_counts: 농약 RAG JSON/raw 데이터가 git 에
    //     포함되지 않고 사용자가 수동 적재하기 때문. 자동 검증에 넣으면 영구 row deficit
    //     으로 false fail 이 난다. → 결과적으로 ncpms 계열 테이블은 이 루프가 검사하지 않음.
    //   - meta.farmos.ai_agent_default_count: 시드 생성 파라미터(기본 30개)이지 검증
    //     임계치가 아니다. → 결과적으로 ai_agent 테이블은 이 루프가 검사하지 않음.
    //
    // shoppingmall 은 ready_row_counts 를 통해 review/카테고리 등을 모두 검사한다.
    const rowExp = {
      ...meta.farmos.expected_row_counts,
      ...meta.shoppingmall.ready_row_counts,
    };
    for (const [tableName, expectedCount] of Object.entries(rowExp)) {
      if (!dbTables.has(tableName)) continue; // missing 은 위에서 잡힘
      const r = psqlRows(`SELECT COUNT(*) FROM ${quoteIdent(tableName)}`);
      const actual = Number.parseInt(r[0][0], 10);
      if (actual < expectedCount) {
        rowDeficits.push({ table: tableName, expected: expectedCount, actual });
      }
    }
  }

  return { missingTables, columnIssues, rowDeficits };
}

const isVerifyClean = (r) =>
  r.missingTables.length === 0 && r.columnIssues.length === 0 && r.rowDeficits.length === 0;

function summarize(label, r) {
  section(`${label} 결과`);
  info(
    `missing tables=${r.missingTables.length}, ` +
      `column issues=${r.columnIssues.length}, ` +
      `row deficits=${r.rowDeficits.length}`,
  );
  if (r.missingTables.length > 0) {
    const head = r.missingTables.slice(0, 10).join(", ");
    info(`  - missing tables: ${head}${r.missingTables.length > 10 ? " ..." : ""}`);
  }
  for (const issue of r.columnIssues.slice(0, 10)) {
    info(`  - column ${issue.kind}: ${issue.table}.${issue.column}${issue.detail ? ` (${issue.detail})` : ""}`);
  }
  if (r.columnIssues.length > 10) info(`  - ... ${r.columnIssues.length - 10} more column issues`);
  for (const def of r.rowDeficits.slice(0, 10)) {
    info(`  - row deficit: ${def.table} (${def.actual}/${def.expected})`);
  }
  if (r.rowDeficits.length > 10) info(`  - ... ${r.rowDeficits.length - 10} more row deficits`);
}

// ============================================================================
// Phase 호출
// ============================================================================
function runPhase(phase) {
  const script = `bootstrap/${phase}.py`;
  section(`${phase} 실행: python ${script}`);
  const result = spawnSync("python", [path.join(projectRoot, script)], {
    cwd: projectRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${phase} exit=${result.status}`);
  info(`${phase} 완료`);
}

function runPostUpdateHooks() {
  section("ShoppingMall 후속 보강");
  const result = spawnSync("python", [path.join(projectRoot, "bootstrap", "apply_shop_updates.py")], {
    cwd: projectRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`apply_shop_updates.py exit=${result.status}`);
  info("ShoppingMall 후속 보강 완료");
}

// ============================================================================
// main
// ============================================================================
function main() {
  section("자동화 시작");
  info(`projectRoot=${projectRoot}`);
  info(`db=${dbConf.host}:${dbConf.port}/${dbConf.database} user=${dbConf.user}`);

  // 사전 체크 — 인증/연결/DB 존재 — 명확한 한글 메시지로 빠른 실패.
  try {
    preflightCheck();
    applySafeSchemaPatches();
  } catch (e) {
    error(e.message);
    return EXIT_ENV_ERROR;
  }

  let meta;
  try {
    meta = loadMeta();
  } catch (e) {
    error(`메타 로드 실패: ${e.message}`);
    return EXIT_ENV_ERROR;
  }

  let initial;
  try {
    initial = verifyDatabase(meta);
  } catch (e) {
    error(`DB 검증 실패: ${e.message}`);
    return EXIT_ENV_ERROR;
  }
  summarize("초기 검증", initial);

  // 분기 정책 (수정):
  //  - column drift(missing/type_mismatch 모두) 는 Phase 1/2 로 회복 불가하므로 분기 결정에서 제외.
  //    `Base.metadata.create_all` 은 IF NOT EXISTS 라 누락 컬럼 ALTER 안 함(plan §4).
  //    따라서 column drift 는 경고만 기록하고, table/row 결함만 보고 Phase 호출 여부를 결정한다.
  //  - column drift 만 단독으로 있으면 exit 10 (수동 마이그레이션 필요).
  const hasMissingTables = initial.missingTables.length > 0;
  const hasRowDeficit = initial.rowDeficits.length > 0;
  const hasColumnDrift = initial.columnIssues.length > 0;

  if (isVerifyClean(initial)) {
    info("DB 상태 정상 — Phase 1/2 호출 생략");
    try {
      runPostUpdateHooks();
    } catch (e) {
      error(`후속 보강 실패: ${e.message}`);
      return EXIT_PHASE_FAILED;
    }
    return EXIT_OK;
  }

  if (!hasMissingTables && !hasRowDeficit && hasColumnDrift) {
    warn("컬럼 drift(누락/타입 불일치) 감지 — 자동 ALTER 금지 정책에 따라 중단합니다.");
    warn("위 항목을 수동 검토(마이그레이션/dump 재생성 등)한 뒤 다시 실행해주세요.");
    return EXIT_COLUMN_DRIFT;
  }

  if (hasColumnDrift) {
    warn(
      `컬럼 drift ${initial.columnIssues.length}건 감지 — 시드로 회복 불가, 진행은 하되 경고로 표시합니다.`,
    );
  }

  try {
    if (hasMissingTables) {
      info("→ Phase 1 + Phase 2 순차 호출 (테이블 누락)");
      runPhase("create_tables");
      runPhase("insert_data");
    } else if (hasRowDeficit) {
      info("→ Phase 2 만 호출 (데이터만 부족)");
      runPhase("insert_data");
    }
  } catch (e) {
    error(`Phase 호출 실패: ${e.message}`);
    return EXIT_PHASE_FAILED;
  }

  let after;
  try {
    after = verifyDatabase(meta);
  } catch (e) {
    error(`재검증 DB 연결 실패: ${e.message}`);
    return EXIT_ENV_ERROR;
  }
  summarize("재검증", after);

  // 재검증: 시드로 회복 가능한 결함(missing tables / row deficit) 만 fail 로 본다.
  // column drift 는 시드로 못 고치므로 경고만 남기고 OK 종료 (사용자가 마이그레이션 결정).
  if (after.missingTables.length > 0 || after.rowDeficits.length > 0) {
    error("재검증 실패 — 시드 후에도 테이블 누락 또는 row 부족이 남아있습니다.");
    return EXIT_REVERIFY_FAILED;
  }

  if (after.columnIssues.length > 0) {
    warn(`재검증에서 컬럼 drift ${after.columnIssues.length}건 — 경고만 표시하고 진행`);
  }

  try {
    runPostUpdateHooks();
  } catch (e) {
    error(`후속 보강 실패: ${e.message}`);
    return EXIT_PHASE_FAILED;
  }

  info("자동화 정상 종료");
  return EXIT_OK;
}

try {
  process.exit(main());
} catch (e) {
  error(`예상치 못한 오류: ${e.stack || e}`);
  process.exit(EXIT_UNEXPECTED);
}

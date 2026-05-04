# IoT Review MCP — 리뷰 자동화 분석 MCP화 Plan

| 항목 | 값 |
|------|-----|
| Feature | iot-review-mcp |
| Author | clover0309 |
| Created | 2026-05-01 |
| Branch | feat/IoT_Review_MCP |
| Phase | Plan |
| PDCA Cycle | 1 of 2 (이번 사이클: MCP화만 / 다음 사이클: 오케스트레이션 에이전트) |

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| Problem | `review_analyzer` / `review_rag` / `review_report` / `trend_detector` 의 분석·검색·리포트 기능이 FastAPI HTTP 엔드포인트로만 노출되어 있어, 향후 도입할 오케스트레이션 에이전트가 이들을 도구(tool)로 직접 호출할 수 없다. 에이전트마다 HTTP 클라이언트·인증 코드를 매번 구현해야 하며, 입력 스키마/오류 계약도 표준화되어 있지 않다. |
| Solution | **FastMCP** 기반 MCP 서버를 구축해 기존 분석 함수들을 1:1 low-level tool로 노출한다. Core 모듈(`backend/app/core/review_*.py`)을 단일 소스로 재사용하고, FastAPI 라우터와 MCP tool은 동일 함수를 얇게 감싼다. 인증은 기존 JWT 미들웨어를 재사용하며(mount 전제), SSE 진행률은 MCP progress notification으로 매핑한다. 호스팅 형태(standalone HTTP / FastAPI mount / stdio)의 최종 결정은 Design Checkpoint에서 3안 비교로 확정한다. |
| Function / UX Effect | Claude Code, Claude Desktop, 향후 오케스트레이션 에이전트 등 모든 MCP 클라이언트가 동일 tool 카탈로그로 리뷰 분석/검색/리포트를 호출할 수 있다. 각 tool은 표준 JSON Schema 입력·출력을 가지며, 진행률을 progress notification으로 받는다. 기존 FastAPI/프론트엔드는 무변경으로 동작한다. |
| Core Value | (1) 분석 기능의 **재사용 가능한 도구화** — 다음 사이클의 오케스트레이션 에이전트가 즉시 합성 가능. (2) 단일 소스 + 얇은 어댑터로 **코드 중복 0**. (3) 표준 MCP 계약으로 **외부 통합 비용 절감**. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| WHY | 분석/검색/리포트 함수가 HTTP에만 노출되어 있어, 향후 오케스트레이션 에이전트가 도구로 호출할 표준 인터페이스가 없다. 매 에이전트마다 HTTP 어댑터를 새로 짜야 한다. |
| WHO | (1) 다음 사이클에 도입할 오케스트레이션 에이전트(Primary), (2) 개발자/관리자가 Claude Code·Claude Desktop에서 직접 분석을 호출하는 시나리오(Secondary), (3) 기존 FastAPI 사용 프론트엔드(불변, 회귀 무 보장 대상). |
| RISK | (1) FastMCP + FastAPI 통합 패턴 미검증 → mount 옵션 PoC 필요. (2) MCP 인증 표준이 미성숙(JWT 헤더 전달 방식). (3) DB 세션·ChromaDB 클라이언트 라이프사이클이 호스팅 형태에 따라 달라짐. (4) 기존 FastAPI 회귀 가능성 — 코어 함수 추출/재배치 시 import 경로 변경. |
| SUCCESS | (SC-01) FastMCP 서버가 기동되고 최소 7개 tool을 노출한다. (SC-02) MCP 클라이언트 호출 결과가 동일 입력 대비 기존 FastAPI 응답과 일치한다(snapshot 기반). (SC-03) 기존 FastAPI 7개 엔드포인트 회귀 없음(수동 smoke OK). (SC-04) 멀티테넌트 컨텍스트(seller_id)가 MCP 호출자 인증을 통해 전달·적용된다. (SC-05) 분석 진행률이 MCP progress notification으로 전달된다(`/analyze/stream` 대응 tool 한정). (SC-06) 코어 함수 단일 소스 — `core/review_*.py`는 분기 없이 양쪽에서 사용. |
| SCOPE | IN: FastMCP 서버 entry point, 7개 low-level tool, 인증 어댑터, progress notification 매핑, ChromaDB/DB 세션 의존성 주입 패턴. OUT: 오케스트레이션 에이전트 본체, 신규 분석 기능, 자동 배치 스케줄러, 프론트엔드 변경, MCP 서버 운영 모니터링. |

---

## 1. 배경

- 현재 리뷰 자동화 분석은 `backend/app/api/review_analysis.py` 라우터로 7개 HTTP 엔드포인트(`/analyze`, `/analyze/stream`, `/embed`, `/embed/stream`, `/search`, `/analysis`, `/trends`, `/report/pdf`, `/settings`)를 노출하며, `core/review_rag.py` / `core/review_analyzer.py` / `core/review_report.py` / `core/trend_detector.py` 4개 코어 모듈에 로직이 집중되어 있다 (archived `review-analysis-automation` 사이클의 산출물).
- 임베딩 파이프라인은 LiteLLM 프록시 + VoyageAI `voyage-3.5` (1024-dim, ChromaDB collection `reviews_voyage_v35`)이며 안정 운영 중. 본 사이클은 이 파이프라인을 **건드리지 않는다**.
- 현재 브랜치 `feat/IoT_Review_MCP` 는 본 사이클을 위한 작업 브랜치이다.
- 다음 사이클의 "오케스트레이션 에이전트"는 LLM 에이전트가 본 MCP 서버의 tool들을 합성 호출(예: `embed → analyze → trend → report` 순)해서 결과를 단일 응답으로 묶는 역할이다. 이번 사이클은 그 에이전트가 사용할 **도구 표면(tool surface)** 만 만든다.

## 2. 요구사항

### 2.1 기능 요구

| ID | 요구사항 | 우선순위 |
|----|----------|---------|
| FR-1 | FastMCP 기반 MCP 서버 entry point 구축 (`backend/app/mcp/review_mcp_server.py` 가설 경로) | P0 |
| FR-2 | `embed_reviews` tool — DB 리뷰 → ChromaDB 동기화 (`ReviewRAG.sync_from_db`) | P0 |
| FR-3 | `search_reviews` tool — 자연어 의미 검색 (`ReviewRAG.search`, top_k/filters/seller_id 지원) | P0 |
| FR-4 | `analyze_reviews` tool — 배치 분석 + DB 저장 (`ReviewAnalyzer.analyze_batch` + `TrendDetector` + `ReviewAnalysis` insert), 결과는 `analysis_id` + 요약 동시 반환 | P0 |
| FR-5 | `analyze_reviews_with_progress` tool — `analyze_batch_with_progress` 를 progress notification으로 매핑 | P0 |
| FR-6 | `get_latest_analysis` tool — 최신 분석 결과 조회 (DB) | P0 |
| FR-7 | `get_analysis_by_id` tool — 특정 `analysis_id` 조회 | P1 |
| FR-8 | `get_trends` tool — 최신 트렌드/이상 탐지 데이터 반환 | P1 |
| FR-9 | `generate_pdf_report` tool — PDF 바이너리 반환 (MCP `resource` 또는 base64 인코딩 — Design에서 확정) | P1 |
| FR-10 | `get_analysis_settings` / `update_analysis_settings` tool — 자동 분석 설정 (P2) | P2 |
| FR-11 | 모든 tool은 표준 입력 스키마(JSON Schema)와 표준 오류 응답 형식을 갖는다 | P0 |
| FR-12 | `seller_id` 컨텍스트가 MCP 호출자 인증(JWT)에서 추출되어 멀티테넌트 필터에 전달된다 | P0 |

### 2.2 비기능 요구

| ID | 내용 |
|----|------|
| NFR-1 | 코어 함수(`core/review_*.py`)는 본 사이클에서 시그니처 변경 없음 — MCP 서버는 얇은 어댑터(adapter) 레이어로만 호출 |
| NFR-2 | 기존 FastAPI 라우터(`api/review_analysis.py`) 7개 엔드포인트는 동일 동작 유지 (회귀 없음) |
| NFR-3 | DB 세션은 `core.database.get_db` 동일 패턴, 호스팅 형태에 따라 dependency injection 또는 컨텍스트 매니저로 주입 |
| NFR-4 | ChromaDB 클라이언트 + `ReviewRAG`/`ReviewAnalyzer`/`ReviewReportGenerator`/`TrendDetector` 인스턴스는 MCP 서버 라이프사이클 내에서 싱글턴 (현 라우터와 동일 패턴) |
| NFR-5 | 인증 실패 시 MCP 표준 오류 응답 (호스팅 옵션에 따라 401-equivalent) |
| NFR-6 | tool 입력 검증 실패 시 MCP 표준 validation error |
| NFR-7 | progress notification은 `analyze_reviews_with_progress` 에 한정, 다른 tool은 단일 응답 |
| NFR-8 | FastMCP 의존성 추가 시 `pyproject.toml`/`requirements.txt` 갱신, 기존 의존성과 충돌 없음 |

### 2.3 확정된 설계 결정 (Plan-fix)

| ID | 결정 | 근거 |
|----|------|------|
| D-1 | tool 입자도 = **1:1 low-level** (high-level wrapper는 다음 사이클의 오케스트레이션 에이전트에 위임) | 사용자 확정 (Checkpoint 2). 조절성·재사용성 우선. |
| D-2 | 인증/멀티테넌트 = **기존 JWT 미들웨어 재사용** (mount 전제) | 사용자 확정. 코드 중복 0 + 검증된 인증 경로. → 호스팅 옵션 C(FastAPI mount)를 Design에서 우선안으로 검토. |
| D-3 | progress 매핑 = **MCP progress notification (`ctx.report_progress`)** | 사용자 확정. SSE → progress 직역. 기존 frontend SSE는 그대로 유지. |
| D-4 | 호스팅 형태 = **Design Checkpoint 3에서 3안 비교 후 확정** | 사용자 확정. Plan에는 후보(A/B/C)와 D-2가 부과하는 제약만 기록. |
| D-5 | tool 매핑 정책 = 기존 FastAPI 엔드포인트 ↔ MCP tool **거의 1:1** (단, `/analyze/stream` 은 progress 변형으로 별 tool, `/settings` GET/PUT 은 별도 tool 2개) | D-1, D-3과 정합. |

## 3. 범위

### 3.1 포함 (In-scope)

1. **FastMCP 서버**
   - entry point 모듈 (가설: `backend/app/mcp/review_mcp_server.py`)
   - tool 등록 + 입력 스키마(Pydantic 또는 dataclass) + 출력 스키마
   - 코어 인스턴스(RAG/Analyzer/TrendDetector/ReportGenerator) lifespan 관리
   - 인증 어댑터(JWT 검증 → `seller_id` 추출 → tool 컨텍스트 주입)
   - progress notification 어댑터 (`analyze_batch_with_progress` 의 SSE update → `ctx.report_progress`)
2. **기존 코드 변경 (최소)**
   - `core/review_*.py` 시그니처 불변
   - `api/review_analysis.py` 의 헬퍼(`_get_seller_product_ids`, `_stratified_sample`)를 코어 또는 공유 모듈로 이동 (코드 중복 방지) — 단, FastAPI 동작 무변경
   - `app/main.py` 또는 lifespan에 MCP 서버 mount 또는 별도 launcher (호스팅 형태에 따라)
3. **의존성**
   - `fastmcp` 패키지 추가 (버전은 Design에서 검증)
4. **문서**
   - 본 Plan, 후속 Design, Do, Analysis, Report
   - `docs/iot-review-analysis-implementation.md` 에 MCP 섹션 추가 (Do phase)
5. **검증 (Check phase)**
   - tool 7~10개 호출 → 동일 입력으로 FastAPI 응답과 비교 (snapshot)
   - 기존 FastAPI 회귀 수동 smoke

### 3.2 제외 (Out-of-scope)

- 오케스트레이션 에이전트 본체 (다음 PDCA 사이클)
- High-level wrapper tool (예: `run_full_analysis`) — 다음 사이클
- 자동 배치 스케줄러 (이미 archived design에서 Phase 2로 연기됨)
- 프론트엔드 변경 (`frontend/src/components/reviews/*`)
- 새로운 분석 알고리즘, LLM 프롬프트 개선
- 임베딩 파이프라인 마이그레이션 (LiteLLM/VoyageAI 그대로)
- MCP 서버 운영 모니터링/메트릭 수집
- MCP server를 실제 production deploy (이번 사이클은 dev 검증까지)

## 4. 수용 기준 (Acceptance Criteria)

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| AC-1 | FastMCP 서버 기동 시 등록된 tool 목록이 7개 이상 노출됨 | MCP `tools/list` 호출 |
| AC-2 | `search_reviews` tool 호출 결과가 `POST /api/v1/reviews/search` 와 동일 결과 셋 (동일 query/top_k/filters) | 양쪽 응답 JSON diff |
| AC-3 | `analyze_reviews` tool 호출 후 `analysis_id` 반환 + DB `review_analyses` 에 row 추가됨 | psql `SELECT` |
| AC-4 | `analyze_reviews_with_progress` 가 progress notification 을 최소 3회 이상 emit | MCP 클라이언트 로그 확인 |
| AC-5 | 인증 토큰 없이 호출 시 MCP 표준 오류 반환 (호스팅 형태별 매핑은 Design에서 확정) | curl/MCP Inspector |
| AC-6 | seller_id 가 부여된 토큰으로 `search_reviews` 호출 시, 해당 seller 의 product_ids 로 필터 적용됨 | 두 토큰의 결과 비교 |
| AC-7 | 기존 FastAPI 7개 엔드포인트가 변경 전과 동일하게 동작 (smoke) | curl 7회 |
| AC-8 | 코어 모듈(`core/review_*.py`) diff 가 기존 시그니처 변경 없음 (import/주석 외 비기능 변경 가능) | git diff 검토 |
| AC-9 | `pyproject.toml` 에 `fastmcp` 추가, `pip install` 후 import 가능 | `python -c "import fastmcp"` |
| AC-10 | `/analyze/stream`, `/embed/stream`, `/report/pdf` 의 MCP 대응 tool이 정의되고 (PDF 는 base64 또는 resource), 1회 이상 정상 호출 성공 | 통합 호출 |

## 5. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| FastMCP + FastAPI mount 패턴이 FastMCP 버전에 따라 차이 (0.x vs 2.x API) | mount 옵션 자체가 깨짐 | Design Checkpoint 3 전에 짧은 spike — 빈 tool 1개로 mount/standalone PoC. 막히면 옵션 B(별 포트)로 대피. |
| JWT 검증 코드를 MCP 컨텍스트로 옮길 때 의존성(Depends, AsyncSession) 깨짐 | 인증 우회 또는 401 남발 | mount 옵션이면 HTTP 미들웨어 단계에서 검증 후 `request.state` 로 전달. standalone HTTP 옵션이면 FastMCP middleware 패턴으로 별도 검증. |
| ChromaDB 클라이언트가 멀티 프로세스 환경에서 충돌 | search/embed 실패 | 호스팅 옵션 C(같은 프로세스)에서는 무관. 옵션 B로 가면 별 프로세스이지만 ChromaDB는 HTTP 클라이언트라 무관함을 검증. |
| `_stratified_sample` 등 라우터 헬퍼를 공유 모듈로 옮기다 FastAPI 회귀 발생 | 기존 동작 깨짐 | 한번에 옮기지 말고: (1) 함수만 그대로 추출, (2) 라우터에서 import 해서 호출, (3) MCP tool도 동일 import. 행동 변화 0 검증 후 진행. |
| MCP progress notification을 클라이언트가 무시하면 사용자가 "멈춘" 것처럼 봄 | UX | MCP tool 응답에 마지막 진행률 포함. 기존 SSE 엔드포인트는 그대로 유지하므로 frontend 영향 없음. |
| PDF 바이너리 반환 방식 (resource vs base64) 결정 보류 | tool 9 형태가 안 정해짐 | Design에서 FastMCP가 지원하는 binary return 형식 확인 후 확정. 백업: 임시 파일 path 반환 + 별 endpoint 다운로드. |
| 본 사이클에서 다음 사이클의 오케스트레이션 에이전트 요구를 모르는 채 입력 스키마를 너무 좁게 잡음 | 다음 사이클에서 전부 손봐야 함 | 입력 스키마는 기존 FastAPI Pydantic 스키마 1:1 미러링. 추가 컨텍스트 필드는 `metadata` dict로 열어둠 (확장 가능). |

## 6. Impact Analysis

### 6.1 변경 리소스

| Resource | Type | Change Description |
|----------|------|--------------------|
| `backend/app/core/review_rag.py` | Module | 변경 **없음** (얇은 어댑터에서 호출만 추가) |
| `backend/app/core/review_analyzer.py` | Module | 변경 **없음** |
| `backend/app/core/review_report.py` | Module | 변경 **없음** |
| `backend/app/core/trend_detector.py` | Module | 변경 **없음** |
| `backend/app/api/review_analysis.py` | Router | 헬퍼 추출(`_get_seller_product_ids`, `_stratified_sample`) — 동작 무변경 |
| `backend/app/mcp/__init__.py` | New file | MCP 서버 모듈 진입점 |
| `backend/app/mcp/review_mcp_server.py` | New file | FastMCP 서버 + tool 정의 |
| `backend/app/mcp/auth.py` | New file | JWT → seller_id 어댑터 (mount 옵션 시 FastAPI middleware 재사용) |
| `backend/app/main.py` | App entry | (옵션 C 채택 시) MCP 앱 mount |
| `pyproject.toml` / `requirements.txt` | Deps | `fastmcp` 추가 |

### 6.2 기존 소비자 (FastAPI 라우터의 영향 범위)

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `_get_seller_product_ids` | call | `api/review_analysis.py` 5곳 | 헬퍼 추출 후 import 변경 — 동작 동일 |
| `_stratified_sample` | call | `api/review_analysis.py` 2곳 | 동일 |
| `_rag` / `_analyzer` / `_trend_detector` / `_report_generator` (싱글턴) | call | `api/review_analysis.py` 전역 | MCP 서버에서도 같은 인스턴스 공유할지(옵션 C) 별 인스턴스(옵션 B)일지 — Design에서 확정 |
| `core/review_*.py` 함수들 | call | 위 라우터 | 시그니처 불변 → 회귀 없음 |
| frontend `useReviewAnalysis.ts` | call | FastAPI 7 endpoints | **변경 없음** |

### 6.3 검증

- [ ] 기존 FastAPI 7개 엔드포인트 smoke (Do phase 종료 시)
- [ ] frontend `useReviewAnalysis` 훅 회귀 (수동)
- [ ] 코어 모듈 시그니처 diff 가 0 (`git diff backend/app/core/review_*.py`)
- [ ] DB `review_analyses` 테이블 스키마 변경 없음

## 7. 아키텍처 고려사항

### 7.1 Project Level

| Level | 선택 |
|-------|:----:|
| Starter | ☐ |
| Dynamic | ✅ (현 프로젝트 분류) |
| Enterprise | ☐ |

### 7.2 핵심 아키텍처 결정 (Plan에서 확정 vs Design으로 위임)

| 결정 | 옵션 | 본 Plan에서 | 비고 |
|------|------|------------|------|
| MCP 프레임워크 | FastMCP / 공식 MCP SDK / 자체 구현 | **FastMCP 확정** | 사용자 결정 |
| 호스팅 형태 | A. stdio / B. standalone HTTP / C. FastAPI mount | **Design로 위임** (옵션 C 우선 검토 — D-2 제약) | Design Checkpoint 3 |
| Transport | stdio / sse / streamable-http | 호스팅에 종속 → Design로 위임 | |
| Tool 입자도 | 1:1 / High-level / Hybrid | **1:1 low-level 확정** | D-1 |
| 인증 모델 | JWT 재사용 / 파라미터 / 단일 테넌트 | **JWT 재사용 확정 (mount 전제)** | D-2 |
| Progress 매핑 | progress notification / 동기 / 분리 | **progress notification 확정** | D-3 |
| PDF 반환 | resource / base64 / file path | Design로 위임 | FastMCP API 확인 후 |

### 7.3 폴더 구조 (제안)

```text
backend/app/
├── api/
│   └── review_analysis.py          (기존, 헬퍼 추출만)
├── core/
│   ├── review_rag.py               (불변)
│   ├── review_analyzer.py          (불변)
│   ├── review_report.py            (불변)
│   ├── trend_detector.py           (불변)
│   └── review_helpers.py           (신규, _stratified_sample 등 추출 — 옵션)
├── mcp/                             (신규)
│   ├── __init__.py
│   ├── review_mcp_server.py         (FastMCP 서버 + tool 7~10개)
│   ├── tools.py                     (tool 함수들 — 얇은 어댑터)
│   ├── schemas.py                   (입력/출력 Pydantic 스키마)
│   └── auth.py                      (JWT → seller_id 어댑터)
└── main.py                          (옵션 C: app.mount("/mcp", ...))
```

## 8. Convention Prerequisites

### 8.1 기존 컨벤션 점검

- [x] `CLAUDE.md` 존재 (확인 필요)
- [x] FastAPI + Pydantic 패턴 확립
- [x] async/await + AsyncSession 패턴 확립
- [x] JWT 인증 미들웨어 확립 (`core/deps.py:get_current_user`)
- [ ] MCP 관련 컨벤션 신규 (본 사이클에서 정의)

### 8.2 새로 정의할 컨벤션

| Category | 정의 | 우선순위 |
|----------|------|---------|
| MCP tool 명명 | `verb_object` (예: `analyze_reviews`, `search_reviews`) | High |
| Tool 모듈 위치 | `backend/app/mcp/tools.py` 또는 모듈 분할 | High |
| Tool 입력/출력 스키마 | 기존 `schemas/review_analysis.py` Pydantic 모델 재사용/import | High |
| 오류 응답 | 기존 FastAPI HTTPException 매핑 → MCP 표준 오류 (Design에서 확정) | High |
| Progress 단계 | `analyze`: 5%(시작) → 5~95% (배치별) → 100%(완료) — 기존 SSE 동일 | Medium |
| 로깅 | 기존 `logging.getLogger(__name__)` 패턴 — `app.mcp.*` namespace | Medium |

### 8.3 환경변수

| 변수 | 용도 | Scope | 신규 |
|------|------|-------|:----:|
| `MCP_SERVER_ENABLED` | MCP 서버 mount 여부 (옵션 C) | Server | ☐ (Design 확정 후) |
| `MCP_SERVER_PORT` | 별 포트 (옵션 B) | Server | ☐ (옵션 B 시) |
| `MCP_REQUIRE_AUTH` | 인증 필수 여부 (개발 편의 vs 프로덕션) | Server | ☐ |

기존 `LITELLM_URL`, `LITELLM_API_KEY`, `EMBED_MODEL`, `EMBED_DIM`, `LLM_*` 등은 **그대로 사용**.

## 9. Next Steps (Phase 전환)

1. [ ] **Design Phase** — `/pdca design iot-review-mcp`
   - 호스팅 형태 3안 비교 + Checkpoint 3 결정
   - tool 7~10개의 정확한 입력/출력 스키마 정의
   - JWT 어댑터 구현 패턴 확정 (mount vs middleware)
   - PDF 반환 방식 확정 (resource / base64)
   - FastMCP 버전 핀(version pin) 결정
   - Session Guide 생성 (Module Map)
2. [ ] **Spike (Design 직전 또는 초반)** — FastMCP 빈 tool 1개로 mount PoC
3. [ ] **Do Phase** — `/pdca do iot-review-mcp [--scope ...]`
4. [ ] **Check Phase** — `/pdca analyze iot-review-mcp` (gap-detector + AC-1~10 검증)
5. [ ] **Report → Archive**

## 10. 참조 문서

- `docs/archive/2026-04/review-analysis-automation/review-analysis-automation.design.md` — 기존 분석 모듈 설계
- `docs/archive/2026-04/farmos_review_analysis/farmos_review_analysis.design.md` — 초기 설계
- `docs/iot-review-analysis-implementation.md` — 운영 코드 동기화 문서 (2026-04-23)
- `backend/app/api/review_analysis.py` — 기존 라우터
- `backend/app/core/review_rag.py`, `review_analyzer.py`, `review_report.py`, `trend_detector.py` — 코어 모듈
- FastMCP 공식 문서 (Design 단계에서 버전별 mount API 확인)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-01 | Initial draft (Plan checkpoint 1+2 통과, FastMCP 확정, hosting 보류) | clover0309 |

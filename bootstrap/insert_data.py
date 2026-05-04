"""Phase 2 진입점: 더미 데이터 INSERT (가산형, 멱등).

NodeJS 자동화 스크립트가 인자 없이 호출한다:
    python bootstrap/insert_data.py

동작:
1. FarmOS backend venv 로 subprocess 분기 →
   `seed_farmos_users()` + `seed_ncpms()` + `seed_ai_agent()`.
2. ShoppingMall backend venv 로 subprocess 분기 →
   `seed_shoppingmall()` + `seed_shoppingmall_reviews()`.
3. ShoppingMall backend venv 로 subprocess 분기 →
   `migrate_json_to_faq_v2.py` (FaqCategory + FaqDoc DB 적재, 멱등).
4. ShoppingMall backend venv 로 subprocess 분기 →
   `ai/seed_rag.py --from-db` (ChromaDB + BM25 인덱스 빌드, DB 기반).

`bootstrap/create_tables.py` 와 같은 이유로 두 backend 를 subprocess 로 분리한다.

이 모듈은 어떤 파괴적 동작(DROP/TRUNCATE/DELETE/ALTER)도 수행하지 않는다.
모든 INSERT 는 ON CONFLICT DO NOTHING/UPDATE 또는 row 수 가드로 멱등 보장.
(단, seed_rag.py 는 ChromaDB 디렉터리를 초기화한 뒤 재적재한다.)

농약 RAG 데이터(rag_pesticide_*)는 자동화에서 제외 — JSON raw 가 git 미포함.
필요 시 `bootstrap/Old_BootStrapBackup/pesticide.py` 풀 ETL 을 수동 실행.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _venv_utils import _venv_python

ROOT = Path(__file__).resolve().parents[1]
FARMOS_BACKEND = ROOT / "backend"
SHOP_BACKEND = ROOT / "shopping_mall" / "backend"


def _run_python_code(label: str, python_exe: str, cwd: Path, code: str) -> None:
    print(f"[insert_data] {label} 시작 (python={python_exe})")
    env = os.environ.copy()
    pythonpath_parts = [str(ROOT)]
    if existing := env.get("PYTHONPATH"):
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    result = subprocess.run([python_exe, "-c", code], cwd=str(cwd), env=env)
    if result.returncode != 0:
        print(
            f"[insert_data] {label} 실패 (exit={result.returncode})",
            file=sys.stderr,
        )
        raise SystemExit(result.returncode)
    print(f"[insert_data] {label} 완료")


def _run_python_script(
    label: str,
    python_exe: str,
    cwd: Path,
    script: str,
    args: list[str] | None = None,
) -> None:
    """스크립트 파일을 직접 실행한다.

    ``__file__`` 기반 sys.path 보정이 있는 스크립트는 ``-c code`` 방식으로
    실행하면 ``__file__`` 이 정의되지 않아 경로 보정에 실패한다.
    이 경우 스크립트 경로를 직접 넘겨 실행한다.
    """
    print(f"[insert_data] {label} 시작 (python={python_exe})")
    env = os.environ.copy()
    pythonpath_parts = [str(ROOT)]
    if existing := env.get("PYTHONPATH"):
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    result = subprocess.run([python_exe, script, *(args or [])], cwd=str(cwd), env=env)
    if result.returncode != 0:
        print(
            f"[insert_data] {label} 실패 (exit={result.returncode})",
            file=sys.stderr,
        )
        raise SystemExit(result.returncode)
    print(f"[insert_data] {label} 완료")


FARMOS_SEED_CODE = """
import asyncio
from bootstrap.farmos_seed import seed_farmos_users
from bootstrap.ncpms_seed import seed_ncpms
from bootstrap.seed_ai_agent import seed_ai_agent
from app.core.database import close_db


async def _run() -> None:
    inserted_users = await seed_farmos_users()
    print(f"[insert_data] seed_farmos_users 추가 row: {inserted_users}")

    upserted_ncpms = await seed_ncpms()
    print(f"[insert_data] seed_ncpms upsert row: {upserted_ncpms}")

    inserted_decisions, summary_bumps = await seed_ai_agent()
    print(
        f"[insert_data] seed_ai_agent 추가: decisions={inserted_decisions}, "
        f"summary_bumps={summary_bumps}"
    )

    await close_db()


asyncio.run(_run())
"""

SHOP_SEED_CODE = """
from bootstrap.shoppingmall_seed import seed_shoppingmall
from bootstrap.shoppingmall_review_seed import seed_shoppingmall_reviews

shop_seeded = seed_shoppingmall()
print(f"[insert_data] seed_shoppingmall: {'시드 수행' if shop_seeded else '스킵'}")

added_reviews = seed_shoppingmall_reviews()
print(f"[insert_data] seed_shoppingmall_reviews 추가 row: {added_reviews}")
"""

def main() -> int:
    print("[insert_data] Phase 2 시작 (더미 데이터 INSERT, 멱등)")

    _run_python_code(
        "FarmOS 사용자/NCPMS/AI Agent 시드",
        _venv_python(FARMOS_BACKEND),
        FARMOS_BACKEND,
        FARMOS_SEED_CODE,
    )

    _run_python_code(
        "ShoppingMall 코어/백오피스/리뷰 시드",
        _venv_python(SHOP_BACKEND),
        SHOP_BACKEND,
        SHOP_SEED_CODE,
    )

    _run_python_script(
        "ShoppingMall FAQ DB 시딩 (FaqCategory + FaqDoc)",
        _venv_python(SHOP_BACKEND),
        SHOP_BACKEND,
        "scripts/migrate_json_to_faq_v2.py",
    )

    _run_python_script(
        "ShoppingMall RAG 전체 시딩 (FAQ + 정책 문서 → ChromaDB + BM25)",
        _venv_python(SHOP_BACKEND),
        SHOP_BACKEND,
        "ai/seed_rag.py",
        ["--from-db"],
    )

    print("[insert_data] Phase 2 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

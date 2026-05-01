"""모델 메타 + 시드 기대값을 JSON 으로 export 한다.

NodeJS 자동화(`automation/`) 가 DB 검증 시 단일 진실 소스로 사용한다.

NodeJS 호출 예:
    python bootstrap/export_meta.py > automation/meta.json

출력 JSON 구조:
{
  "farmos": {
    "tables": {
      "<table_name>": {
        "columns": [{"name": "...", "type": "...", "nullable": true, "default": "..."}],
        "primary_key": ["..."]
      }
    },
    "expected_row_counts": {"users": 2, ...},
    "post_pesticide_min_row_counts": {"rag_pesticide_products": 1, ...},
    "ai_agent_default_count": 30
  },
  "shoppingmall": {
    "tables": {...},
    "expected_row_counts": {...},
    "ready_row_counts": {...},
    "review_target_count": 1000
  }
}

`bootstrap/create_tables.py` 와 같은 이유로 두 backend 를 subprocess 로 분리한다.
각 venv 의 Python 이 자기 모델을 import 해서 SQLAlchemy 메타데이터를 직접 export 하므로
정적 파싱 대비 안정적이다.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

from _venv_utils import _venv_python

ROOT = Path(__file__).resolve().parents[1]
FARMOS_BACKEND = ROOT / "backend"
SHOP_BACKEND = ROOT / "shopping_mall" / "backend"

# [TEMP DIAG] Web_Starter vs CLI 환경 차이 진단용. 정상 확인 후 이 블록 제거.
_DIAG_LOG = ROOT / "logs" / "bootstrap_diagnose.log"


def _diag_append(label: str, extras: dict[str, str] | None = None) -> None:
    try:
        _DIAG_LOG.parent.mkdir(parents=True, exist_ok=True)
        keys = ("DATABASE_URL", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONIOENCODING", "PYTHONUTF8")
        lines = [
            f"\n=== {datetime.datetime.now().isoformat()} {label} ===",
            f"sys.executable = {sys.executable!r}",
            f"cwd            = {os.getcwd()!r}",
            f"argv           = {sys.argv!r}",
            f".env (cwd)     = {os.path.exists('.env')!r}",
            f"backend/.env   = {(ROOT / 'backend' / '.env').exists()!r}",
        ]
        for k in keys:
            lines.append(f"env[{k}] = {os.environ.get(k, '<unset>')!r}")
        path = os.environ.get("PATH", "")
        lines.append(f"PATH (first 400) = {path[:400]!r}")
        if extras:
            for k, v in extras.items():
                lines.append(f"{k} = {v}")
        with open(_DIAG_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as exc:  # 진단 코드는 절대 본 흐름을 깨면 안 됨.
        sys.stderr.write(f"[diag] write failed: {exc}\n")


def _run_meta_extractor(label: str, python_exe: str, cwd: Path, code: str) -> dict:
    env = os.environ.copy()
    pythonpath_parts = [str(ROOT)]
    if existing := env.get("PYTHONPATH"):
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    # 자식 Python 의 stdout/stderr 를 UTF-8 로 강제 (Windows 콘솔 cp949 회피)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # [TEMP DIAG] inner subprocess 가 진단 로그를 append 할 경로 전달.
    env["FARMOS_DIAG_LOG"] = str(_DIAG_LOG)
    _diag_append(
        f"OUTER → spawn inner [{label}]",
        extras={
            "python_exe": repr(python_exe),
            "child_cwd": repr(str(cwd)),
            "child_env[DATABASE_URL]": repr(env.get("DATABASE_URL", "<unset>")),
            "child_env[VIRTUAL_ENV]": repr(env.get("VIRTUAL_ENV", "<unset>")),
            "child_env[PYTHONHOME]": repr(env.get("PYTHONHOME", "<unset>")),
        },
    )

    result = subprocess.run(
        [python_exe, "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"[export_meta] {label} 실패 (exit={result.returncode})\n"
            f"--- stderr ---\n{result.stderr}\n"
        )
        raise SystemExit(result.returncode)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(
            f"[export_meta] {label} JSON 파싱 실패: {exc}\n"
            f"--- stdout ---\n{result.stdout[:2000]}\n"
        )
        raise SystemExit(1) from exc


# 두 추출기 모두 stdout 에 JSON 한 줄을 출력한다(다른 print 금지).
# Base.metadata.tables 를 순회하여 컬럼/타입/nullable/default 를 dict 로 정규화.
FARMOS_META_CODE = r"""
import json
import os
import sys
import datetime
import traceback

# [TEMP DIAG] Settings 로드 전 inner 환경 스냅샷.
_DIAG = os.environ.get("FARMOS_DIAG_LOG")
def _diag_w(msg):
    if not _DIAG:
        return
    try:
        with open(_DIAG, "a", encoding="utf-8") as _f:
            _f.write(msg)
    except Exception:
        pass

_diag_w(f"\n=== {datetime.datetime.now().isoformat()} INNER farmos meta ===\n")
_diag_w(f"sys.executable = {sys.executable!r}\n")
_diag_w(f"cwd            = {os.getcwd()!r}\n")
_diag_w(f".env exists    = {os.path.exists('.env')!r}\n")
for _k in ("DATABASE_URL", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
    _diag_w(f"env[{_k}] = {os.environ.get(_k, '<unset>')!r}\n")

try:
    from app.core.config import settings as _s
    _diag_w(f"settings.DATABASE_URL = {_s.DATABASE_URL!r}\n")
except Exception:
    _diag_w("settings load FAILED:\n" + traceback.format_exc())
    raise

import bootstrap.farmos_seed  # FarmOS 모델 등록
from bootstrap.farmos_seed import EXPECTED_ROW_COUNTS, POST_PESTICIDE_MIN_ROW_COUNTS
from bootstrap.seed_ai_agent import DEFAULT_DECISION_COUNT
from bootstrap.pesticide import Base as PesticideBase
from app.core.database import Base as FarmosBase


def _serialize_metadata(metadata):
    out = {}
    for table_name, table in metadata.tables.items():
        cols = []
        for col in table.columns:
            default_value = None
            if col.default is not None:
                # ORM-side default: callable/scalar/Sequence 등 다양 — repr 로 표시.
                default_value = repr(getattr(col.default, "arg", col.default))
            elif col.server_default is not None:
                default_value = str(col.server_default.arg) if hasattr(col.server_default, "arg") else str(col.server_default)
            cols.append({
                "name": col.name,
                "type": str(col.type),
                "nullable": bool(col.nullable),
                "default": default_value,
            })
        pk = [c.name for c in table.primary_key.columns]
        out[table_name] = {"columns": cols, "primary_key": pk}
    return out


payload = {
    "tables": {
        **_serialize_metadata(FarmosBase.metadata),
        **_serialize_metadata(PesticideBase.metadata),
    },
    "expected_row_counts": dict(EXPECTED_ROW_COUNTS),
    "post_pesticide_min_row_counts": dict(POST_PESTICIDE_MIN_ROW_COUNTS),
    "ai_agent_default_count": int(DEFAULT_DECISION_COUNT),
}

# stdout 으로만 JSON, 그 외 출력 금지.
sys.stdout.write(json.dumps(payload, ensure_ascii=False))
"""


SHOP_META_CODE = r"""
import json
import os
import sys
import datetime
import traceback

# [TEMP DIAG] Settings 로드 전 inner 환경 스냅샷.
_DIAG = os.environ.get("FARMOS_DIAG_LOG")
def _diag_w(msg):
    if not _DIAG:
        return
    try:
        with open(_DIAG, "a", encoding="utf-8") as _f:
            _f.write(msg)
    except Exception:
        pass

_diag_w(f"\n=== {datetime.datetime.now().isoformat()} INNER shop meta ===\n")
_diag_w(f"sys.executable = {sys.executable!r}\n")
_diag_w(f"cwd            = {os.getcwd()!r}\n")
_diag_w(f".env exists    = {os.path.exists('.env')!r}\n")
for _k in ("DATABASE_URL", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
    _diag_w(f"env[{_k}] = {os.environ.get(_k, '<unset>')!r}\n")

import bootstrap.shoppingmall_seed  # ShoppingMall 모델 등록
from bootstrap.shoppingmall_seed import (
    EXPECTED_ROW_COUNTS,
    READY_ROW_COUNTS,
    SHOP_TABLES,
)
from bootstrap.shoppingmall_review_seed import REVIEW_TARGET_COUNT
from app.database import Base as ShopBase


def _serialize_metadata(metadata):
    out = {}
    for table_name, table in metadata.tables.items():
        cols = []
        for col in table.columns:
            default_value = None
            if col.default is not None:
                default_value = repr(getattr(col.default, "arg", col.default))
            elif col.server_default is not None:
                default_value = str(col.server_default.arg) if hasattr(col.server_default, "arg") else str(col.server_default)
            cols.append({
                "name": col.name,
                "type": str(col.type),
                "nullable": bool(col.nullable),
                "default": default_value,
            })
        pk = [c.name for c in table.primary_key.columns]
        out[table_name] = {"columns": cols, "primary_key": pk}
    return out


payload = {
    "tables": _serialize_metadata(ShopBase.metadata),
    "expected_row_counts": dict(EXPECTED_ROW_COUNTS),
    "ready_row_counts": dict(READY_ROW_COUNTS),
    "shop_tables_order": list(SHOP_TABLES),
    "review_target_count": int(REVIEW_TARGET_COUNT),
}

sys.stdout.write(json.dumps(payload, ensure_ascii=False))
"""


def main() -> int:
    # Windows 콘솔의 cp949 기본 인코딩이 JSON 출력 시 한글 깨짐을 유발하므로 UTF-8 강제.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # [TEMP DIAG] 외부 진입점(export_meta.py 자체) 환경 스냅샷.
    _diag_append("OUTER export_meta.py main() entry")

    farmos_meta = _run_meta_extractor(
        "FarmOS 메타 추출",
        _venv_python(FARMOS_BACKEND),
        FARMOS_BACKEND,
        FARMOS_META_CODE,
    )
    shop_meta = _run_meta_extractor(
        "ShoppingMall 메타 추출",
        _venv_python(SHOP_BACKEND),
        SHOP_BACKEND,
        SHOP_META_CODE,
    )

    payload = {
        "farmos": farmos_meta,
        "shoppingmall": shop_meta,
    }

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from _bootstrap_common import (  # type: ignore[import-not-found]
    error,
    info,
    set_log_prefix,
)
from _venv_utils import _venv_python
from dotenv import dotenv_values, load_dotenv
from pesticide import Base, Crop, Product, ProductApplication, RagDocument, Target
from sqlalchemy import (
    and_,
    create_engine,
    inspect,
    or_,
    select,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

SERVICE_ID = "I1910"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_INPUT_DIR = BACKEND_DIR / "data" / "pesticide" / "json_raw"
DEFAULT_BACKEND_ENV_PATH = PROJECT_ROOT / "backend" / ".env"
DEFAULT_ENV_EXAMPLE_PATH = (
    PROJECT_ROOT / "tools" / "pesticide-api-crawler" / ".env.example"
)
DEFAULT_ENV_VALUES = (
    dotenv_values(DEFAULT_ENV_EXAMPLE_PATH) if DEFAULT_ENV_EXAMPLE_PATH.exists() else {}
)
DEFAULT_POSTGRES_HOST = str(DEFAULT_ENV_VALUES.get("POSTGRES_HOST") or "localhost")
DEFAULT_POSTGRES_PORT = int(DEFAULT_ENV_VALUES.get("POSTGRES_PORT") or 5432)
DEFAULT_POSTGRES_USER = str(DEFAULT_ENV_VALUES.get("POSTGRES_USER") or "postgres")
DEFAULT_POSTGRES_PASSWORD = str(DEFAULT_ENV_VALUES.get("POSTGRES_PASSWORD") or "root")
DEFAULT_POSTGRES_DB = str(DEFAULT_ENV_VALUES.get("POSTGRES_DB") or "farmos")
POSTGRES_PING_TIMEOUT_SECONDS = 3.0
POSTGRES_RETRY_INTERVAL_SECONDS = 5.0
NULL_LIKE_VALUES = {"", "-", "--", "N/A", "None", None}
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
WHITESPACE_RE = re.compile(r"\s+")
PAREN_CONTENT_RE = re.compile(r"\([^)]*\)")
RESULT_FILE_PATTERN = re.compile(r"^(?P<start>\d+)-(?P<end>\d+)\.json(?:\.gz)?$")
USE_COUNT_RE = re.compile(r"(\d+)")
DILUTION_RE = re.compile(r"(\d+)")
TABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
LEGACY_TABLE_NAMES = [
    "crops",
    "pesticide_products",
    "product_applications",
    "products",
    "rag_documents",
    "targets",
]
LOG_PREFIX = "PESTICIDE"
KST = ZoneInfo("Asia/Seoul")
TARGET_PEST_NAMES: tuple[str, ...] = (
    "검거세미밤나방",
    "꽃노랑총채벌레",
    "담배가루이",
    "담배거세미나방",
    "담배나방",
    "도둑나방",
    "먹노린재",
    "목화바둑명나방",
    "무잎벌",
    "배추좀나방",
    "배추흰나비",
    "벼룩잎벌레",
    "복숭아혹진딧물",
    "홍비단노린재",
    "썩덩나무노린재",
    "열대거세미나방",
    "큰28점박이무당벌레",
    "큰이십팔점박이무당벌레",
    "톱다리개미허리노린재",
    "파밤나방",
)
TARGET_PEST_SYNONYM_TO_CANONICAL: dict[str, str] = {
    "큰이십팔점박이무당벌레": "큰28점박이무당벌레",
}

PARSER = argparse.ArgumentParser(
    description="농약 원본 JSON(json_raw)을 정제하여 PostgreSQL RAG 테이블에 적재합니다."
)
PARSER.add_argument(
    "--append",
    action="store_true",
    help="기존 테이블을 유지하고 누적 적재합니다. 기본값은 관리 대상 테이블 재생성입니다.",
)
PARSER.add_argument(
    "--backend-env-path",
    type=Path,
    default=DEFAULT_BACKEND_ENV_PATH,
    help="PostgreSQL 접속 정보 로딩에 사용할 backend/.env 경로",
)
PARSER.add_argument(
    "--commit-every-files",
    type=int,
    default=0,
    help="파일 기준 중간 commit 주기. 0 이하면 마지막에 한 번만 commit 합니다.",
)
PARSER.add_argument(
    "--db-url",
    help="SQLAlchemy DB URL 직접 지정. 지정하면 개별 DB 옵션보다 우선합니다.",
)
PARSER.add_argument(
    "--flush-every",
    type=int,
    default=2000,
    help="row 기준 중간 flush/expunge 주기. 0 이하면 비활성화합니다.",
)
PARSER.add_argument("--glob", default="*.json.gz", help="입력 파일 검색 패턴")
PARSER.add_argument(
    "--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="API raw JSON 디렉터리"
)
PARSER.add_argument(
    "--log-every",
    type=int,
    default=5000,
    help="처리한 raw row 수 기준 중간 로그 주기. 0 이하면 파일 단위 로그만 출력합니다.",
)
PARSER.add_argument("--postgres-db", default=None, help="PostgreSQL DB 이름")
PARSER.add_argument("--postgres-host", default=None, help="PostgreSQL 호스트")
PARSER.add_argument(
    "--postgres-max-retries",
    type=int,
    default=10,
    help="PostgreSQL 서버 연결 재시도 최대 횟수(기본 10회)",
)
PARSER.add_argument(
    "--postgres-password",
    default=None,
    help="PostgreSQL 비밀번호",
)
PARSER.add_argument("--postgres-port", type=int, default=None, help="PostgreSQL 포트")
PARSER.add_argument("--postgres-user", default=None, help="PostgreSQL 사용자명")


@dataclass
class Stats:
    applications_written: int = 0
    crops_written: int = 0
    files_seen: int = 0
    products_written: int = 0
    rag_documents_written: int = 0
    rows_seen: int = 0
    targets_written: int = 0


AppKey = tuple[int, str, str, str]
ProductIdentityKey = tuple[str, str]


@dataclass
class UpsertCaches:
    applications: dict[AppKey, ProductApplication]
    documents: dict[AppKey, RagDocument]
    products_by_identity: dict[ProductIdentityKey, Product]
    products: dict[int, Product]


def parse_args() -> argparse.Namespace:
    return PARSER.parse_args()


def ensure_backend_uv_python() -> None:
    """UV 가상환경이 아니면 backend/.venv python으로 현재 스크립트를 재실행한다."""
    if os.environ.get("FARMOS_PESTICIDE_SEED_REEXEC") == "1":
        return

    backend_python = Path(_venv_python(BACKEND_DIR)).resolve()
    current_python = Path(sys.executable).resolve()
    if current_python == backend_python:
        return

    env = os.environ.copy()
    env["FARMOS_PESTICIDE_SEED_REEXEC"] = "1"
    cmd = [str(backend_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=False)
    raise SystemExit(result.returncode)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    safe_message = message.replace("\r", " ").replace("\n", " ")
    info(f"[{datetime.now(KST).strftime('%H:%M:%S KST')}] {safe_message}")


def clean_text(value: Any) -> str | None:
    if value in NULL_LIKE_VALUES:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = WHITESPACE_RE.sub(" ", value.strip())
    return normalized or None


def normalize_keyword(value: str) -> str:
    text = clean_text(value) or ""
    text = PAREN_CONTENT_RE.sub("", text)
    text = re.sub(r"[^\w가-힣]+", "", text)
    return text.lower()


TARGET_PEST_NORMALIZED_SET: set[str] = {
    normalize_keyword(target_name) for target_name in TARGET_PEST_NAMES
}
TARGET_PEST_CANONICAL_BY_NORMALIZED: dict[str, str] = {
    normalize_keyword(target_name): target_name for target_name in TARGET_PEST_NAMES
}
TARGET_PEST_CANONICAL_BY_NORMALIZED.update(
    {
        normalize_keyword(synonym_name): canonical_name
        for synonym_name, canonical_name in TARGET_PEST_SYNONYM_TO_CANONICAL.items()
    }
)


def is_selected_target_name(target_name: str) -> bool:
    return normalize_keyword(target_name) in TARGET_PEST_NORMALIZED_SET


def canonicalize_target_name(target_name: str) -> str:
    normalized = normalize_keyword(target_name)
    canonical = TARGET_PEST_CANONICAL_BY_NORMALIZED.get(normalized)
    if canonical:
        return canonical
    return target_name


def split_top_level(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    separators = {",", "/", "|", "+", ";", "\n", "\r"}

    for char in text:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1

        if depth == 0 and char in separators:
            piece = clean_text("".join(current))
            if piece:
                parts.append(piece)
            current = []
            continue
        current.append(char)

    tail = clean_text("".join(current))
    if tail:
        parts.append(tail)

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in seen:
            seen.add(part)
            deduped.append(part)
    return deduped


def parse_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    match = DATE_RE.fullmatch(text)
    if not match:
        return None
    yyyy, mm, dd = match.groups()
    try:
        return date(int(yyyy), int(mm), int(dd))
    except ValueError:
        return None


def parse_int_from_text(value: Any, pattern: re.Pattern[str]) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    text_no_comma = text.replace(",", "")
    match = pattern.search(text_no_comma)
    if not match:
        return None
    try:
        return int(float(match.group(1)))
    except ValueError:
        return None


def normalize_registration_status(value: Any) -> bool | None:
    text = clean_text(value)
    if not text:
        return None
    normalized = text.replace(" ", "")
    if "등록된품목" in normalized or normalized in {
        "등록",
        "유효",
        "true",
        "True",
        "1",
    }:
        return True
    if "등록되지않은품목" in normalized or normalized in {
        "미등록",
        "말소",
        "false",
        "False",
        "0",
    }:
        return False
    return None


def infer_target_kind(target_name: str) -> str:
    normalized = target_name.replace(" ", "")
    if "잡초" in normalized:
        return "weed"
    if (
        normalized.endswith("병")
        or "도열병" in normalized
        or "역병" in normalized
        or "탄저병" in normalized
    ):
        return "disease"
    return "pest"


def collect_source_files(input_dir: Path, glob_pattern: str) -> list[Path]:
    return sorted(path for path in input_dir.rglob(glob_pattern) if path.is_file())


def load_rows(source_file: Path) -> list[dict[str, Any]]:
    if source_file.suffix == ".gz":
        with gzip.open(source_file, "rt", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(source_file.read_text(encoding="utf-8"))
    dataset = payload.get(SERVICE_ID)
    if not isinstance(dataset, dict):
        raise RuntimeError(f"{source_file} 에 `{SERVICE_ID}` 객체가 없습니다.")
    rows = dataset.get("row", [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RuntimeError(f"{source_file} 의 row 필드가 배열이 아닙니다.")
    return [row for row in rows if isinstance(row, dict)]


def parse_range_from_result_path(path: Path) -> tuple[int, int]:
    match = RESULT_FILE_PATTERN.fullmatch(path.name)
    if not match:
        raise RuntimeError(
            f"파일명 `{path.name}` 이 `00000-00999.json` 또는 `00000-00999.json.gz` 형식이 아닙니다."
        )
    return int(match.group("start")), int(match.group("end"))


def make_product_id(start_index: int, row_index: int) -> int:
    return start_index + row_index + 1


def hash_raw_row(raw_row: dict[str, Any]) -> str:
    payload = json.dumps(
        raw_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_search_text(
    product: Product, crop_name: str, target_name: str, application: dict[str, Any]
) -> str:
    values = [
        crop_name,
        target_name,
        product.ingredient_or_formulation_name,
        product.pesticide_name_eng,
        product.brand_name,
        product.corporation_name,
        product.usage_purpose_name,
        product.formulation_name,
        application.get("application_method"),
        application.get("application_timing"),
        application.get("dilution_text"),
        application.get("max_use_count_text"),
    ]
    return " | ".join(value for value in values if isinstance(value, str) and value)


def build_render_text(
    product: Product,
    crop_name: str,
    target_name: str,
    target_kind: str,
    application: dict[str, Any],
) -> str:
    parts = [
        f"작물 {crop_name}",
        f"대상 {target_name}",
        f"대상종류 {target_kind}",
    ]
    if product.ingredient_or_formulation_name:
        parts.append(f"성분/제형명 {product.ingredient_or_formulation_name}")
    if product.brand_name:
        parts.append(f"상표명 {product.brand_name}")
    if product.corporation_name:
        parts.append(f"법인명 {product.corporation_name}")
    if product.usage_purpose_name:
        parts.append(f"용도 {product.usage_purpose_name}")
    if product.formulation_name:
        parts.append(f"제제형태 {product.formulation_name}")
    if application.get("application_method"):
        parts.append(f"농약사용방법 {application['application_method']}")
    if application.get("application_timing"):
        parts.append(f"사용적기 {application['application_timing']}")
    if application.get("dilution_text"):
        parts.append(f"희석배수 {application['dilution_text']}")
    if application.get("use_quantity"):
        quantity_text = application["use_quantity"]
        if application.get("use_unit"):
            quantity_text = f"{quantity_text} {application['use_unit']}"
        parts.append(f"사용수량 {quantity_text}")
    if application.get("max_use_count_text"):
        parts.append(f"사용횟수 {application['max_use_count_text']}")
    if application.get("human_livestock_toxicity"):
        parts.append(f"사람가축독성 {application['human_livestock_toxicity']}")
    if application.get("ecotoxicity"):
        parts.append(f"생태독성 {application['ecotoxicity']}")
    if product.is_registered is not None:
        parts.append(f"등록여부 {product.is_registered}")
    if product.registration_valid_until:
        parts.append(f"등록유효일자 {product.registration_valid_until.isoformat()}")
    return ". ".join(parts)


def build_product(
    raw_row: dict[str, Any],
    source_file: Path,
    start_index: int,
    row_index: int,
    now: str,
) -> Product:
    return Product(
        product_id=make_product_id(start_index, row_index),
        product_code=clean_text(raw_row.get("AGCHM_PRDLST_NO")),
        registration_number=clean_text(raw_row.get("PRDLST_REG_NO")),
        ingredient_or_formulation_name=clean_text(raw_row.get("PRDLST_KOR_NM")),
        pesticide_name_eng=clean_text(raw_row.get("PRDLST_ENG_NM")),
        brand_name=clean_text(raw_row.get("BRND_NM")),
        corporation_name=clean_text(raw_row.get("CPR_NM")),
        pesticide_category_name=clean_text(raw_row.get("AGCHM_DVS_NM")),
        usage_purpose_name=clean_text(raw_row.get("PRPOS_DVS_CD_NM")),
        formulation_name=clean_text(raw_row.get("MDC_SHAP_NM")),
        is_registered=normalize_registration_status(raw_row.get("REG_YN_NM")),
        registration_date=parse_date(raw_row.get("PRDLST_REG_DT")),
        registration_valid_until=parse_date(raw_row.get("PRDLST_REG_VALD_DT")),
        registration_standard=clean_text(raw_row.get("PRDLST_REG_STND")),
        manufacturer_importer_type=clean_text(raw_row.get("MNF_INCM_DVS_NM")),
        representative_name=clean_text(raw_row.get("PRSDNT_NM")),
        business_registration_number=clean_text(raw_row.get("BUSS_REG_NO")),
        business_registration_event_name=clean_text(raw_row.get("BUSS_REG_EVNT_NM")),
        address=clean_text(raw_row.get("ADDR")),
        raw_row_hash=hash_raw_row(raw_row),
        source_file_name=source_file.name,
        source_row_index=row_index + 1,
        created_at=now,
        updated_at=now,
    )


def build_application_payload(
    raw_row: dict[str, Any], source_file: Path, row_index: int
) -> dict[str, Any]:
    return {
        "application_method": clean_text(raw_row.get("AGCHM_USE_MTHD")),
        "application_timing": clean_text(raw_row.get("USE_PPRTM")),
        "dilution_text": clean_text(raw_row.get("DILU_DRNG")),
        "dilution_factor": parse_int_from_text(raw_row.get("DILU_DRNG"), DILUTION_RE),
        "use_quantity": clean_text(raw_row.get("USE_QTY")),
        "use_unit": clean_text(raw_row.get("USE_UNIT")),
        "max_use_count_text": clean_text(raw_row.get("USE_TMNO")),
        "max_use_count": parse_int_from_text(raw_row.get("USE_TMNO"), USE_COUNT_RE),
        "test_drug_name": clean_text(raw_row.get("TEST_DRGS_NM")),
        "human_livestock_toxicity": clean_text(raw_row.get("PERSN_LVSTCK_TOXCTY")),
        "ecotoxicity": clean_text(raw_row.get("ECLGY_TOXCTY")),
        "source_file_name": source_file.name,
        "source_row_index": row_index + 1,
    }


def make_app_key(product: Product, crop: Crop, target: Target) -> AppKey:
    return (
        product.product_id,
        crop.crop_name_normalized,
        target.target_name_normalized,
        target.target_kind,
    )


def product_identity_keys(product: Product) -> list[ProductIdentityKey]:
    keys: list[ProductIdentityKey] = []
    if product.product_code and product.registration_number:
        keys.append(
            ("code+reg", f"{product.product_code}::{product.registration_number}")
        )
    if product.product_code:
        keys.append(("code", product.product_code))
    if product.registration_number:
        keys.append(("reg", product.registration_number))
    if product.raw_row_hash:
        keys.append(("raw_hash", product.raw_row_hash))
    return keys


def find_existing_product_by_identity(
    session: Session, candidate: Product
) -> Product | None:
    conditions = []
    if candidate.product_code and candidate.registration_number:
        conditions.append(
            and_(
                Product.product_code == candidate.product_code,
                Product.registration_number == candidate.registration_number,
            )
        )
    if candidate.product_code:
        conditions.append(Product.product_code == candidate.product_code)
    if candidate.registration_number:
        conditions.append(Product.registration_number == candidate.registration_number)
    conditions.append(Product.raw_row_hash == candidate.raw_row_hash)

    rows = session.scalars(select(Product).where(or_(*conditions))).all()
    if not rows:
        return None

    if candidate.product_code and candidate.registration_number:
        for row in rows:
            if (
                row.product_code == candidate.product_code
                and row.registration_number == candidate.registration_number
            ):
                return row
    if candidate.product_code:
        for row in rows:
            if row.product_code == candidate.product_code:
                return row
    if candidate.registration_number:
        for row in rows:
            if row.registration_number == candidate.registration_number:
                return row
    for row in rows:
        if row.raw_row_hash == candidate.raw_row_hash:
            return row
    return None


def upsert_product(
    session: Session,
    caches: UpsertCaches,
    candidate: Product,
    now: str,
    *,
    append_mode: bool,
) -> Product:
    cached = caches.products.get(candidate.product_id)
    if cached is not None:
        existing = cached
    elif append_mode:
        existing = None
        for identity_key in product_identity_keys(candidate):
            cached_by_identity = caches.products_by_identity.get(identity_key)
            if cached_by_identity is not None:
                existing = cached_by_identity
                break
        if existing is None:
            existing = find_existing_product_by_identity(session, candidate)
        if existing is None:
            session.add(candidate)
            caches.products[candidate.product_id] = candidate
            for identity_key in product_identity_keys(candidate):
                caches.products_by_identity[identity_key] = candidate
            return candidate
    else:
        existing = None
        session.add(candidate)
        caches.products[candidate.product_id] = candidate
        for identity_key in product_identity_keys(candidate):
            caches.products_by_identity[identity_key] = candidate
        return candidate

    caches.products[candidate.product_id] = existing
    for identity_key in product_identity_keys(existing):
        caches.products_by_identity[identity_key] = existing
    for identity_key in product_identity_keys(candidate):
        caches.products_by_identity[identity_key] = existing

    existing.product_code = candidate.product_code
    existing.registration_number = candidate.registration_number
    existing.ingredient_or_formulation_name = candidate.ingredient_or_formulation_name
    existing.pesticide_name_eng = candidate.pesticide_name_eng
    existing.brand_name = candidate.brand_name
    existing.corporation_name = candidate.corporation_name
    existing.pesticide_category_name = candidate.pesticide_category_name
    existing.usage_purpose_name = candidate.usage_purpose_name
    existing.formulation_name = candidate.formulation_name
    existing.is_registered = candidate.is_registered
    existing.registration_date = candidate.registration_date
    existing.registration_valid_until = candidate.registration_valid_until
    existing.registration_standard = candidate.registration_standard
    existing.manufacturer_importer_type = candidate.manufacturer_importer_type
    existing.representative_name = candidate.representative_name
    existing.business_registration_number = candidate.business_registration_number
    existing.business_registration_event_name = (
        candidate.business_registration_event_name
    )
    existing.address = candidate.address
    existing.raw_row_hash = candidate.raw_row_hash
    existing.source_file_name = candidate.source_file_name
    existing.source_row_index = candidate.source_row_index
    existing.updated_at = now
    return existing


def upsert_application(
    session: Session,
    caches: UpsertCaches,
    *,
    product: Product,
    crop: Crop,
    target: Target,
    application_payload: dict[str, Any],
    now: str,
    append_mode: bool,
) -> ProductApplication:
    key = make_app_key(product, crop, target)
    cached = caches.applications.get(key)
    if cached is not None:
        existing = cached
    elif append_mode and crop.crop_id is not None and target.target_id is not None:
        existing = session.scalar(
            select(ProductApplication).where(
                ProductApplication.product_id == product.product_id,
                ProductApplication.crop_id == crop.crop_id,
                ProductApplication.target_id == target.target_id,
            )
        )
        if existing is not None:
            caches.applications[key] = existing
    else:
        existing = None

    if existing is None:
        application = ProductApplication(
            product=product,
            crop=crop,
            target=target,
            application_method=application_payload["application_method"],
            application_timing=application_payload["application_timing"],
            dilution_text=application_payload["dilution_text"],
            dilution_factor=application_payload["dilution_factor"],
            use_quantity=application_payload["use_quantity"],
            use_unit=application_payload["use_unit"],
            max_use_count_text=application_payload["max_use_count_text"],
            max_use_count=application_payload["max_use_count"],
            test_drug_name=application_payload["test_drug_name"],
            human_livestock_toxicity=application_payload["human_livestock_toxicity"],
            ecotoxicity=application_payload["ecotoxicity"],
            source_file_name=application_payload["source_file_name"],
            source_row_index=application_payload["source_row_index"],
            created_at=now,
            updated_at=now,
        )
        session.add(application)
        caches.applications[key] = application
        return application

    existing.application_method = application_payload["application_method"]
    existing.application_timing = application_payload["application_timing"]
    existing.dilution_text = application_payload["dilution_text"]
    existing.dilution_factor = application_payload["dilution_factor"]
    existing.use_quantity = application_payload["use_quantity"]
    existing.use_unit = application_payload["use_unit"]
    existing.max_use_count_text = application_payload["max_use_count_text"]
    existing.max_use_count = application_payload["max_use_count"]
    existing.test_drug_name = application_payload["test_drug_name"]
    existing.human_livestock_toxicity = application_payload["human_livestock_toxicity"]
    existing.ecotoxicity = application_payload["ecotoxicity"]
    existing.source_file_name = application_payload["source_file_name"]
    existing.source_row_index = application_payload["source_row_index"]
    existing.updated_at = now
    return existing


def upsert_rag_document(
    session: Session,
    caches: UpsertCaches,
    *,
    application: ProductApplication,
    product: Product,
    crop: Crop,
    target: Target,
    application_payload: dict[str, Any],
    now: str,
    append_mode: bool,
) -> RagDocument:
    key = make_app_key(product, crop, target)
    search_text = build_search_text(
        product, crop.crop_name, target.target_name, application_payload
    )
    render_text = build_render_text(
        product,
        crop.crop_name,
        target.target_name,
        target.target_kind,
        application_payload,
    )
    cached = caches.documents.get(key)
    if cached is not None:
        existing = cached
    elif append_mode and application.application_id is not None:
        existing = session.scalar(
            select(RagDocument).where(
                RagDocument.application_id == application.application_id
            )
        )
        if existing is not None:
            caches.documents[key] = existing
    else:
        existing = None

    if existing is None:
        document = RagDocument(
            application=application,
            crop_name=crop.crop_name,
            crop_name_normalized=crop.crop_name_normalized,
            target_name=target.target_name,
            target_name_normalized=target.target_name_normalized,
            target_kind=target.target_kind,
            ingredient_or_formulation_name=product.ingredient_or_formulation_name,
            pesticide_name_eng=product.pesticide_name_eng,
            brand_name=product.brand_name,
            corporation_name=product.corporation_name,
            registration_number=product.registration_number,
            product_code=product.product_code,
            usage_purpose_name=product.usage_purpose_name,
            formulation_name=product.formulation_name,
            application_method=application.application_method,
            application_timing=application.application_timing,
            dilution_text=application.dilution_text,
            dilution_factor=application.dilution_factor,
            use_quantity=application.use_quantity,
            use_unit=application.use_unit,
            max_use_count_text=application.max_use_count_text,
            max_use_count=application.max_use_count,
            human_livestock_toxicity=application.human_livestock_toxicity,
            ecotoxicity=application.ecotoxicity,
            is_registered=product.is_registered,
            registration_valid_until=product.registration_valid_until,
            source_file_name=product.source_file_name,
            source_row_index=product.source_row_index,
            search_text=search_text,
            render_text=render_text,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        caches.documents[key] = document
        return document

    existing.crop_name = crop.crop_name
    existing.crop_name_normalized = crop.crop_name_normalized
    existing.target_name = target.target_name
    existing.target_name_normalized = target.target_name_normalized
    existing.target_kind = target.target_kind
    existing.ingredient_or_formulation_name = product.ingredient_or_formulation_name
    existing.pesticide_name_eng = product.pesticide_name_eng
    existing.brand_name = product.brand_name
    existing.corporation_name = product.corporation_name
    existing.registration_number = product.registration_number
    existing.product_code = product.product_code
    existing.usage_purpose_name = product.usage_purpose_name
    existing.formulation_name = product.formulation_name
    existing.application_method = application.application_method
    existing.application_timing = application.application_timing
    existing.dilution_text = application.dilution_text
    existing.dilution_factor = application.dilution_factor
    existing.use_quantity = application.use_quantity
    existing.use_unit = application.use_unit
    existing.max_use_count_text = application.max_use_count_text
    existing.max_use_count = application.max_use_count
    existing.human_livestock_toxicity = application.human_livestock_toxicity
    existing.ecotoxicity = application.ecotoxicity
    existing.is_registered = product.is_registered
    existing.registration_valid_until = product.registration_valid_until
    existing.source_file_name = product.source_file_name
    existing.source_row_index = product.source_row_index
    existing.search_text = search_text
    existing.render_text = render_text
    existing.updated_at = now
    return existing


def load_backend_env(env_path: Path) -> None:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        return
    log(f"경고: backend env 파일이 없습니다: {env_path.as_posix()}")


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def normalize_db_url(url: str | None) -> str | None:
    if not url:
        return None
    normalized = url.strip()
    if normalized.startswith("postgres://"):
        return normalized.replace("postgres://", "postgresql+psycopg2://", 1)
    if normalized.startswith("postgresql+asyncpg://"):
        return normalized.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if normalized.startswith("postgresql+psycopg://"):
        return normalized.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if normalized.startswith("postgresql://"):
        return normalized.replace("postgresql://", "postgresql+psycopg2://", 1)
    return normalized


def build_engine(args: argparse.Namespace):
    if args.db_url:
        db_url = normalize_db_url(args.db_url)
        if not db_url:
            raise RuntimeError("--db-url 값이 비어 있습니다.")
        if db_url.startswith("postgresql+psycopg2://"):
            return create_engine(
                db_url,
                future=True,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(POSTGRES_PING_TIMEOUT_SECONDS)},
            )
        return create_engine(db_url, future=True)
    env_db_url = normalize_db_url(os.getenv("DATABASE_URL"))
    if env_db_url:
        if env_db_url.startswith("postgresql+psycopg2://"):
            return create_engine(
                env_db_url,
                future=True,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(POSTGRES_PING_TIMEOUT_SECONDS)},
            )
        return create_engine(env_db_url, future=True)
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=first_non_empty(
            args.postgres_user, os.getenv("POSTGRES_USER"), DEFAULT_POSTGRES_USER
        ),
        password=first_non_empty(
            args.postgres_password,
            os.getenv("POSTGRES_PASSWORD"),
            os.getenv("PGPASSWORD"),
            DEFAULT_POSTGRES_PASSWORD,
        ),
        host=first_non_empty(
            args.postgres_host, os.getenv("POSTGRES_HOST"), DEFAULT_POSTGRES_HOST
        ),
        port=int(
            first_non_empty(
                args.postgres_port, os.getenv("POSTGRES_PORT"), DEFAULT_POSTGRES_PORT
            )
        ),
        database=first_non_empty(
            args.postgres_db, os.getenv("POSTGRES_DB"), DEFAULT_POSTGRES_DB
        ),
    )
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(POSTGRES_PING_TIMEOUT_SECONDS)},
    )


def wait_for_postgres_server(engine: Any, max_retries: int) -> None:
    host = engine.url.host or DEFAULT_POSTGRES_HOST
    port = int(engine.url.port or DEFAULT_POSTGRES_PORT)
    log(f"PostgreSQL 서버 연결 확인 시작: {host}:{port}")

    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            with socket.create_connection(
                (host, port), timeout=POSTGRES_PING_TIMEOUT_SECONDS
            ):
                pass
            log(f"PostgreSQL 서버 연결 확인 완료: {host}:{port}")
            return
        except OSError as exc:
            if attempt >= max_retries:
                raise RuntimeError(
                    "PostgreSQL 서버 연결 재시도 초과: "
                    f"{host}:{port}, attempts={max_retries}, last_error={exc}"
                ) from exc
            log(
                "PostgreSQL 서버 응답 대기 중: "
                f"{host}:{port}, attempt={attempt}/{max_retries}, error={exc}. "
                f"{int(POSTGRES_RETRY_INTERVAL_SECONDS)}초 후 재시도합니다."
            )
            time.sleep(POSTGRES_RETRY_INTERVAL_SECONDS)


def rebuild_tables(engine: Any) -> None:
    # 과거 무접두어 테이블이 남아 있으면 함께 정리해 이름 규칙을 일관되게 유지한다.
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table_name in LEGACY_TABLE_NAMES:
            if not TABLE_NAME_PATTERN.fullmatch(table_name):
                raise ValueError(f"Invalid table name: {table_name}")
            if table_name in existing_tables:
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def get_or_create_crop(
    session: Session,
    cache: dict[str, Crop],
    crop_name: str,
    now: str,
) -> Crop:
    normalized = normalize_keyword(crop_name)
    cached = cache.get(normalized)
    if cached is not None:
        return cached
    existing = session.scalar(
        select(Crop).where(Crop.crop_name_normalized == normalized)
    )
    if existing is not None:
        cache[normalized] = existing
        return existing
    crop = Crop(
        crop_name=crop_name,
        crop_name_normalized=normalized,
        created_at=now,
        updated_at=now,
    )
    session.add(crop)
    cache[normalized] = crop
    return crop


def get_or_create_target(
    session: Session,
    cache: dict[tuple[str, str], Target],
    target_name: str,
    target_kind: str,
    now: str,
) -> Target:
    normalized = normalize_keyword(target_name)
    cache_key = (normalized, target_kind)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    existing = session.scalar(
        select(Target).where(
            Target.target_name_normalized == normalized,
            Target.target_kind == target_kind,
        )
    )
    if existing is not None:
        cache[cache_key] = existing
        return existing
    target = Target(
        target_name=target_name,
        target_name_normalized=normalized,
        target_kind=target_kind,
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    cache[cache_key] = target
    return target


def iter_pairs(raw_row: dict[str, Any]) -> list[tuple[str, str, str]]:
    crop_names = split_top_level(raw_row.get("CROPS_NM")) or ["작물정보없음"]
    target_names = split_top_level(raw_row.get("SICKNS_HLSCT_NM_WEEDS_NM"))
    selected_target_names: list[str] = []
    seen_target_names: set[str] = set()
    for target_name in target_names:
        if not is_selected_target_name(target_name):
            continue
        canonical_target_name = canonicalize_target_name(target_name)
        if canonical_target_name in seen_target_names:
            continue
        seen_target_names.add(canonical_target_name)
        selected_target_names.append(canonical_target_name)
    return [
        (crop_name, target_name, infer_target_kind(target_name))
        for crop_name in crop_names
        for target_name in selected_target_names
    ]


def populate_database(
    session: Session,
    source_files: list[Path],
    log_every: int,
    append_mode: bool,
    flush_every: int,
    commit_every_files: int,
) -> Stats:
    stats = Stats()
    crop_cache: dict[str, Crop] = {}
    target_cache: dict[tuple[str, str], Target] = {}
    seen_crop_keys: set[str] = set()
    seen_target_keys: set[tuple[str, str]] = set()
    caches = UpsertCaches(
        products={},
        products_by_identity={},
        applications={},
        documents={},
    )

    for source_file in source_files:
        log(f"파일 처리 시작: {source_file.name}")
        stats.files_seen += 1
        source_start_index, _ = parse_range_from_result_path(source_file)
        rows = load_rows(source_file)
        log(f"파일 로드 완료: {source_file.name}, rows={len(rows)}")

        for row_index, raw_row in enumerate(rows):
            stats.rows_seen += 1
            now = utcnow_iso()
            pairs = iter_pairs(raw_row)
            if not pairs:
                continue
            product_candidate = build_product(
                raw_row, source_file, source_start_index, row_index, now
            )
            product = upsert_product(
                session,
                caches,
                product_candidate,
                now,
                append_mode=append_mode,
            )
            stats.products_written += 1

            application_payload = build_application_payload(
                raw_row, source_file, row_index
            )
            for crop_name, target_name, target_kind in pairs:
                crop = get_or_create_crop(
                    session,
                    crop_cache,
                    crop_name,
                    now,
                )
                seen_crop_keys.add(crop.crop_name_normalized)
                target = get_or_create_target(
                    session,
                    target_cache,
                    target_name,
                    target_kind,
                    now,
                )
                seen_target_keys.add(
                    (target.target_name_normalized, target.target_kind)
                )

                application = upsert_application(
                    session,
                    caches,
                    product=product,
                    crop=crop,
                    target=target,
                    application_payload=application_payload,
                    now=now,
                    append_mode=append_mode,
                )
                stats.applications_written += 1

                upsert_rag_document(
                    session,
                    caches,
                    application=application,
                    product=product,
                    crop=crop,
                    target=target,
                    application_payload=application_payload,
                    now=now,
                    append_mode=append_mode,
                )
                stats.rag_documents_written += 1

            if log_every > 0 and stats.rows_seen % log_every == 0:
                log(
                    "중간 진행상황: "
                    f"rows_seen={stats.rows_seen}, "
                    f"products={stats.products_written}, "
                    f"applications={stats.applications_written}, "
                    f"rag_documents={stats.rag_documents_written}"
                )
            if flush_every > 0 and stats.rows_seen % flush_every == 0:
                session.flush()
                session.expunge_all()
                crop_cache.clear()
                target_cache.clear()
                caches = UpsertCaches(
                    products={},
                    products_by_identity={},
                    applications={},
                    documents={},
                )
                log(
                    f"중간 flush/expunge 완료: rows_seen={stats.rows_seen}, "
                    f"files_seen={stats.files_seen}"
                )

        log(
            "파일 처리 완료: "
            f"{source_file.name}, 누적 rows_seen={stats.rows_seen}, "
            f"누적 rag_documents={stats.rag_documents_written}"
        )
        if commit_every_files > 0 and stats.files_seen % commit_every_files == 0:
            session.commit()
            session.expunge_all()
            crop_cache.clear()
            target_cache.clear()
            caches = UpsertCaches(
                products={},
                products_by_identity={},
                applications={},
                documents={},
            )
            log(
                f"중간 commit 완료: files_seen={stats.files_seen}, "
                f"rows_seen={stats.rows_seen}"
            )

    stats.crops_written = len(seen_crop_keys)
    stats.targets_written = len(seen_target_keys)
    return stats


def main() -> int:
    ensure_backend_uv_python()
    set_log_prefix(LOG_PREFIX)
    args = parse_args()
    load_backend_env(args.backend_env_path)
    if args.log_every < 0:
        raise RuntimeError("--log-every 는 0 이상이어야 합니다.")
    if args.postgres_max_retries <= 0:
        raise RuntimeError("--postgres-max-retries 는 1 이상이어야 합니다.")
    if args.flush_every < 0:
        raise RuntimeError("--flush-every 는 0 이상이어야 합니다.")
    if args.commit_every_files < 0:
        raise RuntimeError("--commit-every-files 는 0 이상이어야 합니다.")
    input_dir: Path = args.input_dir
    if not input_dir.exists() or not input_dir.is_dir():
        error(f"json_raw 디렉터리가 없어 적재를 건너뜁니다: {input_dir}")
        return 0

    source_files = collect_source_files(input_dir, args.glob)
    if not source_files:
        error(f"json_raw 파일이 없어 적재를 건너뜁니다: {input_dir}")
        return 0

    log(f"전처리 시작: input_dir={input_dir.as_posix()}, files={len(source_files)}")
    engine = build_engine(args)
    wait_for_postgres_server(engine, max_retries=args.postgres_max_retries)
    if not args.append:
        log("테이블 재생성(drop/create)을 수행합니다.")
        rebuild_tables(engine)
    else:
        log("기존 테이블 유지(create_all) 모드로 수행합니다.")
        Base.metadata.create_all(engine)

    with Session(engine) as session:
        stats = populate_database(
            session,
            source_files,
            args.log_every,
            append_mode=args.append,
            flush_every=args.flush_every,
            commit_every_files=args.commit_every_files,
        )
        log("DB commit을 수행합니다.")
        session.commit()

    safe_destination = engine.url.render_as_string(hide_password=True)
    log(f"[DONE] destination={safe_destination}")
    log(
        "[DONE] summary: "
        f"files_seen={stats.files_seen}, "
        f"rows_seen={stats.rows_seen}, "
        f"products_written={stats.products_written}, "
        f"crops_written={stats.crops_written}, "
        f"targets_written={stats.targets_written}, "
        f"applications_written={stats.applications_written}, "
        f"rag_documents_written={stats.rag_documents_written}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        error(str(exc))
        raise

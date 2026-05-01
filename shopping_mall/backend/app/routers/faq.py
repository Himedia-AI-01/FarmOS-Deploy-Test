"""⑤ FAQ 관리 라우터 (어드민 전용).

카테고리와 문서를 단일 라우터에서 관리합니다.

엔드포인트:
    # 카테고리
    GET    /api/admin/faq-categories         — 카테고리 목록
    POST   /api/admin/faq-categories         — 카테고리 생성
    PUT    /api/admin/faq-categories/{id}    — 카테고리 수정
    DELETE /api/admin/faq-categories/{id}    — 카테고리 삭제

    # 문서
    GET    /api/admin/faq-docs               — 문서 목록 (필터·애널리틱스)
    POST   /api/admin/faq-docs               — 문서 생성 + ChromaDB 동기화
    GET    /api/admin/faq-docs/{doc_id}      — 문서 단건 조회
    PUT    /api/admin/faq-docs/{doc_id}      — 문서 수정 + ChromaDB 동기화
    DELETE /api/admin/faq-docs/{doc_id}      — 문서 소프트 삭제

ChromaDB 동기화는 BackgroundTasks로 처리됩니다.
어드민은 즉시 응답을 받고, 임베딩 계산은 백그라운드에서 완료됩니다.
"""
import json
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.faq_category import FaqCategory
from app.models.faq_doc import FaqDoc, CHROMA_COLLECTION
from app.models.faq_citation import FaqCitation
from app.services.faq_sync import FaqSync

logger = logging.getLogger(__name__)

# ── 라우터 인스턴스 ────────────────────────────────────────────────────────────

_categories_router = APIRouter(prefix="/api/admin/faq-categories", tags=["admin-faq"])
_docs_router = APIRouter(prefix="/api/admin/faq-docs", tags=["admin-faq"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# ── 상수 ──────────────────────────────────────────────────────────────────────
# chroma_doc_id 자동 생성에 사용할 UUID 해시 길이 (12자 = 충분한 유일성)
_CHROMA_DOC_ID_HEX_LEN: int = 12
# FAQ 목록 조회 최대 결과 수
_FAQ_LIST_MAX_LIMIT: int = 500
# FAQ 목록 조회 기본 결과 수
_FAQ_LIST_DEFAULT_LIMIT: int = 200


# ── Pydantic 스키마 (카테고리) ──────────────────────────────────────────────────

class FaqCategoryCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    color: str = "bg-stone-100 text-stone-700"
    icon: str = "help"
    sort_order: int = 0

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError("slug는 소문자·숫자·하이픈만 사용 가능합니다 (예: exchange-return).")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name은 빈 문자열일 수 없습니다.")
        return v


class FaqCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("name", "description", "color", "icon", mode="before")
    @classmethod
    def strip_and_reject_blank(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("빈 문자열은 허용되지 않습니다.")
        return v


class FaqCategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    color: str
    icon: str
    sort_order: int
    is_active: bool
    doc_count: int = 0       # 활성 FAQ 문서 수
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


# ── Pydantic 스키마 (문서) ─────────────────────────────────────────────────────

class FaqDocCreate(BaseModel):
    faq_category_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=10000)
    extra_metadata: dict = {}
    chroma_doc_id: Optional[str] = None  # 미입력 시 자동 생성


class FaqDocUpdate(BaseModel):
    faq_category_id: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None
    extra_metadata: Optional[dict] = None
    is_active: Optional[bool] = None

    @field_validator("title", "content", mode="before")
    @classmethod
    def strip_and_reject_blank(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("빈 문자열은 허용되지 않습니다.")
        return v


class FaqDocResponse(BaseModel):
    id: int
    faq_category_id: Optional[int] = None
    faq_category_name: Optional[str] = None
    faq_category_slug: Optional[str] = None
    chroma_collection: str
    chroma_doc_id: str
    title: str
    content: str
    extra_metadata: dict
    is_active: bool
    created_at: str
    updated_at: str
    # 애널리틱스
    citation_count: int = 0

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_doc(
        cls,
        doc: FaqDoc,
        citation_count: int = 0,
    ) -> "FaqDocResponse":
        meta: dict = {}
        try:
            meta = json.loads(doc.extra_metadata or "{}")
        except Exception:
            pass
        return cls(
            id=doc.id,
            faq_category_id=doc.faq_category_id,
            faq_category_name=doc.faq_category.name if doc.faq_category else None,
            faq_category_slug=doc.faq_category.slug if doc.faq_category else None,
            chroma_collection=doc.chroma_collection,
            chroma_doc_id=doc.chroma_doc_id,
            title=doc.title,
            content=doc.content,
            extra_metadata=meta,
            is_active=doc.is_active,
            created_at=doc.created_at.isoformat(),
            updated_at=doc.updated_at.isoformat(),
            citation_count=citation_count,
        )


# ── 헬퍼 함수 (카테고리) ────────────────────────────────────────────────────────

def _to_response(cat: FaqCategory, db: Session) -> FaqCategoryResponse:
    doc_count = (
        db.query(FaqDoc)
        .filter(FaqDoc.faq_category_id == cat.id, FaqDoc.is_active == True)
        .count()
    )
    return FaqCategoryResponse(
        id=cat.id,
        name=cat.name,
        slug=cat.slug,
        description=cat.description,
        color=cat.color,
        icon=cat.icon,
        sort_order=cat.sort_order,
        is_active=cat.is_active,
        doc_count=doc_count,
        created_at=cat.created_at.isoformat(),
        updated_at=cat.updated_at.isoformat(),
    )


# ── 헬퍼 함수 (문서) ────────────────────────────────────────────────────────────

def _make_chroma_doc_id() -> str:
    """ChromaDB 문서 ID 자동 생성 — UUID 기반으로 race condition 없음."""
    return f"faq_{uuid.uuid4().hex[:_CHROMA_DOC_ID_HEX_LEN]}"


def _get_analytics(db: Session, doc_ids: list[int]) -> dict[int, dict]:
    """doc_id 목록에 대한 인용 수 집계를 한 번에 반환합니다."""
    if not doc_ids:
        return {}

    citation_rows = (
        db.query(FaqCitation.faq_doc_id, func.count(FaqCitation.id).label("cnt"))
        .filter(FaqCitation.faq_doc_id.in_(doc_ids))
        .group_by(FaqCitation.faq_doc_id)
        .all()
    )
    citation_map: dict[int, int] = {r.faq_doc_id: r.cnt for r in citation_rows}

    return {
        doc_id: {"citation_count": citation_map.get(doc_id, 0)}
        for doc_id in doc_ids
    }


# ── 백그라운드 태스크 헬퍼 ────────────────────────────────────────────────────────

def _sync_unlinked_docs(doc_ids: list[int]) -> None:
    """카테고리 삭제 후 미분류 전환된 문서를 ChromaDB에 재동기화합니다.

    백그라운드에서 새 세션을 열어 최신 상태(faq_category_id=None)로 re-query 후 upsert.
    """
    from sqlalchemy.orm import joinedload as _joinedload

    db = SessionLocal()
    try:
        docs = (
            db.query(FaqDoc)
            .options(_joinedload(FaqDoc.faq_category))
            .filter(FaqDoc.id.in_(doc_ids))
            .all()
        )
        for doc in docs:
            FaqSync.upsert(doc)
    except Exception as e:
        logger.error("[faq_categories] 미분류 문서 ChromaDB 재동기화 실패: %s", e)
    finally:
        db.close()


# ── 엔드포인트 (카테고리) ──────────────────────────────────────────────────────

@_categories_router.get("", response_model=list[FaqCategoryResponse])
def list_faq_categories(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """FAQ 서브카테고리 목록을 sort_order 순으로 반환합니다."""
    q = db.query(FaqCategory)
    if not include_inactive:
        q = q.filter(FaqCategory.is_active == True)
    cats = q.order_by(FaqCategory.sort_order.asc(), FaqCategory.id.asc()).all()

    if not cats:
        return []

    # 문서 수 일괄 집계 — 카테고리당 개별 COUNT 대신 IN 쿼리 1회
    cat_ids = [c.id for c in cats]
    count_rows = (
        db.query(FaqDoc.faq_category_id, func.count(FaqDoc.id).label("cnt"))
        .filter(FaqDoc.faq_category_id.in_(cat_ids), FaqDoc.is_active == True)
        .group_by(FaqDoc.faq_category_id)
        .all()
    )
    count_map: dict[int, int] = {row.faq_category_id: row.cnt for row in count_rows}

    return [
        FaqCategoryResponse(
            id=c.id,
            name=c.name,
            slug=c.slug,
            description=c.description,
            color=c.color,
            icon=c.icon,
            sort_order=c.sort_order,
            is_active=c.is_active,
            doc_count=count_map.get(c.id, 0),
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in cats
    ]


@_categories_router.post("", response_model=FaqCategoryResponse, status_code=201)
def create_faq_category(
    body: FaqCategoryCreate,
    db: Session = Depends(get_db),
):
    """새 FAQ 서브카테고리를 생성합니다."""
    existing = db.query(FaqCategory).filter(FaqCategory.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"슬러그 '{body.slug}'가 이미 존재합니다.")

    cat = FaqCategory(
        name=body.name,
        slug=body.slug,
        description=body.description,
        color=body.color,
        icon=body.icon,
        sort_order=body.sort_order,
        is_active=True,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    logger.info("[faq_categories] 생성: id=%d slug=%s", cat.id, cat.slug)
    return _to_response(cat, db)


@_categories_router.put("/{cat_id}", response_model=FaqCategoryResponse)
def update_faq_category(
    cat_id: int,
    body: FaqCategoryUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """FAQ 서브카테고리를 수정합니다."""
    cat = db.query(FaqCategory).filter(FaqCategory.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없습니다.")

    old_name = cat.name

    if body.name is not None:
        cat.name = body.name.strip()
    if body.description is not None:
        cat.description = body.description
    if body.color is not None:
        cat.color = body.color
    if body.icon is not None:
        cat.icon = body.icon
    if body.sort_order is not None:
        cat.sort_order = body.sort_order
    if body.is_active is not None:
        cat.is_active = body.is_active

    db.commit()
    db.refresh(cat)
    logger.info("[faq_categories] 수정: id=%d", cat_id)

    # 카테고리 이름이 바뀌면 연결된 FAQ 문서의 ChromaDB 텍스트가 stale해짐
    # (to_chroma_document()가 "[카테고리명] Q: ..." 형식으로 prefix를 포함하므로)
    if body.name is not None and cat.name != old_name:
        from sqlalchemy.orm import joinedload as _joinedload
        docs = (
            db.query(FaqDoc)
            .options(_joinedload(FaqDoc.faq_category))
            .filter(FaqDoc.faq_category_id == cat_id)
            .all()
        )
        for doc in docs:
            background_tasks.add_task(FaqSync.upsert, doc)
        logger.info("[faq_categories] 이름 변경으로 %d개 문서 ChromaDB 재동기화 예약: id=%d", len(docs), cat_id)

    return _to_response(cat, db)


@_categories_router.delete("/{cat_id}", status_code=204)
def delete_faq_category(
    cat_id: int,
    force: bool = Query(False, description="True이면 연결된 문서가 있어도 삭제 (문서는 미분류로 전환)"),
    background_tasks: BackgroundTasks = Depends(),
    db: Session = Depends(get_db),
):
    """FAQ 서브카테고리를 삭제합니다.

    force=False (기본): 연결된 활성 문서가 있으면 409 반환.
    force=True: 연결된 문서의 faq_category_id를 NULL로 변경 후 삭제.
    """
    cat = db.query(FaqCategory).filter(FaqCategory.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없습니다.")

    linked_docs = (
        db.query(FaqDoc)
        .filter(FaqDoc.faq_category_id == cat_id)
        .all()
    )
    linked_count = len(linked_docs)

    if linked_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"이 카테고리에 연결된 FAQ 문서가 {linked_count}개 있습니다. "
                "force=true로 요청하면 문서를 미분류로 전환 후 삭제합니다."
            ),
        )

    # commit 전에 ID를 캡처 — commit 후 linked_docs 인스턴스는 expired/detached
    doc_ids = [doc.id for doc in linked_docs] if linked_count > 0 else []

    if linked_count > 0:
        # 연결 문서를 미분류(NULL)로 전환
        db.query(FaqDoc).filter(
            FaqDoc.faq_category_id == cat_id
        ).update({"faq_category_id": None})

    db.delete(cat)
    db.commit()
    logger.info("[faq_categories] 삭제: id=%d slug=%s (linked=%d)", cat_id, cat.slug, linked_count)

    # 카테고리 삭제 후 연결 문서의 ChromaDB 메타데이터가 stale해짐
    # (subcategory_slug / subcategory_name / faq_category_id 필드가 구 카테고리를 가리킴)
    # 백그라운드에서 새 세션으로 re-query → faq_category_id=None 상태로 upsert
    if doc_ids:
        background_tasks.add_task(_sync_unlinked_docs, doc_ids)


# ── 엔드포인트 (문서) ──────────────────────────────────────────────────────────

@_docs_router.get("", response_model=list[FaqDocResponse])
def list_faq_docs(
    faq_category_id: Optional[int] = Query(None, description="서브카테고리 ID 필터"),
    is_active: Optional[bool] = Query(None),
    include_analytics: bool = Query(True, description="인용/피드백 통계 포함 여부"),
    limit: int = Query(_FAQ_LIST_DEFAULT_LIMIT, ge=1, le=_FAQ_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """FAQ 문서 목록을 반환합니다."""
    from sqlalchemy.orm import joinedload

    q = db.query(FaqDoc).options(joinedload(FaqDoc.faq_category))
    if faq_category_id is not None:
        q = q.filter(FaqDoc.faq_category_id == faq_category_id)
    if is_active is not None:
        q = q.filter(FaqDoc.is_active == is_active)
    docs = q.order_by(FaqDoc.id.desc()).offset(offset).limit(limit).all()

    analytics: dict[int, dict] = {}
    if include_analytics and docs:
        analytics = _get_analytics(db, [d.id for d in docs])

    return [
        FaqDocResponse.from_orm_doc(d, **analytics.get(d.id, {}))
        for d in docs
    ]


@_docs_router.post("", response_model=FaqDocResponse, status_code=201)
def create_faq_doc(
    body: FaqDocCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """새 FAQ 문서를 생성하고 ChromaDB에 백그라운드 동기화합니다."""
    from sqlalchemy.orm import joinedload

    chroma_doc_id = body.chroma_doc_id or _make_chroma_doc_id()

    if db.query(FaqDoc).filter(FaqDoc.chroma_doc_id == chroma_doc_id).first():
        raise HTTPException(
            status_code=409,
            detail=f"chroma_doc_id '{chroma_doc_id}' 가 이미 존재합니다.",
        )

    if body.faq_category_id is not None:
        if not db.query(FaqCategory).filter(FaqCategory.id == body.faq_category_id).first():
            raise HTTPException(status_code=404, detail="지정한 카테고리를 찾을 수 없습니다.")

    faq_doc = FaqDoc(
        faq_category_id=body.faq_category_id,
        category="faq",
        chroma_collection=CHROMA_COLLECTION,
        chroma_doc_id=chroma_doc_id,
        title=body.title,
        content=body.content,
        extra_metadata=json.dumps(body.extra_metadata, ensure_ascii=False),
        is_active=True,
    )
    db.add(faq_doc)
    db.commit()

    faq_doc = (
        db.query(FaqDoc)
        .options(joinedload(FaqDoc.faq_category))
        .filter(FaqDoc.id == faq_doc.id)
        .one()
    )
    background_tasks.add_task(FaqSync.upsert, faq_doc)
    logger.info("[faq] 생성: id=%d faq_category_id=%s", faq_doc.id, faq_doc.faq_category_id)
    return FaqDocResponse.from_orm_doc(faq_doc)


@_docs_router.get("/{doc_id}", response_model=FaqDocResponse)
def get_faq_doc(doc_id: int, db: Session = Depends(get_db)):
    """FAQ 문서 단건 조회."""
    from sqlalchemy.orm import joinedload

    faq_doc = (
        db.query(FaqDoc)
        .options(joinedload(FaqDoc.faq_category))
        .filter(FaqDoc.id == doc_id)
        .first()
    )
    if not faq_doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    analytics = _get_analytics(db, [faq_doc.id]).get(faq_doc.id, {})
    return FaqDocResponse.from_orm_doc(faq_doc, **analytics)


@_docs_router.put("/{doc_id}", response_model=FaqDocResponse)
def update_faq_doc(
    doc_id: int,
    body: FaqDocUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """FAQ 문서를 수정하고 ChromaDB에 백그라운드 동기화합니다."""
    from sqlalchemy.orm import joinedload

    faq_doc = (
        db.query(FaqDoc)
        .options(joinedload(FaqDoc.faq_category))
        .filter(FaqDoc.id == doc_id)
        .first()
    )
    if not faq_doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    if body.faq_category_id is not None:
        if body.faq_category_id == 0:
            faq_doc.faq_category_id = None  # 0 = 미분류 전환
        else:
            if not db.query(FaqCategory).filter(FaqCategory.id == body.faq_category_id).first():
                raise HTTPException(status_code=404, detail="지정한 카테고리를 찾을 수 없습니다.")
            faq_doc.faq_category_id = body.faq_category_id

    if body.title is not None:
        faq_doc.title = body.title
    if body.content is not None:
        faq_doc.content = body.content
    if body.extra_metadata is not None:
        faq_doc.extra_metadata = json.dumps(body.extra_metadata, ensure_ascii=False)
    if body.is_active is not None:
        faq_doc.is_active = body.is_active

    db.commit()

    faq_doc = (
        db.query(FaqDoc)
        .options(joinedload(FaqDoc.faq_category))
        .filter(FaqDoc.id == doc_id)
        .one()
    )

    if faq_doc.is_active:
        background_tasks.add_task(FaqSync.upsert, faq_doc)
    else:
        background_tasks.add_task(
            FaqSync.delete, faq_doc.chroma_doc_id, faq_doc.chroma_collection
        )

    logger.info("[faq] 수정: id=%d is_active=%s", faq_doc.id, faq_doc.is_active)
    analytics = _get_analytics(db, [faq_doc.id]).get(faq_doc.id, {})
    return FaqDocResponse.from_orm_doc(faq_doc, **analytics)


@_docs_router.delete("/{doc_id}", status_code=204)
def delete_faq_doc(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """FAQ 문서를 소프트 삭제(is_active=False)하고 ChromaDB에서 제거합니다."""
    faq_doc = db.query(FaqDoc).filter(FaqDoc.id == doc_id).first()
    if not faq_doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    faq_doc.is_active = False
    db.commit()

    background_tasks.add_task(
        FaqSync.delete, faq_doc.chroma_doc_id, faq_doc.chroma_collection
    )
    logger.info("[faq] 소프트 삭제: id=%d chroma_id=%s", faq_doc.id, faq_doc.chroma_doc_id)


# ── 단일 진입점 ────────────────────────────────────────────────────────────────

# main.py에서 이것만 include
router = APIRouter()
router.include_router(_categories_router)
router.include_router(_docs_router)

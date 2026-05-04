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
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="True이면 연결된 문서가 있어도 삭제 (문서는 미분류로 전환)"),
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


# ── 분석/인사이트 엔드포인트 ──────────────────────────────────────────────────────

_analytics_router = APIRouter(prefix="/api/admin/faq-analytics", tags=["admin-faq"])


class FaqActionSummary(BaseModel):
    """대시보드 카드용 핵심 지표 — 4개 벤토 카드에 사용."""
    total_docs: int
    active_docs: int
    unanswered_count: int       # escalated=True ChatLog 수
    underperforming_count: int  # citation_count == 0인 활성 FAQ 수


@_analytics_router.get("/action-summary", response_model=FaqActionSummary)
def get_faq_action_summary(db: Session = Depends(get_db)):
    """FAQ 관리 대시보드 카드용 핵심 지표를 반환합니다.

    - total_docs: 전체 FAQ 문서 수
    - active_docs: 활성 문서 수
    - unanswered_count: 챗봇이 처리 못해 에스컬레이션된 질문 수
    - underperforming_count: 한 번도 인용되지 않은 활성 FAQ 수
    """
    from app.models.chat_log import ChatLog

    total_docs = db.query(func.count(FaqDoc.id)).scalar() or 0
    active_docs = (
        db.query(func.count(FaqDoc.id)).filter(FaqDoc.is_active == True).scalar() or 0  # noqa: E712
    )
    unanswered_count = (
        db.query(func.count(ChatLog.id)).filter(ChatLog.escalated == True).scalar() or 0  # noqa: E712
    )

    # citation_count == 0인 활성 FAQ 수
    cited_ids = db.query(FaqCitation.faq_doc_id).distinct()
    underperforming_count = (
        db.query(func.count(FaqDoc.id))
        .filter(FaqDoc.is_active == True)  # noqa: E712
        .filter(FaqDoc.id.notin_(cited_ids))
        .scalar() or 0
    )

    return FaqActionSummary(
        total_docs=total_docs,
        active_docs=active_docs,
        unanswered_count=unanswered_count,
        underperforming_count=underperforming_count,
    )


# intent → 관리자용 한글 레이블
_INTENT_LABEL: dict[str, str] = {
    "delivery": "배송·조회",
    "faq": "자주 묻는 질문",
    "stock": "상품·재고",
    "cancel": "취소·환불",
    "escalation": "처리 불가",
    "policy": "정책·약관",
    "refusal": "거절됨",
    "other": "기타",
    "greeting": "인사",
}


class TrendingQuestionItem(BaseModel):
    """이번 주 자주 나온 질문 토픽."""
    intent: str
    intent_label: str       # 관리자용 한글 레이블
    count: int              # 해당 intent 질문 수 (기간 내)
    sample_question: str    # 가장 최근 대표 질문 텍스트


class TrendingQuestionsResponse(BaseModel):
    period_days: int
    total_questions: int    # 기간 내 전체 질문 수
    items: list[TrendingQuestionItem]


@_analytics_router.get("/trending-questions", response_model=TrendingQuestionsResponse)
def get_trending_questions(
    days: int = Query(7, ge=1, le=90, description="집계 기간(일)"),
    limit: int = Query(5, ge=1, le=20, description="반환할 토픽 수"),
    db: Session = Depends(get_db),
):
    """최근 N일간 자주 나온 질문 토픽을 반환합니다.

    ChatLog.intent 기준으로 집계하며, 각 토픽의 대표 질문 텍스트를 함께 반환합니다.
    관리자가 어떤 주제의 FAQ를 강화해야 할지 파악하는 데 사용합니다.
    """
    from app.core.datetime_utils import now_kst
    from app.models.chat_log import ChatLog
    from sqlalchemy import desc

    since = now_kst() - __import__("datetime").timedelta(days=days)

    # 기간 내 전체 질문 수
    total_questions = (
        db.query(func.count(ChatLog.id))
        .filter(ChatLog.created_at >= since)
        .scalar() or 0
    )

    # intent별 카운트 집계
    intent_counts = (
        db.query(ChatLog.intent, func.count(ChatLog.id).label("cnt"))
        .filter(ChatLog.created_at >= since)
        .group_by(ChatLog.intent)
        .order_by(desc("cnt"))
        .limit(limit)
        .all()
    )

    items = []
    for intent, cnt in intent_counts:
        # 해당 intent의 가장 최근 질문 텍스트
        sample = (
            db.query(ChatLog.question)
            .filter(ChatLog.created_at >= since, ChatLog.intent == intent)
            .order_by(ChatLog.created_at.desc())
            .first()
        )
        items.append(TrendingQuestionItem(
            intent=intent,
            intent_label=_INTENT_LABEL.get(intent, intent),
            count=cnt,
            sample_question=sample[0] if sample else "",
        ))

    return TrendingQuestionsResponse(
        period_days=days,
        total_questions=total_questions,
        items=items,
    )


class UnansweredSample(BaseModel):
    """에스컬레이션된 실제 사용자 질문 샘플."""
    id: int
    question: str
    intent: str
    created_at: str


class LeastCitedFaqItem(BaseModel):
    """인용 수 하위 활성 FAQ — 검토·개선·삭제 대상."""
    id: int
    title: str
    category_name: Optional[str] = None
    category_slug: Optional[str] = None
    citation_count: int
    created_at: str


class TopCitedFaqItem(BaseModel):
    """인용 수 기준 상위 FAQ 항목."""
    id: int
    title: str
    category_name: Optional[str] = None
    citation_count: int


@_analytics_router.get("/unanswered-samples", response_model=list[UnansweredSample])
def get_unanswered_samples(
    limit: int = Query(10, ge=1, le=50, description="반환할 최대 항목 수"),
    db: Session = Depends(get_db),
):
    """에스컬레이션된 실제 사용자 질문 목록을 반환합니다.

    챗봇이 처리하지 못해 에스컬레이션된 질문의 원문 텍스트를 제공합니다.
    관리자가 "이 질문에 답하는 FAQ를 등록해야겠다"고 판단하는 데 사용합니다.
    """
    from app.models.chat_log import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.escalated == True)  # noqa: E712
        .order_by(ChatLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        UnansweredSample(
            id=row.id,
            question=row.question,
            intent=row.intent,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@_analytics_router.get("/least-cited", response_model=list[LeastCitedFaqItem])
def get_least_cited_faqs(
    limit: int = Query(5, ge=1, le=50, description="반환할 최대 항목 수"),
    db: Session = Depends(get_db),
):
    """인용 수 하위 활성 FAQ 목록을 반환합니다.

    인용 수 오름차순으로 정렬하여 반환합니다(0인 항목이 먼저 나옴).
    관리자가 내용 개선 또는 삭제를 검토하는 데 사용합니다.

    구현 노트:
      joinedload + GROUP BY + COUNT를 한 쿼리로 조합하면 PostgreSQL이
      집계 대상이 아닌 JOIN 컬럼을 GROUP BY에 요구해 GroupingError가 발생함.
      → 집계(서브쿼리)와 엔티티 로딩(joinedload)을 2단계로 분리해 해결.
    """
    from sqlalchemy.orm import joinedload

    # 1단계: doc_id별 인용 수 집계 (집계 전용 — joinedload 없음)
    count_sq = (
        db.query(FaqCitation.faq_doc_id, func.count(FaqCitation.id).label("cnt"))
        .group_by(FaqCitation.faq_doc_id)
        .subquery()
    )

    # 2단계: 활성 FaqDoc에 인용 수를 LEFT JOIN, 카테고리는 joinedload로 로딩
    rows = (
        db.query(FaqDoc, func.coalesce(count_sq.c.cnt, 0).label("cnt"))
        .outerjoin(count_sq, count_sq.c.faq_doc_id == FaqDoc.id)
        .options(joinedload(FaqDoc.faq_category))
        .filter(FaqDoc.is_active == True)  # noqa: E712
        .order_by(func.coalesce(count_sq.c.cnt, 0).asc(), FaqDoc.created_at.asc())
        .limit(limit)
        .all()
    )
    return [
        LeastCitedFaqItem(
            id=doc.id,
            title=doc.title,
            category_name=doc.faq_category.name if doc.faq_category else None,
            category_slug=doc.faq_category.slug if doc.faq_category else None,
            citation_count=int(cnt),
            created_at=doc.created_at.isoformat(),
        )
        for doc, cnt in rows
    ]


@_analytics_router.get("/top-cited", response_model=list[TopCitedFaqItem])
def get_top_cited_faqs(
    limit: int = Query(10, ge=1, le=50, description="반환할 최대 항목 수"),
    db: Session = Depends(get_db),
):
    """인용 수 기준 상위 FAQ 문서 목록을 반환합니다.

    챗봇이 응답에 가장 많이 활용한 FAQ를 파악하여
    콘텐츠 품질 개선 우선순위를 결정하는 데 사용합니다.

    구현 노트: least-cited와 동일한 이유로 집계/로딩 2단계 분리.
    """
    from sqlalchemy.orm import joinedload

    # 1단계: 인용 수 집계 서브쿼리
    count_sq = (
        db.query(FaqCitation.faq_doc_id, func.count(FaqCitation.id).label("cnt"))
        .group_by(FaqCitation.faq_doc_id)
        .subquery()
    )

    # 2단계: FaqDoc에 인용 수 LEFT JOIN, 카테고리 joinedload
    rows = (
        db.query(FaqDoc, func.coalesce(count_sq.c.cnt, 0).label("cnt"))
        .outerjoin(count_sq, count_sq.c.faq_doc_id == FaqDoc.id)
        .options(joinedload(FaqDoc.faq_category))
        .order_by(func.coalesce(count_sq.c.cnt, 0).desc())
        .limit(limit)
        .all()
    )
    return [
        TopCitedFaqItem(
            id=doc.id,
            title=doc.title,
            category_name=doc.faq_category.name if doc.faq_category else None,
            citation_count=int(cnt),
        )
        for doc, cnt in rows
    ]


class FaqRecommendationItem(BaseModel):
    """FAQ 등록 추천 후보 — 1·2·3위."""
    rank: int
    representative_question: str  # 대표 질문 원문 (가장 최근)
    count: int                    # 같은 질문이 들어온 총 수
    recent_count: int             # 최근 7일 수
    escalated_count: int          # 에스컬레이션 수
    gap_type: str                 # "missing" | "escalated"
    score: float
    top_intent: str
    top_intent_label: str         # 해당 클러스터의 주요 intent 한글 레이블


class FaqRecommendationsResponse(BaseModel):
    period_days: int
    total_gap_questions: int
    items: list[FaqRecommendationItem]


@_analytics_router.get("/faq-recommendations", response_model=FaqRecommendationsResponse)
def get_faq_recommendations(
    days: int = Query(30, ge=7, le=90, description="집계 기간(일), 기본 30일"),
    limit: int = Query(3, ge=1, le=10, description="반환할 추천 수, 기본 3"),
    db: Session = Depends(get_db),
):
    """FAQ 등록 추천 후보 1·2·3위를 반환합니다.

    분석 로직은 app.services.faq_gap_analyzer 에 위임합니다.
    - normalize_query 기반 텍스트 클러스터링으로 같은 질문 묶기
    - 작업 intent(cancel, greeting, refusal) 제외
    - 스코어 = (최근 7일 수 × 1.5 + 이전 수) × (1 + 에스컬레이션 비율)
    """
    from app.services.faq_gap_analyzer import analyze

    result = analyze(db, days=days, limit=limit)

    items = [
        FaqRecommendationItem(
            rank=item.rank,
            representative_question=item.representative_question,
            count=item.count,
            recent_count=item.recent_count,
            escalated_count=item.escalated_count,
            gap_type=item.gap_type,
            score=item.score,
            top_intent=item.top_intent,
            top_intent_label=item.top_intent_label,
        )
        for item in result.items
    ]

    return FaqRecommendationsResponse(
        period_days=result.period_days,
        total_gap_questions=result.total_gap_questions,
        items=items,
    )


# ── FAQ 작성 에이전트 (싱글턴) ──────────────────────────────────────────────────

_faq_writer = None


def set_faq_writer(agent) -> None:
    """lifespan에서 초기화된 FaqWriterAgent를 주입합니다."""
    global _faq_writer
    _faq_writer = agent


def get_faq_writer():
    """현재 등록된 FaqWriterAgent를 반환합니다. 미초기화 시 None."""
    return _faq_writer


# ── FAQ 초안 자동 생성 ─────────────────────────────────────────────────────────

class FaqDraftRequest(BaseModel):
    representative_question: str
    top_intent: str
    gap_type: str           # "missing" | "escalated"
    count: int
    escalated_count: int


class FaqDraftResponse(BaseModel):
    title: str
    content: str
    suggested_category_id: Optional[int] = None
    suggested_category_slug: Optional[str] = None
    model_used: str
    citation_doc: Optional[str] = None
    citation_chapter: Optional[str] = None
    citation_article: Optional[str] = None
    citation_clause: Optional[str] = None


_ALL_POLICY_COLLECTIONS: list[str] = [
    "return_policy", "payment_policy", "membership_policy",
    "delivery_policy", "quality_policy", "service_policy",
]
_JO_RE = re.compile(r"제\d+조(?:\([^)]*\))?")
_HANG_RE = re.compile(r"제\d+항")
_JO_NUM_RE = re.compile(r"제(\d+)조")
_HANG_NUM_RE = re.compile(r"제(\d+)항")
_JANG_NUM_RE = re.compile(r"제(\d+)장")
# 정책 문서의 항은 원문자(①②③…) 형식으로 표기됨 — "제\d+항" 패턴은 미매칭
# ① = U+2460, ② = U+2461 … ⑳ = U+2473
_CIRCLE_NUM_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]")
_CIRCLE_TO_INT: dict[str, int] = {chr(0x2460 + i): i + 1 for i in range(20)}


class PolicyArticleItem(BaseModel):
    chapter: str   # "제N장 장제목" or "" for uncategorized
    article: str
    clauses: list[str]


@_analytics_router.get("/policy-articles", response_model=list[PolicyArticleItem])
def get_policy_articles(
    doc: str = Query(..., description="정책 문서명 (예: 반품교환환불정책)"),
):
    """정책 문서의 장(章)·조(條)·항(項) 목록을 반환합니다.

    FAQ 등록 모달의 정책 인용 장·조·항 드롭다운에 사용됩니다.
    ChromaDB의 6개 정책 컬렉션을 순회하여 doc_title이 일치하는 청크의
    메타데이터(chapter, article)와 문서 본문(항 추출)을 수집해 정렬 후 반환합니다.

    실제 메타데이터 키 (seed_rag.py chunk_by_articles 기준):
        doc_title → 정책 문서명
        article   → "제N조(조항명)"
        chapter   → "제N장 장제목" (장이 있는 경우만)
    """
    from app.paths import CHROMA_DB_PATH

    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    except Exception as e:
        logger.warning("[policy_articles] ChromaDB 접근 실패: %s", e)
        return []

    # (chapter, article) → set of clauses
    article_clauses: dict[tuple[str, str], set[str]] = {}

    for col_name in _ALL_POLICY_COLLECTIONS:
        try:
            col = client.get_collection(name=col_name)
        except Exception:
            continue
        try:
            result = col.get(where={"doc_title": doc}, include=["metadatas", "documents"])
        except Exception as e:
            logger.warning("[policy_articles] 컬렉션 %s 조회 실패: %s", col_name, e)
            continue

        metadatas = result.get("metadatas") or []
        documents = result.get("documents") or []

        for meta, doc_text in zip(metadatas, documents):
            if not meta or meta.get("doc_title", "") != doc:
                continue

            raw_article = meta.get("article", "") or ""
            chapter = meta.get("chapter", "") or ""

            # 조(條) 추출 — article 메타데이터에서 탐색
            m = _JO_RE.search(raw_article)
            if not m:
                continue
            article = m.group()

            key = (chapter, article)
            if key not in article_clauses:
                article_clauses[key] = set()

            # 항(項) 추출 — 정책 문서는 ①②③ 원문자 형식으로 항을 표기하므로
            # 원문자를 찾아 "제N항" 레이블로 변환한다.
            # (글자 수 제한 없이 청크 전체를 탐색)
            for m in _CIRCLE_NUM_RE.finditer(doc_text or ""):
                n = _CIRCLE_TO_INT.get(m.group(), 0)
                if n:
                    article_clauses[key].add(f"제{n}항")

    def _sort_key(key: tuple[str, str]) -> tuple[int, int]:
        chapter, article = key
        m_jang = _JANG_NUM_RE.search(chapter)
        m_jo = _JO_NUM_RE.search(article)
        return (
            int(m_jang.group(1)) if m_jang else 0,
            int(m_jo.group(1)) if m_jo else 9999,
        )

    def _clause_sort_key(clause: str) -> int:
        m = _HANG_NUM_RE.search(clause)
        return int(m.group(1)) if m else 9999

    return [
        PolicyArticleItem(
            chapter=chapter,
            article=article,
            clauses=sorted(article_clauses[(chapter, article)], key=_clause_sort_key),
        )
        for chapter, article in sorted(article_clauses.keys(), key=_sort_key)
    ]


@_analytics_router.post("/generate-draft", response_model=FaqDraftResponse)
def generate_faq_draft_endpoint(
    body: FaqDraftRequest,
    db: Session = Depends(get_db),
):
    """Gap Analyzer 추천 질문을 바탕으로 FAQ 제목·답변 초안을 자동 생성합니다.

    FaqWriterAgent(LangChain tool-calling)를 사용해 유사 FAQ 검색,
    정책 조회, 카테고리 매핑을 에이전트가 스스로 수행합니다.

    Returns:
        title: AI가 생성한 FAQ 제목 (질문 형태)
        content: AI가 생성한 답변 초안 (어드민이 검토 후 수정 권장)
        suggested_category_id: 추천 카테고리 ID (slug → id 변환, 없을 수 있음)
        suggested_category_slug: 추천 카테고리 slug
        model_used: 사용된 LLM 모델명
        citation_doc / citation_article / citation_clause: 정책 인용 정보
    """
    from app.core.config import settings

    writer = get_faq_writer()
    if writer is None:
        # lifespan 초기화 실패 시 요청 단위 폴백 초기화
        if not settings.litellm_api_key:
            raise HTTPException(status_code=503, detail="LiteLLM 서비스가 설정되지 않았습니다.")
        try:
            from ai.agent.llm import build_primary_llm, build_fallback_llm
            from ai.agent.faq_writer import FaqWriterAgent
            from ai.rag import RAGService
            writer = FaqWriterAgent(
                primary=build_primary_llm(),
                fallback=build_fallback_llm(),
                rag_service=RAGService(),
            )
            set_faq_writer(writer)
        except Exception as e:
            logger.error("[faq_draft] FaqWriterAgent 초기화 실패: %s", e)
            raise HTTPException(status_code=503, detail="FAQ 작성 에이전트를 초기화할 수 없습니다.")

    try:
        result = writer.generate(
            db,
            representative_question=body.representative_question,
            top_intent=body.top_intent,
            gap_type=body.gap_type,
            count=body.count,
            escalated_count=body.escalated_count,
        )
    except (ValueError, RuntimeError) as e:
        logger.error("[faq_draft] 초안 생성 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("[faq_draft] 예기치 않은 오류: %s", e)
        raise HTTPException(status_code=500, detail="FAQ 초안 생성 중 오류가 발생했습니다.")

    # suggested_category_slug → id 변환
    category_id: Optional[int] = None
    if result.suggested_category_slug:
        cat = (
            db.query(FaqCategory)
            .filter(
                FaqCategory.slug == result.suggested_category_slug,
                FaqCategory.is_active.is_(True),
            )
            .first()
        )
        if cat:
            category_id = cat.id

    return FaqDraftResponse(
        title=result.title,
        content=result.content,
        suggested_category_id=category_id,
        suggested_category_slug=result.suggested_category_slug,
        model_used=result.model_used,
        citation_doc=result.citation_doc,
        citation_chapter=result.citation_chapter,
        citation_article=result.citation_article,
        citation_clause=result.citation_clause,
    )


# ── 단일 진입점 ────────────────────────────────────────────────────────────────

# main.py에서 이것만 include
router = APIRouter()
router.include_router(_categories_router)
router.include_router(_docs_router)
router.include_router(_analytics_router)

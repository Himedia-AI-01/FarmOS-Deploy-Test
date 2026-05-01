"""FAQ 문서 모델 — 통합 FAQ.

⑤ CS 지식베이스 자동 동기화:
  PostgreSQL이 단일 진실 소스(Single Source of Truth) 역할을 합니다.
  ChromaDB는 파생 검색 인덱스 — DB 변경 시 FaqSync 서비스가 자동으로 동기화합니다.

모든 지식 문서는 단일 ChromaDB 컬렉션("faq")에 저장되며,
faq_category_id FK로 서브카테고리를 구분합니다.
(구 category 컬럼은 마이그레이션 호환성을 위해 유지하되 "faq" 고정)

정책 문서(PDF/DOCX)는 파일 기반 청킹이 필요하므로 이 모델 대상 아님.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 단일 컬렉션 상수
CHROMA_COLLECTION = "faq"

# 하위 호환용 별칭 — 기존 코드가 이 dict를 참조하는 경우 대비
CATEGORY_TO_COLLECTION: dict[str, str] = {"faq": "faq"}


class FaqDoc(Base):
    """FAQ 문서 — ChromaDB 동기화 단위."""

    __tablename__ = "shop_faq_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── 분류 ──────────────────────────────────────────────────────────────────
    # FAQ 서브카테고리 FK (nullable — 마이그레이션·미분류 문서 허용)
    faq_category_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("shop_faq_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 하위 호환성 유지 컬럼 — 항상 "faq" 고정
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, default="faq"
    )
    # 모든 문서는 단일 "faq" ChromaDB 컬렉션 사용
    chroma_collection: Mapped[str] = mapped_column(
        String(50), nullable=False, default=CHROMA_COLLECTION
    )
    # ChromaDB document ID — 자동 생성 또는 수동 지정
    chroma_doc_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # ── 내용 ──────────────────────────────────────────────────────────────────
    # FAQ 질문 텍스트 (검색의 주요 대상)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # FAQ 답변 본문
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 부가 메타데이터 (JSON 문자열)
    # 예: {"tags": ["배송", "배달"], "source": "faq_legacy"}
    extra_metadata: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # ── 운영 상태 ─────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── 타임스탬프 ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── 관계 ─────────────────────────────────────────────────────────────────
    faq_category: Mapped["FaqCategory | None"] = relationship(  # noqa: F821
        "FaqCategory",
        back_populates="docs",
        foreign_keys=[faq_category_id],
    )

    # ── 헬퍼 ──────────────────────────────────────────────────────────────────

    def to_chroma_document(self) -> str:
        """ChromaDB에 저장할 문서 텍스트를 생성합니다.

        Q&A 형식으로 저장하여 FAQ 검색 품질을 최적화합니다.
        서브카테고리 정보를 앞에 붙여 분류 인식 정확도를 높입니다.
        """
        prefix = ""
        if self.faq_category is not None:
            prefix = f"[{self.faq_category.name}] "
        return f"{prefix}Q: {self.title}\nA: {self.content}"

    def to_chroma_metadata(self) -> dict:
        """ChromaDB document metadata를 생성합니다."""
        import json as _json

        meta: dict = {}
        try:
            meta = _json.loads(self.extra_metadata or "{}")
        except Exception as e:
            logger.warning(
                "extra_metadata 파싱 실패 — FaqDoc id=%s: %s (값: %r)",
                self.id, e, self.extra_metadata,
            )

        base: dict = {
            "db_id": self.id,
            "chroma_doc_id": self.chroma_doc_id,
        }

        if self.faq_category_id is not None:
            base["faq_category_id"] = self.faq_category_id

        # 관계가 로드된 경우 슬러그/이름 포함 (검색 필터용)
        # 세션 분리 상태를 방어적으로 처리 — lazy load 오류 대비
        try:
            cat = self.faq_category  # may trigger lazy load or raise DetachedInstanceError
            if cat is not None:
                base["subcategory_slug"] = cat.slug
                base["subcategory_name"] = cat.name
        except Exception as e:
            # 세션 분리 상태 — 슬러그 없이 저장 (ChromaDB fallback 검색 가능)
            logger.debug(
                "[faq_doc] subcategory 메타데이터 로드 실패 (doc_id=%s): %s",
                self.chroma_doc_id, e, exc_info=True,
            )

        # extra_metadata에서 태그 전파 (콤마 구분 문자열로 저장)
        tags = meta.get("tags")
        if isinstance(tags, list):
            base["tags"] = ",".join(str(t) for t in tags)
        elif isinstance(tags, str):
            base["tags"] = tags

        # 나머지 meta 키 전파 — 예약 키와 충돌하지 않는 경우에만
        _RESERVED = {"db_id", "chroma_doc_id", "faq_category_id", "subcategory_slug", "subcategory_name", "tags"}
        for key, value in meta.items():
            if key not in _RESERVED and key not in base:
                base[key] = value

        return base

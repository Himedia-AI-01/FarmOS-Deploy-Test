"""FAQ 서브카테고리 모델.

관리자가 자유롭게 추가·변경·삭제할 수 있는 FAQ 서브카테고리.
FaqDoc.faq_category_id FK 참조 대상.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FaqCategory(Base):
    """FAQ 서브카테고리 — 관리자 자유 편집 가능."""

    __tablename__ = "shop_faq_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── 식별 ──────────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # URL/필터에 사용되는 고유 슬러그 (소문자·하이픈)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    # ── 표시 ──────────────────────────────────────────────────────────────────
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 예: "bg-blue-100 text-blue-700" — Tailwind 클래스 조합
    color: Mapped[str] = mapped_column(String(100), nullable=False, default="bg-stone-100 text-stone-700")
    # Material Symbols 아이콘 이름 (예: "help", "local_shipping", "payments")
    icon: Mapped[str] = mapped_column(String(80), nullable=False, default="help")
    # 정렬 순서 (낮을수록 앞에 표시)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── 운영 상태 ─────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── 타임스탬프 ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── 관계 ──────────────────────────────────────────────────────────────────
    docs: Mapped[list["FaqDoc"]] = relationship(  # noqa: F821
        "FaqDoc",
        back_populates="faq_category",
        foreign_keys="FaqDoc.faq_category_id",
    )

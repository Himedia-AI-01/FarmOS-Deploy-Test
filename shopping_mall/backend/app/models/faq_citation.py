"""FAQ 인용 추적 모델.

챗봇이 특정 FAQ 문서를 검색해 응답에 활용했을 때 1건씩 기록합니다.
이를 통해 "챗봇이 이 FAQ를 얼마나 참조했는지" 집계가 가능합니다.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FaqCitation(Base):
    """챗봇 응답 1건에서 FAQ 문서 1건이 인용된 기록."""

    __tablename__ = "shop_faq_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 인용이 발생한 ChatLog
    chat_log_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("shop_chat_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 인용된 FaqDoc (DB PK)
    faq_doc_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("shop_faq_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── 제약 ──────────────────────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("chat_log_id", "faq_doc_id", name="uq_faq_citation_log_doc"),
    )

    # ── 관계 ──────────────────────────────────────────────────────────────────
    chat_log = relationship("ChatLog", foreign_keys=[chat_log_id])
    faq_doc = relationship("FaqDoc", foreign_keys=[faq_doc_id])

from datetime import date, datetime, timezone

from sqlalchemy import Integer, String, Date, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("users.id"), nullable=False
    )
    photos: Mapped[list["JournalEntryPhoto"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ── 필수 (농업ON ■) ──
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    crop: Mapped[str] = mapped_column(String(50), nullable=False)
    work_stage: Mapped[str] = mapped_column(String(20), nullable=False)

    # ── 선택 (농업ON □) ──
    weather: Mapped[str | None] = mapped_column(String(20), default=None)

    # 농약/비료 구입
    purchase_pesticide_type: Mapped[str | None] = mapped_column(
        String(50), default=None
    )
    purchase_pesticide_product: Mapped[str | None] = mapped_column(
        String(100), default=None
    )
    purchase_pesticide_amount: Mapped[str | None] = mapped_column(
        String(50), default=None
    )
    purchase_fertilizer_type: Mapped[str | None] = mapped_column(
        String(50), default=None
    )
    purchase_fertilizer_product: Mapped[str | None] = mapped_column(
        String(100), default=None
    )
    purchase_fertilizer_amount: Mapped[str | None] = mapped_column(
        String(50), default=None
    )

    # 농약/비료 사용
    usage_pesticide_type: Mapped[str | None] = mapped_column(String(50), default=None)
    usage_pesticide_product: Mapped[str | None] = mapped_column(
        String(100), default=None
    )
    usage_pesticide_amount: Mapped[str | None] = mapped_column(String(50), default=None)
    usage_fertilizer_type: Mapped[str | None] = mapped_column(String(50), default=None)
    usage_fertilizer_product: Mapped[str | None] = mapped_column(
        String(100), default=None
    )
    usage_fertilizer_amount: Mapped[str | None] = mapped_column(
        String(50), default=None
    )

    # 세부작업내용
    detail: Mapped[str | None] = mapped_column(Text, default=None)

    # ── 시스템 ──
    raw_stt_text: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(String(10), default="text")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class JournalEntryPhoto(Base):
    """영농일지 entry 에 첨부된 사진 메타데이터.

    파일 자체는 디스크 (UPLOAD_BASE_DIR/journal/{user_id}/) 에 저장되고,
    DB 행은 그 메타와 entry/owner 관계를 보관한다. entry_id=null 인 행은
    분석만 끝나고 아직 entry 와 연결되지 않은 임시 사진 (24h 후 cleanup).
    """

    __tablename__ = "journal_entry_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("users.id"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(255), default=None)
    original_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    mime_type: Mapped[str] = mapped_column(String(50), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    entry: Mapped["JournalEntry | None"] = relationship(back_populates="photos")

from datetime import datetime
from sqlalchemy import Boolean, Integer, String, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ChatLog(Base):
    __tablename__ = "shop_chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("shop_users.id"), nullable=True)
    session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("shop_chat_sessions.id"), nullable=True)
    intent: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    user = relationship("User", foreign_keys=[user_id])
    session = relationship("ChatSession", back_populates="logs", foreign_keys=[session_id])

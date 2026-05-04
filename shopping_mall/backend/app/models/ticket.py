"""교환/취소/주문 변경 티켓 모델 — OrderGraph 멀티스텝 HitL 최종 결과물."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ShopTicket(Base):
    """교환·취소·주문 변경 접수 티켓.

    OrderGraph가 사용자와 멀티스텝 대화로 수집한 정보를 최종 저장합니다.
    오피스 툴에서 이 테이블을 읽어 처리합니다.
    """

    __tablename__ = "shop_tickets"

    # 동일 주문에 대해 동일 액션의 "received" 티켓이 중복 생성되지 않도록 보장.
    # 부분 유니크 인덱스 — status가 'received'인 행에만 적용.
    __table_args__ = (
        Index(
            "uq_shop_tickets_active",
            "order_id", "action_type",
            unique=True,
            postgresql_where=text("status = 'received'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shop_users.id"), nullable=False, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shop_chat_sessions.id"), nullable=True, index=True
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shop_orders.id"), nullable=False
    )

    # "cancel" | "exchange" | "change"
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # 취소/교환 공통 사유
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # 취소 플로우만: "원결제 수단" | "포인트" 등
    refund_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 교환/변경 플로우: JSON 배열
    # - exchange: [{"item_id": int, "name": str, "qty": int}]
    # - change: [{"change_type": str, "change_detail": str}]
    items: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 운영팀 확인용 플래그. DB 컬럼은 Text이며, 값은 JSON 배열 형태로 직렬화해 저장합니다.
    # 예: [{"code": "high_value_review", "label": "5만원 이상 운영팀 우선 확인", "severity": "warning"}]
    flags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # "received" → "processing" → "completed" | "cancelled"
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="received")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── 관계 ──────────────────────────────────────────────────────────────────
    user = relationship("User", back_populates=None, lazy="raise")
    order = relationship("Order", back_populates=None, lazy="raise")

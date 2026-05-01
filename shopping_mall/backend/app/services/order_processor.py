"""주문 자동화 처리 서비스.

티켓 기반 자동 취소·재고 복구 로직을 캡슐화합니다.
OrderGraph(create_ticket 노드)와 CS 에이전트(cancel_order 도구) 양쪽에서 공유합니다.

설계 원칙:
- 모든 public 메서드는 staticmethod (상태 없음 — 의존성은 파라미터로 주입)
- commit 경계는 호출자가 결정 (apply_* 계열은 flush만 수행)
- create_* 계열만 자체 commit·refresh 포함 (원자성 보장)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.order import Order
    from app.models.ticket import ShopTicket

logger = logging.getLogger(__name__)

# 자동 취소 가능 주문 상태 (배송사 픽업 전)
AUTO_CANCEL_STATUSES: frozenset[str] = frozenset({"pending", "preparing"})

# 관리자 검토 필요 상태 (배송 시작 후)
ADMIN_REVIEW_STATUSES: frozenset[str] = frozenset({"shipped"})


class OrderProcessor:
    """주문 처리 자동화 서비스 (stateless — 모든 메서드 staticmethod)."""

    # ── 재고 복구 ──────────────────────────────────────────────────────────────

    @staticmethod
    def restore_stock(db: "Session", order_id: int) -> dict[int, int]:
        """주문 품목의 재고를 복구합니다.

        OrderItem을 일괄 조회하고 Product.stock을 quantity만큼 증가시킵니다.
        행 레벨 잠금(with_for_update)으로 동시 복구 시 경쟁 조건을 방지합니다.

        ④ 재고 임계값 자동 제어:
        복구 후 stock > 0 이고 is_available=False 이면 True로 자동 전환합니다.

        commit은 호출자가 수행합니다.

        Args:
            db: SQLAlchemy 세션
            order_id: 재고를 복구할 주문 ID

        Returns:
            {product_id: restored_qty} — 복구된 상품 목록 (로깅·감사용)
        """
        from app.models.order import OrderItem
        from app.models.product import Product

        items = db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
        if not items:
            logger.warning("[order_processor] 재고 복구 대상 OrderItem 없음: order=%d", order_id)
            return {}

        # 상품 ID 목록으로 행 잠금 일괄 획득 — N+1 방지
        product_ids = [item.product_id for item in items]
        products: dict[int, "Product"] = {
            p.id: p
            for p in (
                db.query(Product)
                .filter(Product.id.in_(product_ids))
                .with_for_update()
                .all()
            )
        }

        restored: dict[int, int] = {}
        for item in items:
            product = products.get(item.product_id)
            if product is None:
                logger.warning(
                    "[order_processor] 상품 조회 실패 — product=%d order=%d",
                    item.product_id, order_id,
                )
                continue

            before = product.stock
            product.stock += item.quantity
            restored[item.product_id] = item.quantity

            # ④ 재고 복구 후 is_available 자동 전환
            if product.stock > 0 and not product.is_available:
                product.is_available = True
                logger.info(
                    "[order_processor] 재고 복구 → is_available=True: product=%d stock=%d",
                    product.id, product.stock,
                )

            logger.debug(
                "[order_processor] 재고 복구 예정: product=%d %d → %d (+%d)",
                item.product_id, before, product.stock, item.quantity,
            )

        return restored

    # ── 자동 취소 (OrderGraph용) ───────────────────────────────────────────────

    @staticmethod
    def apply_auto_cancel(
        db: "Session",
        order: "Order",
        ticket: "ShopTicket",
    ) -> dict[int, int]:
        """자동 취소 처리: Order 상태 변경 + 재고 복구 + 티켓 완료 처리.

        OrderGraph의 create_ticket 노드 전용.
        호출 전 db.flush()로 ticket.id가 확보된 상태여야 합니다.
        commit은 호출자(create_ticket 노드)가 수행합니다.

        Args:
            db: SQLAlchemy 세션
            order: 취소할 Order 객체 (status가 AUTO_CANCEL_STATUSES 중 하나)
            ticket: 방금 생성된 ShopTicket 객체 (flush 완료, commit 전)

        Returns:
            restore_stock()의 결과 {product_id: restored_qty}

        Raises:
            ValueError: 취소 불가 상태의 주문인 경우
        """
        if order.status not in AUTO_CANCEL_STATUSES:
            raise ValueError(
                f"apply_auto_cancel: 취소 불가 상태 — order={order.id} status={order.status}"
            )

        order.status = "cancelled"
        ticket.status = "completed"

        restored = OrderProcessor.restore_stock(db, order.id)

        logger.info(
            "[order_processor] 자동 취소 적용(미커밋): order=%d ticket=%s restored=%s",
            order.id,
            getattr(ticket, "id", "?"),
            restored,
        )
        return restored

    # ── CS 에이전트용 단일 트랜잭션 취소 ──────────────────────────────────────

    @staticmethod
    def create_and_auto_cancel(
        db: "Session",
        user_id: int,
        order: "Order",
        reason: str,
        refund_method: str | None = None,
        session_id: int | None = None,
    ) -> "ShopTicket":
        """CS 에이전트용: 티켓 생성 + 즉시 자동 취소를 단일 트랜잭션으로 처리.

        OrderGraph 멀티스텝 플로우를 거치지 않고 CS 에이전트가 직접 취소를
        실행할 때 사용합니다 (사용자가 주문 ID와 이유를 한 번에 제공한 경우).

        Args:
            db: SQLAlchemy 세션
            user_id: 요청 사용자 ID (소유권 확인은 호출자 책임)
            order: 취소할 Order 객체 (status 검증 포함)
            reason: 취소 사유 문자열
            refund_method: 환불 방법 ('원결제 수단' | '포인트' 등). None이면 기본값 사용
            session_id: 채팅 세션 ID (없으면 None)

        Returns:
            생성·완료된 ShopTicket 객체 (status='completed')

        Raises:
            ValueError: 취소 불가 상태이거나 이미 received 티켓이 존재할 때
        """
        from app.models.ticket import ShopTicket

        if order.status not in AUTO_CANCEL_STATUSES:
            raise ValueError(
                f"create_and_auto_cancel: 자동 취소 불가 상태 — order={order.id} status={order.status}"
            )

        # 중복 활성 티켓 사전 검사 — 정책상 동일 주문에 received 티켓 하나만 허용
        existing = (
            db.query(ShopTicket)
            .filter(
                ShopTicket.order_id == order.id,
                ShopTicket.action_type == "cancel",
                ShopTicket.status == "received",
            )
            .first()
        )
        if existing:
            raise ValueError(
                f"이미 처리 대기 중인 취소 티켓이 있습니다: ticket_id={existing.id}"
            )

        ticket = ShopTicket(
            user_id=user_id,
            session_id=session_id,
            order_id=order.id,
            action_type="cancel",
            reason=reason,
            refund_method=refund_method or "원결제 수단",
            status="received",  # apply_auto_cancel이 completed로 변경
        )
        db.add(ticket)
        db.flush()  # ticket.id 확보 (commit 전)

        OrderProcessor.apply_auto_cancel(db, order, ticket)
        db.commit()
        db.refresh(ticket)

        logger.info(
            "[order_processor] CS 직접 취소 완료: ticket=%d order=%d user=%d restored_ok=%s",
            ticket.id,
            order.id,
            user_id,
            ticket.status == "completed",
        )
        return ticket

    # ── 반품 처리 ──────────────────────────────────────────────────────────────

    @staticmethod
    def apply_return(
        db: "Session",
        order: "Order",
    ) -> None:
        """반품 완료 처리: Order.status를 delivered → returned로 변경.

        admin.py의 update_ticket_status에서 교환/취소 티켓이 completed로
        전환될 때 호출됩니다. commit은 호출자가 수행합니다.

        Args:
            db: SQLAlchemy 세션
            order: 반품 처리할 Order 객체 (status == "delivered" 검증 포함)

        Raises:
            ValueError: delivered 상태가 아닌 주문인 경우
        """
        if order.status != "delivered":
            raise ValueError(
                f"apply_return: 반품 불가 상태 — order={order.id} status={order.status}"
            )

        order.status = "returned"
        logger.info(
            "[order_processor] 반품 완료 적용(미커밋): order=%d",
            order.id,
        )

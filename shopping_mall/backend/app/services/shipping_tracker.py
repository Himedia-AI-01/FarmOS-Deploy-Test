"""Simulated shipping status tracker."""
import json
from datetime import datetime
from typing import Optional

from app.core.datetime_utils import now_kst
from sqlalchemy.orm import Session

from app.models.shipment import Shipment


class ShippingTracker:
    """Simulate shipment status progression based on time since creation."""

    STATUS_FLOW = ["registered", "picked_up", "in_transit", "delivered"]

    @classmethod
    def check_status(cls, shipment: Shipment) -> str:
        """Simulate status progression based on days since creation.

        delivered 상태는 자동 전환하지 않습니다.
        배송 지연·내부 사정 등을 고려하여 배송 완료는 관리자가 직접 처리해야 합니다.
        자동 전환 최대 단계: in_transit
        """
        if shipment.status == "delivered":
            return "delivered"

        now = now_kst()
        if shipment.created_at is None:
            return shipment.status

        days_elapsed = (now - shipment.created_at).total_seconds() / 86400

        if days_elapsed >= 2:
            new_status = "in_transit"
        elif days_elapsed >= 1:
            new_status = "picked_up"
        else:
            new_status = "registered"

        return new_status

    @classmethod
    def update_shipment(cls, shipment: Shipment, db: Optional[Session] = None) -> bool:
        """Update a single shipment. Returns True if status changed.

        Args:
            shipment: 업데이트할 Shipment 객체
            db: SQLAlchemy 세션 — 전달 시 Shipment.status=delivered 전환에 맞춰
                Order.status(shipped → delivered)를 동기화합니다.
                None이면 Order 동기화를 건너뜁니다.
        """
        new_status = cls.check_status(shipment)
        if new_status == shipment.status:
            shipment.last_checked_at = now_kst()
            # check_status()는 in_transit까지만 자동 전환하므로 "delivered" 상태인
            # shipment는 항상 이 경로로 돌아온다.  외부에서 직접 delivered로 설정된
            # 경우에도 Order 동기화가 누락되지 않도록 여기서 방어적으로 처리한다.
            if new_status == "delivered" and db is not None:
                from app.models.order import Order
                order = db.query(Order).filter(Order.id == shipment.order_id).first()
                if order and order.status == "shipped":
                    order.status = "delivered"
            return False

        now = now_kst()
        old_status = shipment.status

        # Update tracking history
        history = []
        if shipment.tracking_history:
            try:
                history = json.loads(shipment.tracking_history)
            except (json.JSONDecodeError, TypeError):
                history = []

        history.append({
            "from": old_status,
            "to": new_status,
            "timestamp": now.isoformat(),
        })

        shipment.status = new_status
        shipment.tracking_history = json.dumps(history, ensure_ascii=False)
        shipment.last_checked_at = now

        if new_status == "delivered":
            shipment.delivered_at = now
            # Order 상태 동기화: shipped → delivered
            if db is not None:
                from app.models.order import Order
                order = db.query(Order).filter(Order.id == shipment.order_id).first()
                if order and order.status == "shipped":
                    order.status = "delivered"

        return True

    @classmethod
    def check_all(cls, db: Session) -> int:
        """Check and update all non-delivered shipments. Returns count of updated."""
        shipments = (
            db.query(Shipment)
            .filter(Shipment.status != "delivered")
            .all()
        )
        updated = 0
        for shipment in shipments:
            if cls.update_shipment(shipment, db=db):
                updated += 1

        if updated > 0:
            db.commit()
        return updated

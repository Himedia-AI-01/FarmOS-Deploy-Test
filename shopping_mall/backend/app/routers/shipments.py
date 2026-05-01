"""Shipment tracking router."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.ticket import ShopTicket
from app.schemas.shipment import ShipmentCreate, ShipmentResponse
from app.services.shipping_tracker import ShippingTracker

router = APIRouter(prefix="/api/shipments", tags=["shipments"])


@router.post("/", response_model=ShipmentResponse)
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db)):
    if body.related_ticket_id is not None:
        ticket = db.query(ShopTicket).filter(ShopTicket.id == body.related_ticket_id).first()
        if not ticket:
            raise HTTPException(status_code=404, detail="ShopTicket not found")
    shipment = Shipment(
        order_id=body.order_id,
        carrier=body.carrier,
        tracking_number=body.tracking_number,
        expected_arrival=body.expected_arrival,
        related_ticket_id=body.related_ticket_id,
    )
    db.add(shipment)
    db.flush()  # shipment.id 확보

    # preparing → shipped 자동 전환 (교환 배송 시 원 주문은 delivered → 조건 False로 안전)
    order = db.query(Order).filter(Order.id == body.order_id).first()
    if order and order.status == "preparing":
        order.status = "shipped"

    db.commit()
    db.refresh(shipment)
    return shipment


@router.get("/", response_model=List[ShipmentResponse])
def list_shipments(
    status: Optional[str] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    query = db.query(Shipment)
    if status:
        query = query.filter(Shipment.status == status)
    return query.order_by(Shipment.created_at.desc()).all()


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return shipment


@router.post("/{shipment_id}/check", response_model=ShipmentResponse)
def check_shipment_status(shipment_id: int, db: Session = Depends(get_db)):
    """Manually trigger a status check for a shipment."""
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    ShippingTracker.update_shipment(shipment)
    db.commit()
    db.refresh(shipment)
    return shipment

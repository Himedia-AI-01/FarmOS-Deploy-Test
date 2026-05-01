"""관리자 전용 라우터 — 티켓·챗봇 로그·배송 운영 데이터."""
import logging
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.chat_log import ChatLog
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.ticket import ShopTicket
from app.models.user import User
from app.services.shipping_tracker import ShippingTracker

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── 스키마 ──────────────────────────────────────────────────────────────────


class TicketResponse(BaseModel):
    id: int
    user_id: int
    session_id: Optional[int] = None
    order_id: int
    action_type: str
    reason: str
    refund_method: Optional[str] = None
    items: Optional[str] = None  # JSON 문자열 배열
    status: str
    created_at: datetime
    user_name: Optional[str] = None
    order_total: Optional[int] = None

    model_config = {"from_attributes": True}


class TicketStatusUpdate(BaseModel):
    status: str  # "received" | "processing" | "completed" | "cancelled"


class AdminChatLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    session_id: Optional[int] = None
    intent: str
    question: str
    answer: str
    escalated: bool
    rating: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminChatSessionResponse(BaseModel):
    id: int
    user_id: int
    title: Optional[str] = None
    status: str
    log_count: int
    last_question: Optional[str] = None
    last_message_at: Optional[datetime] = None
    has_escalation: bool
    # None=미처리 없음 / "received"=접수됨 / "processing"=처리중 (가장 심각한 상태)
    pending_ticket_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

_VALID_TICKET_STATUSES = {"received", "processing", "completed", "cancelled"}

# 허용된 상태 전환 — 종료 상태(completed, cancelled)에서는 전환 불가
_TICKET_ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    "received":   ["processing", "cancelled"],
    "processing": ["completed", "cancelled"],
    "completed":  [],
    "cancelled":  [],
}


def _enrich_ticket(t: ShopTicket, db: Session) -> TicketResponse:
    """lazy="raise" 관계를 별도 쿼리로 보완한다."""
    user = db.query(User).filter(User.id == t.user_id).first()
    order = db.query(Order).filter(Order.id == t.order_id).first()
    return TicketResponse(
        id=t.id,
        user_id=t.user_id,
        session_id=t.session_id,
        order_id=t.order_id,
        action_type=t.action_type,
        reason=t.reason,
        refund_method=t.refund_method,
        items=t.items,
        status=t.status,
        created_at=t.created_at,
        user_name=user.name if user else None,
        order_total=order.total_price if order else None,
    )


# ── 티켓 엔드포인트 ──────────────────────────────────────────────────────────


@router.get("/tickets", response_model=List[TicketResponse])
def list_tickets(
    status: Optional[str] = Query(None, description="received | processing | completed | cancelled"),
    action_type: Optional[str] = Query(None, description="cancel | exchange"),
    user_id: Optional[int] = Query(None, description="특정 사용자의 티켓만 조회"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 교환·취소 티켓 목록을 반환합니다."""
    q = db.query(ShopTicket)
    if status:
        q = q.filter(ShopTicket.status == status)
    if action_type:
        q = q.filter(ShopTicket.action_type == action_type)
    if user_id is not None:
        q = q.filter(ShopTicket.user_id == user_id)
    tickets = q.order_by(desc(ShopTicket.created_at)).offset(offset).limit(limit).all()
    if not tickets:
        return []

    # User / Order 일괄 조회 — 티켓 N건에 대해 각각 1회 IN 쿼리
    user_ids = list({t.user_id for t in tickets})
    order_ids = list({t.order_id for t in tickets})
    users_map: dict[int, User] = {
        u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
    }
    orders_map: dict[int, Order] = {
        o.id: o for o in db.query(Order).filter(Order.id.in_(order_ids)).all()
    }

    return [
        TicketResponse(
            id=t.id,
            user_id=t.user_id,
            session_id=t.session_id,
            order_id=t.order_id,
            action_type=t.action_type,
            reason=t.reason,
            refund_method=t.refund_method,
            items=t.items,
            status=t.status,
            created_at=t.created_at,
            user_name=users_map[t.user_id].name if t.user_id in users_map else None,
            order_total=orders_map[t.order_id].total_price if t.order_id in orders_map else None,
        )
        for t in tickets
    ]


@router.get("/tickets/stats")
def get_ticket_stats(db: Session = Depends(get_db)):
    """상태별 티켓 건수를 반환합니다."""
    rows = db.query(ShopTicket.status, ShopTicket.action_type).all()
    stats: dict = {
        "received": 0,
        "processing": 0,
        "completed": 0,
        "cancelled": 0,
        "total": 0,
        "exchange": 0,
        "cancel": 0,
    }
    for status, action_type in rows:
        stats["total"] += 1
        if status in stats:
            stats[status] += 1
        if action_type in stats:
            stats[action_type] += 1
    return stats


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    t = db.query(ShopTicket).filter(ShopTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="티켓을 찾을 수 없습니다.")
    return _enrich_ticket(t, db)


@router.patch("/tickets/{ticket_id}/status", response_model=TicketResponse)
def update_ticket_status(
    ticket_id: int,
    body: TicketStatusUpdate,
    db: Session = Depends(get_db),
):
    """티켓 상태를 변경합니다 (received → processing → completed | cancelled)."""
    if body.status not in _VALID_TICKET_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 상태: {body.status}. 허용값: {', '.join(_VALID_TICKET_STATUSES)}",
        )
    t = db.query(ShopTicket).filter(ShopTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="티켓을 찾을 수 없습니다.")
    allowed = _TICKET_ALLOWED_TRANSITIONS.get(t.status, [])
    if body.status not in allowed:
        allowed_str = ", ".join(allowed) if allowed else "없음 (종료 상태)"
        raise HTTPException(
            status_code=400,
            detail=f"'{t.status}' → '{body.status}' 전환은 허용되지 않습니다. 허용된 전환: {allowed_str}",
        )
    t.status = body.status

    # 교환/취소 티켓이 completed로 전환될 때 delivered 주문 → returned 자동 처리
    if body.status == "completed" and t.action_type in ("cancel", "exchange"):
        from app.services.order_processor import OrderProcessor
        order = db.query(Order).filter(Order.id == t.order_id).first()
        if order and order.status == "delivered":
            try:
                OrderProcessor.apply_return(db, order)
            except ValueError as e:
                logger.warning(
                    "[admin] apply_return 실패 — order_id=%s: %s", order.id, e
                )

    db.commit()
    db.refresh(t)
    return _enrich_ticket(t, db)


# ── 주문 상태 수동 전환 ──────────────────────────────────────────────────────


class AdminOrderStatusUpdate(BaseModel):
    status: str  # "preparing" | "cancelled"


# 관리자 허용 전환 — 자동 전환이 없는 단계만 수동으로 처리
_ADMIN_ORDER_TRANSITIONS: dict[str, list[str]] = {
    "pending":   ["preparing", "cancelled"],
    "preparing": ["cancelled"],
    "shipped":   ["cancelled"],  # 강제 취소 (배송 중 관리자 처리)
    "delivered": [],
    "cancelled": [],
    "returned":  [],
}


@router.patch("/orders/{order_id}/status")
def admin_update_order_status(
    order_id: int,
    body: AdminOrderStatusUpdate,
    db: Session = Depends(get_db),
):
    """관리자 수동 주문 상태 전환.

    허용 전환:
      pending   → preparing  (상품 준비 시작 확인)
      pending   → cancelled  (관리자 직접 취소)
      preparing → cancelled  (관리자 직접 취소)
      shipped   → cancelled  (강제 취소 — 배송 중 예외 처리)
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    allowed = _ADMIN_ORDER_TRANSITIONS.get(order.status, [])
    if body.status not in allowed:
        allowed_str = ", ".join(allowed) if allowed else "없음 (전환 불가 상태)"
        raise HTTPException(
            status_code=400,
            detail=f"'{order.status}' → '{body.status}' 전환은 허용되지 않습니다. 허용된 전환: {allowed_str}",
        )

    # pending / preparing 취소 시 재고 복구
    if body.status == "cancelled" and order.status in ("pending", "preparing"):
        from app.services.order_processor import OrderProcessor
        OrderProcessor.restore_stock(db, order.id)

    order.status = body.status
    db.commit()
    return {"order_id": order_id, "status": order.status}


# ── 배송 엔드포인트 ──────────────────────────────────────────────────────────


class RelatedTicketSummary(BaseModel):
    id: int
    action_type: str
    status: str
    reason: str

    model_config = {"from_attributes": True}


class AdminShipmentResponse(BaseModel):
    id: int
    order_id: int
    carrier: str
    tracking_number: str
    status: str
    expected_arrival: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    tracking_history: Optional[str] = None
    created_at: Optional[datetime] = None
    # 연관 정보
    order_total: Optional[int] = None
    related_ticket_id: Optional[int] = None  # 명시적 FK — None이면 원본 배송(교환 배송 아님)
    related_ticket: Optional[RelatedTicketSummary] = None

    model_config = {"from_attributes": True}


def _enrich_shipment(s: Shipment, db: Session) -> AdminShipmentResponse:
    order = db.query(Order).filter(Order.id == s.order_id).first()
    if s.related_ticket_id is not None:
        ticket = db.query(ShopTicket).filter(ShopTicket.id == s.related_ticket_id).first()
    else:
        ticket = (
            db.query(ShopTicket)
            .filter(
                ShopTicket.order_id == s.order_id,
                ShopTicket.action_type == "exchange",
            )
            .order_by(desc(ShopTicket.created_at))
            .first()
        )
    related = (
        RelatedTicketSummary(
            id=ticket.id,
            action_type=ticket.action_type,
            status=ticket.status,
            reason=ticket.reason,
        )
        if ticket
        else None
    )
    return AdminShipmentResponse(
        id=s.id,
        order_id=s.order_id,
        carrier=s.carrier,
        tracking_number=s.tracking_number,
        status=s.status,
        expected_arrival=s.expected_arrival,
        last_checked_at=s.last_checked_at,
        delivered_at=s.delivered_at,
        tracking_history=s.tracking_history,
        created_at=s.created_at,
        order_total=order.total_price if order else None,
        related_ticket_id=s.related_ticket_id,
        related_ticket=related,
    )


@router.get("/shipments", response_model=List[AdminShipmentResponse])
def admin_list_shipments(
    status: Optional[str] = Query(None, description="registered | picked_up | in_transit | delivered"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """배송 목록 + 연관 교환 티켓을 반환합니다."""
    q = db.query(Shipment)
    if status:
        q = q.filter(Shipment.status == status)
    shipments = q.order_by(desc(Shipment.created_at)).offset(offset).limit(limit).all()

    if not shipments:
        return []

    # Order / ShopTicket 일괄 조회 — 배송 N건에 대해 각각 1회씩
    order_ids = list({s.order_id for s in shipments})
    orders_map: dict[int, Order] = {
        o.id: o for o in db.query(Order).filter(Order.id.in_(order_ids)).all()
    }

    explicit_ticket_ids = [s.related_ticket_id for s in shipments if s.related_ticket_id is not None]
    explicit_tickets_map: dict[int, ShopTicket] = {}
    if explicit_ticket_ids:
        explicit_tickets_map = {
            t.id: t for t in db.query(ShopTicket).filter(ShopTicket.id.in_(explicit_ticket_ids)).all()
        }

    # related_ticket_id가 없는 배송은 order_id 기준 최신 exchange 티켓 조회 (1회 IN 쿼리)
    implicit_order_ids = [s.order_id for s in shipments if s.related_ticket_id is None]
    implicit_tickets_map: dict[int, ShopTicket] = {}
    if implicit_order_ids:
        # 각 order_id당 가장 최근 exchange 티켓 — row_number로 선택
        inner = (
            db.query(
                ShopTicket,
                func.row_number().over(
                    partition_by=ShopTicket.order_id,
                    order_by=desc(ShopTicket.created_at),
                ).label("rn"),
            )
            .filter(
                ShopTicket.order_id.in_(implicit_order_ids),
                ShopTicket.action_type == "exchange",
            )
            .subquery()
        )
        from sqlalchemy.orm import aliased
        TicketAlias = aliased(ShopTicket, inner)
        implicit_tickets_map = {
            t.order_id: t
            for t in db.query(TicketAlias).filter(inner.c.rn == 1).all()
        }

    results = []
    for s in shipments:
        if s.related_ticket_id is not None:
            ticket = explicit_tickets_map.get(s.related_ticket_id)
        else:
            ticket = implicit_tickets_map.get(s.order_id)

        order = orders_map.get(s.order_id)
        related = (
            RelatedTicketSummary(
                id=ticket.id,
                action_type=ticket.action_type,
                status=ticket.status,
                reason=ticket.reason,
            )
            if ticket
            else None
        )
        results.append(AdminShipmentResponse(
            id=s.id,
            order_id=s.order_id,
            carrier=s.carrier,
            tracking_number=s.tracking_number,
            status=s.status,
            expected_arrival=s.expected_arrival,
            last_checked_at=s.last_checked_at,
            delivered_at=s.delivered_at,
            tracking_history=s.tracking_history,
            created_at=s.created_at,
            order_total=order.total_price if order else None,
            related_ticket_id=s.related_ticket_id,
            related_ticket=related,
        ))
    return results


@router.post("/shipments/{shipment_id}/check", response_model=AdminShipmentResponse)
def admin_check_shipment(shipment_id: int, db: Session = Depends(get_db)):
    """배송 상태를 수동으로 업데이트합니다."""
    s = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="배송을 찾을 수 없습니다.")
    ShippingTracker.update_shipment(s, db=db)
    db.commit()
    db.refresh(s)
    return _enrich_shipment(s, db)


# ── 챗봇 로그 엔드포인트 (전체 사용자) ─────────────────────────────────────────


@router.get("/chatbot/logs", response_model=List[AdminChatLogResponse])
def admin_list_chat_logs(
    intent: Optional[str] = Query(None),
    escalated: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 사용자의 챗봇 로그를 반환합니다."""
    q = db.query(ChatLog)
    if intent:
        q = q.filter(ChatLog.intent == intent)
    if escalated is not None:
        q = q.filter(ChatLog.escalated == escalated)
    return q.order_by(desc(ChatLog.created_at)).offset(offset).limit(limit).all()


@router.get("/chatbot/logs/escalated", response_model=List[AdminChatLogResponse])
def admin_list_escalated_logs(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """에스컬레이션된 모든 챗봇 로그를 반환합니다."""
    return (
        db.query(ChatLog)
        .filter(ChatLog.escalated.is_(True))
        .order_by(desc(ChatLog.created_at))
        .limit(limit)
        .all()
    )


@router.get("/chatbot/sessions", response_model=List[AdminChatSessionResponse])
def admin_list_chat_sessions(
    escalated_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 챗 세션 목록 (최근 업데이트 순)."""
    from app.models.chat_session import ChatSession

    sessions_q = db.query(ChatSession)
    if escalated_only:
        # DB 레벨에서 필터링 — offset/limit 전에 적용해야 페이지네이션이 정확함
        sessions_q = (
            sessions_q
            .join(ChatLog, ChatLog.session_id == ChatSession.id)
            .filter(ChatLog.escalated.is_(True))
            .distinct()
        )
    sessions = (
        sessions_q
        .order_by(desc(ChatSession.updated_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    if not sessions:
        return []

    session_ids = [s.id for s in sessions]

    # ── 집계 1: log_count + has_escalation (ChatLog 1회 IN 쿼리) ──────────────
    from sqlalchemy import case
    agg_rows = (
        db.query(
            ChatLog.session_id,
            func.count(ChatLog.id).label("log_count"),
            func.max(case((ChatLog.escalated == True, 1), else_=0)).label("has_esc"),
        )
        .filter(ChatLog.session_id.in_(session_ids))
        .group_by(ChatLog.session_id)
        .all()
    )
    log_count_map: dict[int, int] = {r.session_id: r.log_count for r in agg_rows}
    has_esc_map: dict[int, bool] = {r.session_id: bool(r.has_esc) for r in agg_rows}

    # ── 집계 2: 세션별 마지막 로그 (row_number 서브쿼리 1회) ───────────────────
    last_log_inner = (
        db.query(
            ChatLog.session_id,
            ChatLog.question,
            ChatLog.created_at,
            func.row_number().over(
                partition_by=ChatLog.session_id,
                order_by=desc(ChatLog.created_at),
            ).label("rn"),
        )
        .filter(ChatLog.session_id.in_(session_ids))
        .subquery()
    )
    last_log_rows = db.query(last_log_inner).filter(last_log_inner.c.rn == 1).all()
    last_question_map: dict[int, Optional[str]] = {r.session_id: r.question for r in last_log_rows}
    last_at_map: dict[int, Optional[datetime]] = {r.session_id: r.created_at for r in last_log_rows}

    # ── 집계 3: 세션별 pending 티켓 상태 (ShopTicket 1회 IN 쿼리) ─────────────
    ticket_rows = (
        db.query(ShopTicket.session_id, ShopTicket.status)
        .filter(
            ShopTicket.session_id.in_(session_ids),
            ShopTicket.status.in_(["received", "processing"]),
        )
        .all()
    )
    ticket_status_map: dict[int, set] = {}
    for row in ticket_rows:
        ticket_status_map.setdefault(row.session_id, set()).add(row.status)

    result = []
    for s in sessions:
        sid = s.id
        statuses = ticket_status_map.get(sid, set())
        pending_ticket_status: Optional[str] = None
        if statuses:
            pending_ticket_status = "processing" if "processing" in statuses else "received"

        result.append(AdminChatSessionResponse(
            id=sid,
            user_id=s.user_id,
            title=s.title,
            status=s.status,
            log_count=log_count_map.get(sid, 0),
            last_question=last_question_map.get(sid),
            last_message_at=last_at_map.get(sid),
            has_escalation=escalated_only or has_esc_map.get(sid, False),
            pending_ticket_status=pending_ticket_status,
            created_at=s.created_at,
            updated_at=s.updated_at,
        ))
    return result


@router.get("/chatbot/sessions/{session_id}/logs", response_model=List[AdminChatLogResponse])
def admin_get_session_logs(
    session_id: int,
    db: Session = Depends(get_db),
):
    """특정 세션의 모든 챗봇 로그를 시간 순으로 반환합니다."""
    from app.models.chat_session import ChatSession

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return (
        db.query(ChatLog)
        .filter(ChatLog.session_id == session_id)
        .order_by(ChatLog.created_at.asc())
        .all()
    )

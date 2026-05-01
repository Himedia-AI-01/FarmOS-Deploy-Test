"""OrderGraph 노드 함수들.

각 노드는 config["configurable"]["db"]로 DB Session을 주입받습니다.
interrupt()를 호출하는 노드는 재실행 시 DB 조회를 다시 수행합니다 (읽기 전용이므로 안전).
"""
import json
import logging
import re
from datetime import datetime, timezone

from langgraph.types import interrupt, RunnableConfig

from .state import OrderState
from .prompts import (
    ORDER_PROMPTS,
    CANCEL_KEYWORDS,
    HARD_CANCEL_KEYWORDS,
    CONFIRM_KEYWORDS,
    CANCEL_REASON_MAP,
    EXCHANGE_REASON_MAP,
    REFUND_METHOD_MAP,
)

logger = logging.getLogger(__name__)


# ── 상수 ───────────────────────────────────────────────────────────────────────

# 취소 가능 상태: 아직 배송사에 픽업되지 않은 주문
CANCELLABLE_STATUSES: frozenset[str] = frozenset({"pending", "preparing"})
# 교환 가능 상태: 수령 완료된 주문
EXCHANGEABLE_STATUSES: frozenset[str] = frozenset({"delivered"})

_STATUS_DISPLAY: dict[str, str] = {
    "pending":   "주문 접수",
    "preparing": "상품 준비 중",
    "shipped":   "배송 중",
    "delivered": "배송 완료",
    "cancelled": "취소 완료",
    "returned":  "반품 완료",
}


# ── 헬퍼 함수 ──────────────────────────────────────────────────────────────────

def _get_db(config: RunnableConfig):
    return config["configurable"]["db"]


def _is_cancel_intent(text: str) -> bool:
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in CANCEL_KEYWORDS)


def _is_hard_cancel_intent(text: str) -> bool:
    """명시적 흐름 중단 의도 — 단순 '아니오'/'아니요'는 해당되지 않음."""
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in HARD_CANCEL_KEYWORDS)


def _is_flow_abort_intent(text: str, action: str) -> bool:
    """흐름 중단 의도 판별 — 액션 컨텍스트를 반영.

    취소 플로우(action='cancel')에서는 '취소'를 중단 키워드에서 제외합니다.
    '취소 사유', '카드 취소', '배송 취소 때문에' 등 취소 플로우의 정상 응답에
    '취소'가 포함되더라도 흐름 중단으로 오탐하지 않습니다.
    교환 플로우에서는 CANCEL_KEYWORDS 전체를 그대로 사용합니다.
    """
    keywords = CANCEL_KEYWORDS - {"취소"} if action == "cancel" else CANCEL_KEYWORDS
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in keywords)


def _is_confirm_intent(text: str) -> bool:
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in CONFIRM_KEYWORDS)


def _build_order_summaries(db, orders: list) -> dict[int, str]:
    """주문 목록에 대한 요약 문자열을 일괄 조회로 생성.

    N+1 방지: 주문 ID 목록으로 OrderItem·Product를 각 1회 쿼리.

    Returns:
        {order_id: "상품명 외 N건"} 형태의 딕셔너리
    """
    from app.models.order import OrderItem
    from app.models.product import Product

    order_ids = [o.id for o in orders]

    # 주문별 전체 품목 일괄 조회
    all_items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id.in_(order_ids))
        .all()
    )

    # order_id → items 그룹핑
    items_by_order: dict[int, list] = {oid: [] for oid in order_ids}
    product_ids: set[int] = set()
    for item in all_items:
        items_by_order[item.order_id].append(item)
        product_ids.add(item.product_id)

    # 필요한 상품 정보 일괄 조회
    products: dict[int, str] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p.name

    summaries: dict[int, str] = {}
    for order in orders:
        items = items_by_order.get(order.id, [])
        if not items:
            summaries[order.id] = "주문 상품"
            continue
        first_name = products.get(items[0].product_id, "상품")
        suffix = f" 외 {len(items) - 1}건" if len(items) > 1 else ""
        summaries[order.id] = f"{first_name}{suffix}"

    return summaries


def _parse_order_selection(text: str, orders: list) -> int | None:
    """사용자 입력에서 주문 ID 파싱. 번호(1~N) 또는 주문 ID 직접 입력."""
    text = text.strip()
    # 숫자만 추출
    nums = re.findall(r"\d+", text)
    if not nums:
        return None
    n = int(nums[0])
    # 1~N 범위 번호인지 확인
    if 1 <= n <= len(orders):
        return orders[n - 1].id
    # 직접 주문 ID인지 확인
    for order in orders:
        if order.id == n:
            return order.id
    return None


_MAX_REASON_LENGTH = 200


def _parse_reason(text: str, reason_map: dict[str, str]) -> str:
    """번호 또는 직접 입력 사유 파싱."""
    text = text.strip()
    if text in reason_map:
        return reason_map[text]
    # 앞 숫자 추출 시도
    m = re.match(r"^(\d+)", text)
    if m and m.group(1) in reason_map:
        return reason_map[m.group(1)]
    # 그대로 사유로 사용 (직접 입력) — 길이 제한 적용
    return text[:_MAX_REASON_LENGTH] if text else "기타"


def _parse_refund_method(text: str) -> str:
    text = text.strip()
    if text in REFUND_METHOD_MAP:
        return REFUND_METHOD_MAP[text]
    if "1" in text or "원결제" in text or "카드" in text:
        return REFUND_METHOD_MAP["1"]
    if "2" in text or "적립" in text or "포인트" in text:
        return REFUND_METHOD_MAP["2"]
    return REFUND_METHOD_MAP["1"]  # 기본값


# "N번 [상품] [M개]" — 품목 인덱스는 "번"으로 명시적으로 구분,
# 수량은 동일 토큰 내 "M개"에서만 추출 (전역 re.findall로 혼용하지 않음)
_ITEM_SELECTION_RE = re.compile(
    r"(\d+)\s*번(?:\s*상품)?(?:[^\d]*?(\d+)\s*개)?"
)


def _parse_item_selections(user_input: str, order_items: list, db) -> list:
    """교환 품목 선택 입력 파싱.

    반환: selected items 리스트 (비어 있으면 파싱 실패).

    규칙:
      - "전체" 단독 (N번 없음) → 모든 품목 전량
      - "N번 [상품] [전체|M개]" → 품목 N, 수량 M 또는 전량
      - 번호와 수량을 같은 매치 토큰에서 추출해 혼용 방지
    """
    from app.models.product import Product

    # 관련 상품 정보 일괄 조회 — N+1 방지
    product_ids = {oi.product_id for oi in order_items}
    product_names: dict[int, str] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            product_names[p.id] = p.name

    selected: list = []

    # "전체" 단독 입력 — "N번 상품 전체"는 아래 N번 패턴으로 처리
    if "전체" in user_input and not re.search(r"\d+\s*번", user_input):
        for oi in order_items:
            name = product_names.get(oi.product_id, f"상품 #{oi.product_id}")
            selected.append({"item_id": oi.id, "product_id": oi.product_id, "name": name, "qty": oi.quantity})
        return selected

    # "N번 [상품] [M개]" 패턴 — 수량이 없거나 "전체" → 전량
    for m in _ITEM_SELECTION_RE.finditer(user_input):
        idx = int(m.group(1))
        if 1 <= idx <= len(order_items):
            oi = order_items[idx - 1]
            name = product_names.get(oi.product_id, f"상품 #{oi.product_id}")
            qty = min(int(m.group(2)), oi.quantity) if m.group(2) else oi.quantity
            selected.append({"item_id": oi.id, "product_id": oi.product_id, "name": name, "qty": qty})

    return selected


# ── 노드 함수 ─────────────────────────────────────────────────────────────────

async def route_action(state: OrderState, config: RunnableConfig) -> dict:
    """취소/교환 분기 라우팅용 passthrough 노드."""
    return state


async def list_orders(state: OrderState, config: RunnableConfig) -> dict:
    """취소/교환 가능한 주문 목록 조회 → interrupt로 선택 대기.

    - 취소: pending / preparing 상태만 (배송 픽업 전)
    - 교환: delivered 상태만 (수령 완료)
    """
    from app.models.order import Order

    db = _get_db(config)

    eligible_statuses = (
        CANCELLABLE_STATUSES if state["action"] == "cancel" else EXCHANGEABLE_STATUSES
    )
    no_orders_key = (
        "no_cancellable_orders" if state["action"] == "cancel" else "no_exchangeable_orders"
    )

    orders = (
        db.query(Order)
        .filter(
            Order.user_id == state["user_id"],
            Order.status.in_(eligible_statuses),
        )
        .order_by(Order.created_at.desc())
        .limit(5)
        .all()
    )

    if not orders:
        return {
            **state,
            "abort": True,
            "response": ORDER_PROMPTS[no_orders_key],
            "is_pending": False,
        }

    # 일괄 조회로 N+1 방지
    summaries = _build_order_summaries(db, orders)
    order_lines = []
    for i, o in enumerate(orders):
        date_str = o.created_at.strftime("%Y-%m-%d")
        status_display = _STATUS_DISPLAY.get(o.status, o.status)
        order_lines.append(
            f"{i + 1}) 주문 번호 #{o.id}\n"
            f"   · 상품: {summaries[o.id]}\n"
            f"   · 주문일: {date_str}\n"
            f"   · 상태: {status_display}"
        )
    order_list = "\n\n".join(order_lines)

    prompt_key = "select_order_cancel" if state["action"] == "cancel" else "select_order_exchange"
    prompt = ORDER_PROMPTS[prompt_key].format(order_list=order_list)

    # ── interrupt: 사용자 주문 선택 대기 ──────────────────────────────────
    user_input = interrupt(prompt)

    if _is_flow_abort_intent(user_input, state["action"]):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    # 단일 주문일 때 긍정 응답("응", "네", "진행해줘" 등) → 유일한 주문 자동 선택
    if len(orders) == 1 and _is_confirm_intent(user_input):
        order_id = orders[0].id
    else:
        order_id = _parse_order_selection(user_input, orders)

    if order_id is None:
        # 한 번 더 물어보기
        retry_prompt = ORDER_PROMPTS["invalid_order_selection"].format(order_list=order_list)
        user_input = interrupt(retry_prompt)
        if _is_flow_abort_intent(user_input, state["action"]):
            return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}
        if len(orders) == 1 and _is_confirm_intent(user_input):
            order_id = orders[0].id
        else:
            order_id = _parse_order_selection(user_input, orders)

    if order_id is None:
        return {
            **state,
            "abort": True,
            "response": "주문을 확인하지 못했습니다. 처음부터 다시 시도해 주세요.",
            "is_pending": False,
        }

    # 선택된 주문 표시명 생성 (summaries는 이미 일괄 조회된 상태)
    selected_order = next(o for o in orders if o.id == order_id)
    order_display = (
        f"주문 번호 #{order_id} · {summaries[order_id]} · 주문일 {selected_order.created_at.strftime('%Y-%m-%d')}"
    )

    return {**state, "order_id": order_id, "order_display": order_display}


async def select_items(state: OrderState, config: RunnableConfig) -> dict:
    """교환 플로우: 교환 품목 선택 → interrupt."""
    from app.models.order import OrderItem
    from app.models.product import Product

    db = _get_db(config)
    order_items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == state["order_id"])
        .all()
    )

    if not order_items:
        return {**state, "abort": True, "response": "해당 주문의 상품을 조회할 수 없습니다.", "is_pending": False}

    # 상품명 일괄 조회 — N+1 방지
    product_ids = {oi.product_id for oi in order_items}
    product_names: dict[int, str] = {
        p.id: p.name
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }

    item_lines = []
    for i, oi in enumerate(order_items):
        name = product_names.get(oi.product_id, f"상품 #{oi.product_id}")
        item_lines.append(f"{i + 1}. {name} × {oi.quantity}개")
    item_list = "\n".join(item_lines)

    prompt = ORDER_PROMPTS["select_items"].format(
        order_display=state["order_display"],
        item_list=item_list,
    )

    # ── interrupt: 교환 품목 선택 대기 ────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_flow_abort_intent(user_input, state["action"]):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    # 사용자 입력 파싱 — 엄격한 "N번" 앵커 사용, 수량은 동일 토큰에서만 추출
    selected = _parse_item_selections(user_input, order_items, db)

    if not selected:
        # 유효한 품목 번호를 찾지 못한 경우: 자동 전체 선택 대신 한 번 재입력 요청
        retry_prompt = (
            "선택하신 품목을 확인하지 못했습니다.\n\n"
            f"{item_list}\n\n"
            "번호로 다시 알려주세요 (예: 1번 상품 2개, 1번 상품 전체).\n"
            "진행을 중단하려면 '그만'이라고 입력하세요."
        )
        user_input = interrupt(retry_prompt)
        if _is_flow_abort_intent(user_input, state["action"]):
            return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}
        selected = _parse_item_selections(user_input, order_items, db)

    if not selected:
        return {
            **state,
            "abort": True,
            "response": "품목 선택을 확인하지 못했습니다. 처음부터 다시 시도해 주세요.",
            "is_pending": False,
        }

    return {**state, "selected_items": selected}


async def check_stock(state: OrderState, config: RunnableConfig) -> dict:
    """교환 품목 재고 확인 (자동 — interrupt 없음).

    재고 부족 시 수량 조정 안내를 응답에 포함하고 진행합니다.
    오피스 팀에서 최종 처리 시 재확인하므로 챗봇은 안내만 합니다.
    """
    from app.models.product import Product

    db = _get_db(config)

    # 재고 일괄 조회 — N+1 방지
    product_ids = {item["product_id"] for item in state["selected_items"]}
    stock_map: dict[int, int] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            stock_map[p.id] = p.stock

    notes = []
    for item in state["selected_items"]:
        stock = stock_map.get(item["product_id"])
        if stock is not None and stock < item["qty"]:
            if stock == 0:
                notes.append(f"• {item['name']}: 현재 재고 없음 (접수는 가능하며 오피스 확인 후 처리됩니다)")
            else:
                notes.append(f"• {item['name']}: 현재 재고 {stock}개 (요청 {item['qty']}개)")

    # 재고 노트는 summary에서 보여주므로 state에만 저장
    stock_note = "\n".join(notes) if notes else ""
    return {**state, "stock_note": stock_note}


async def get_reason(state: OrderState, config: RunnableConfig) -> dict:
    """교환/취소 사유 선택 → interrupt."""
    prompt_key = "cancel_reason" if state["action"] == "cancel" else "exchange_reason"
    reason_map = CANCEL_REASON_MAP if state["action"] == "cancel" else EXCHANGE_REASON_MAP
    prompt = ORDER_PROMPTS[prompt_key]

    # ── interrupt: 사유 입력 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_flow_abort_intent(user_input, state["action"]):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    reason = _parse_reason(user_input, reason_map)
    return {**state, "reason": reason}


async def get_refund_method(state: OrderState, config: RunnableConfig) -> dict:
    """취소 플로우만: 환불 방법 선택 → interrupt."""
    prompt = ORDER_PROMPTS["refund_method"]

    # ── interrupt: 환불 방법 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_flow_abort_intent(user_input, state["action"]):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    refund_method = _parse_refund_method(user_input)
    return {**state, "refund_method": refund_method}


_MAX_CONFIRMATION_ATTEMPTS = 3


async def show_summary(state: OrderState, config: RunnableConfig) -> dict:
    """최종 내용 요약 → interrupt로 최종 승인 대기.

    confirmation_attempts가 _MAX_CONFIRMATION_ATTEMPTS를 초과하면
    무한 루프 방지를 위해 플로우를 강제 중단합니다.
    """
    attempts = state.get("confirmation_attempts", 0) + 1
    if attempts > _MAX_CONFIRMATION_ATTEMPTS:
        logger.warning(
            "[order_graph] show_summary 최대 재확인 횟수 초과 — 플로우 강제 중단 (user=%s)",
            state.get("user_id"),
        )
        return {
            **state,
            "abort": True,
            "confirmation_attempts": attempts,
            "response": "확인이 어려워 처리를 중단했습니다. 다시 시도하시려면 처음부터 말씀해 주세요.",
            "is_pending": False,
        }

    if state["action"] == "cancel":
        prompt = ORDER_PROMPTS["cancel_summary"].format(
            order_display=state.get("order_display", ""),
            reason=state.get("reason", ""),
            refund_method=state.get("refund_method", "원결제 수단 환불"),
        )
    else:
        items_display = "\n".join(
            f"  • {item['name']} × {item['qty']}개"
            for item in state.get("selected_items", [])
        )
        stock_note = state.get("stock_note", "")
        if stock_note:
            items_display += f"\n\n재고 안내:\n{stock_note}"

        prompt = ORDER_PROMPTS["exchange_summary"].format(
            order_display=state.get("order_display", ""),
            items_display=items_display,
            reason=state.get("reason", ""),
        )

    # ── interrupt: 최종 승인 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    # 명시적 중단("그만", "취소" 등)만 abort로 처리.
    # 단순 "아니오"/"아니요"는 abort가 아닌 confirmed=False로 처리하여 재확인 유도.
    is_hard_cancel = _is_hard_cancel_intent(user_input)
    confirmed = _is_confirm_intent(user_input) and not _is_flow_abort_intent(user_input, state["action"])
    return {**state, "confirmed": confirmed, "abort": is_hard_cancel, "confirmation_attempts": attempts}


async def create_ticket(state: OrderState, config: RunnableConfig) -> dict:
    """티켓 발행 — DB INSERT + 정책 기반 자동 처리.

    취소(cancel) + 배송 전(pending/preparing):
      → OrderProcessor.apply_auto_cancel(): Order.status=cancelled + 재고 복구 + ticket.status=completed
      → 단일 트랜잭션 commit
      → 응답: auto_cancelled

    배송 중 취소 또는 교환 접수:
      → ticket.status=received 유지 (관리자 검토 대기)
      → 응답: admin_pending_cancel / ticket_created
    """
    from app.models.ticket import ShopTicket
    from app.models.order import Order
    from app.services.order_processor import OrderProcessor, AUTO_CANCEL_STATUSES
    from sqlalchemy.exc import IntegrityError

    db = _get_db(config)

    # 소유권 재검증 — defense-in-depth (list_orders에서 이미 필터했으나 재확인)
    order = db.query(Order).filter(
        Order.id == state["order_id"],
        Order.user_id == state["user_id"],
    ).first()
    if not order:
        logger.warning(
            f"[order_graph] 소유권 검증 실패: user={state['user_id']} order={state['order_id']}"
        )
        return {**state, "response": "주문 정보를 확인할 수 없습니다.", "is_pending": False}

    # 상태 재검증 — interrupt 대기 중 주문 상태가 변경되었을 수 있음 (defense-in-depth)
    eligible = CANCELLABLE_STATUSES if state["action"] == "cancel" else EXCHANGEABLE_STATUSES
    if order.status not in eligible:
        logger.warning(
            "[order_graph] 상태 재검증 실패: user=%s order=%s status=%s action=%s",
            state["user_id"], state["order_id"], order.status, state["action"],
        )
        action_ko = "취소" if state["action"] == "cancel" else "교환"
        status_display = _STATUS_DISPLAY.get(order.status, order.status)
        return {
            **state,
            "response": f"현재 주문 상태({status_display})에서는 {action_ko}가 불가합니다.",
            "is_pending": False,
        }

    # 멱등성 검사 — 이미 접수된 티켓이 있으면 새로 INSERT하지 않고 재사용
    _active_filter = [
        ShopTicket.user_id == state["user_id"],
        ShopTicket.order_id == state["order_id"],
        ShopTicket.action_type == state["action"],
        ShopTicket.status == "received",
    ]
    existing = db.query(ShopTicket).filter(*_active_filter).first()
    if existing:
        logger.info(
            "[order_graph] 기존 티켓 재사용: #%s user=%s action=%s order=%s",
            existing.id, state["user_id"], state["action"], state["order_id"],
        )
        action_ko = "취소" if state["action"] == "cancel" else "교환"
        response = ORDER_PROMPTS["ticket_unchanged"].format(
            action=action_ko, ticket_id=existing.id
        )
        return {**state, "ticket_id": existing.id, "response": response, "is_pending": False}

    # ── 자동 취소 가능 여부 판단 (INSERT 전에 결정 — 트랜잭션 설계 명확화) ─────
    is_auto_cancel = (
        state["action"] == "cancel"
        and order.status in AUTO_CANCEL_STATUSES
    )

    items_json = json.dumps(state.get("selected_items", []), ensure_ascii=False) or None

    ticket = ShopTicket(
        user_id=state["user_id"],
        session_id=state.get("session_id"),
        order_id=state["order_id"],
        action_type=state["action"],
        reason=state.get("reason") or "기타",
        refund_method=state.get("refund_method"),
        items=items_json if state["action"] == "exchange" else None,
        status="received",
    )
    db.add(ticket)
    try:
        db.flush()  # ticket.id 확보 (apply_auto_cancel 내부에서 참조)

        if is_auto_cancel:
            # 단일 트랜잭션: Order.status=cancelled + 재고 복구 + ticket.status=completed
            OrderProcessor.apply_auto_cancel(db, order, ticket)

        db.commit()
        db.refresh(ticket)
        logger.info(
            "[order_graph] 티켓 발행: #%s user=%s action=%s order=%s auto_cancel=%s",
            ticket.id, state["user_id"], state["action"], state["order_id"], is_auto_cancel,
        )
    except IntegrityError:
        # 동시 요청이 체크 이후 먼저 커밋한 경우 — 롤백 후 기존 티켓 재사용
        db.rollback()
        ticket = db.query(ShopTicket).filter(*_active_filter).first()
        if ticket is None:
            raise
        logger.info(
            "[order_graph] 동시 삽입 충돌 — 기존 티켓 재사용: #%s user=%s order=%s",
            ticket.id, state["user_id"], state["order_id"],
        )
        is_auto_cancel = False  # 재사용 경로에서는 자동 취소 미적용

    # ── 응답 메시지 분기 ────────────────────────────────────────────────────
    if is_auto_cancel:
        response = ORDER_PROMPTS["auto_cancelled"].format(ticket_id=ticket.id)
    elif state["action"] == "cancel":
        # 배송 중 취소 — 관리자 검토 대기
        response = ORDER_PROMPTS["admin_pending_cancel"].format(ticket_id=ticket.id)
    else:
        # 교환 접수
        response = ORDER_PROMPTS["ticket_created"].format(ticket_id=ticket.id)

    return {**state, "ticket_id": ticket.id, "response": response, "is_pending": False}


async def handle_flow_cancel(state: OrderState, config: RunnableConfig) -> dict:
    """플로우 중단 처리 (사용자 취소 또는 오류)."""
    # response가 이미 설정된 경우 그대로 사용
    response = state.get("response") or ORDER_PROMPTS["flow_cancelled"]
    return {**state, "response": response, "is_pending": False}


# ── 조건부 라우팅 함수 ─────────────────────────────────────────────────────────

def route_after_list_orders(state: OrderState) -> str:
    """list_orders 이후 분기."""
    if state.get("abort"):
        return "handle_flow_cancel"
    return "select_items" if state["action"] == "exchange" else "get_reason"


def route_after_get_reason(state: OrderState) -> str:
    """get_reason 이후 분기."""
    if state.get("abort"):
        return "handle_flow_cancel"
    return "get_refund_method" if state["action"] == "cancel" else "show_summary"


def route_after_show_summary(state: OrderState) -> str:
    """show_summary 이후 분기.

    - abort(명시적 중단) → handle_flow_cancel
    - confirmed → create_ticket
    - 단순 거절("아니오" 등) → show_summary 재확인
    """
    if state.get("abort"):
        return "handle_flow_cancel"
    if state.get("confirmed"):
        return "create_ticket"
    return "show_summary"


def route_abort_check(state: OrderState) -> str:
    """범용 abort 체크 (select_items, check_stock, get_refund_method 이후)."""
    return "handle_flow_cancel" if state.get("abort") else "__continue__"

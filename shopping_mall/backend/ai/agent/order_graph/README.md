# ai/agent/order_graph/

LangGraph `StateGraph` 기반 취소·교환 멀티스텝 HitL 플로우.  
`USE_MULTI_AGENT=true`일 때만 활성화됩니다.

---

## 왜 LangGraph인가

취소/교환은 여러 턴에 걸친 대화가 필요합니다.

```
사용자: "주문 취소하고 싶어"
봇: "어떤 주문인가요? [주문 목록]"
사용자: "2번"
봇: "취소 사유를 선택해주세요. [1.단순변심 2.배송지연 ...]"
사용자: "1"
봇: "환불 방법을 선택해주세요. [1.원결제수단 2.포인트]"
사용자: "1"
봇: "확인해주세요. [요약] 진행하시겠어요?"
사용자: "네"
봇: "티켓 #42 발행 완료"
```

기존 단일 에이전트의 `pending_action` 컬럼은 1단계 확인만 지원했습니다.  
LangGraph의 `checkpointer`가 **그래프 상태 전체**를 PostgreSQL에 저장하므로  
임의의 단계에서 중단하고, 다음 요청에서 정확히 그 지점부터 재개할 수 있습니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `state.py` | `OrderState` TypedDict — 그래프 전체 생애 동안 누적되는 상태 |
| `nodes.py` | 노드 함수 + 조건부 라우팅 함수 |
| `graph.py` | `build_order_graph(checkpointer)` — StateGraph 컴파일 |
| `prompts.py` | `ORDER_PROMPTS` 딕셔너리, `CANCEL_KEYWORDS`, `CONFIRM_KEYWORDS`, reason/method 맵 |

---

## OrderState 스키마

```python
class OrderState(TypedDict):
    # 요청 정보
    action: str              # "cancel" | "exchange"
    user_id: int
    session_id: int
    user_message: str        # interrupt resume 값 (사용자 답변)

    # 수집 데이터
    order_id: int | None
    order_display: str | None    # "주문 #12 — 딸기 2kg (2026-04-01)"
    selected_items: list         # 교환 품목 [{item_id, product_id, name, qty}]
    reason: str | None
    refund_method: str | None    # 취소 플로우만
    stock_note: str              # check_stock → show_summary 재고 부족 안내 메시지

    # 제어 플래그
    confirmed: bool | None      # None=미결, True=승인, False=거부
    abort: bool                  # True → handle_flow_cancel로 즉시 분기
    confirmation_attempts: int   # show_summary 재진입 횟수 — 3회 초과 시 강제 탈출
    is_pending: bool             # True=interrupt 대기 / False=플로우 종료

    # 결과
    ticket_id: int | None
    response: str            # 터미널 노드(create_ticket, handle_flow_cancel)가 채우는 최종 메시지
```

> **`stock_note`**: `check_stock` 노드가 채우고, `show_summary` 노드가 사용자에게 표시.  
> 교환 불가 수준의 재고 부족이어도 플로우는 계속 진행하고 접수 가능 여부는 오피스에서 최종 확인합니다.
>
> **`confirmation_attempts`**: `show_summary`가 재진입할 때마다 1씩 증가. `_MAX_CONFIRMATION_ATTEMPTS = 3`을 초과하면 `abort=True`로 강제 탈출합니다. 사용자가 모호한 응답을 반복할 때 무한 루프를 방지합니다.

---

## 그래프 구조

### 취소 플로우

```
START
  → route_action
  → list_orders          [interrupt: 주문 선택]
  → get_reason           [interrupt: 취소 사유]
  → get_refund_method    [interrupt: 환불 방법]
  → show_summary         [interrupt: 최종 확인]
  → create_ticket        → END
```

### 교환 플로우

```
START
  → route_action
  → list_orders          [interrupt: 주문 선택]
  → select_items         [interrupt: 교환 품목 선택]
  → check_stock          [자동 — interrupt 없음]
  → get_reason           [interrupt: 교환 사유]
  → show_summary         [interrupt: 최종 확인]
  → create_ticket        → END
```

어느 단계에서든 사용자가 "취소" 키워드를 입력하면 `abort=True` → `handle_flow_cancel` → `END`.

---

## 노드 목록

| 노드 | interrupt | 역할 |
|------|:---------:|------|
| `route_action` | ✗ | 취소/교환 분기용 passthrough |
| `list_orders` | ✓ | DB: 최근 5개 주문 조회 → 주문 선택 대기 |
| `select_items` | ✓ | DB: 주문 품목 조회 → 교환 품목 선택 대기 (교환 전용) |
| `check_stock` | ✗ | DB: 재고 일괄 조회(IN 쿼리) + 부족 시 `stock_note` 기록 (교환 전용) |
| `get_reason` | ✓ | 취소/교환 사유 선택 대기 |
| `get_refund_method` | ✓ | 환불 방법 선택 대기 (취소 전용) |
| `show_summary` | ✓ | 수집 정보 요약 + 최종 승인 대기 → `confirmed` 설정 |
| `create_ticket` | ✗ | DB: `ShopTicket` INSERT → `ticket_id` / `response` 설정 |
| `handle_flow_cancel` | ✗ | 플로우 중단 처리 → `response` = "취소됐습니다" |

---

## interrupt/resume 패턴 — 핵심 주의사항

LangGraph의 `interrupt(value)`는 예외를 발생시켜 그래프를 일시 중단합니다.  
**다음 `ainvoke()` 호출 시 노드는 처음부터 다시 실행되고**, `interrupt()`가 resume 값을 반환합니다.

```python
# list_orders 노드 — 두 번 실행됨
async def list_orders(state, config):
    db = config["configurable"]["db"]
    orders = db.query(Order).filter(...).all()   # ← 두 번째 실행에서도 다시 조회 (읽기 전용 OK)

    prompt = "어떤 주문인가요? ..."
    user_input = interrupt(prompt)               # ← 첫 실행: 여기서 중단
                                                 #    두 번째 실행: resume 값 반환
    order_id = _parse_order_selection(user_input, orders)
    return {..., "order_id": order_id}
```

**따라서:**
- `interrupt()` 이전에 DB 쓰기를 하면 두 번 실행됩니다 — 금지
- DB 읽기는 멱등이므로 안전합니다

---

## DB 주입 패턴

LangGraph는 노드에 `config: RunnableConfig`를 전달합니다.  
`SupervisorExecutor._call_order_agent()`에서 `config`를 구성할 때 `db`를 주입합니다.

```python
# SupervisorExecutor
config = {
    "configurable": {
        "thread_id": str(session_id),   # LangGraph checkpointer 키
        "db": db,                        # SQLAlchemy Session 주입
    }
}
await self.order_graph.ainvoke(initial_state, config)

# 노드 내부
def _get_db(config: RunnableConfig):
    return config["configurable"]["db"]
```

---

## checkpointer 설정

`AsyncPostgresSaver`를 사용합니다 (psycopg3 기반, psycopg2와 별도).  
앱 시작 시 `checkpointer.setup()`이 LangGraph 전용 테이블을 자동 생성합니다.

```python
# app/main.py (USE_MULTI_AGENT=true 시)
async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
    await checkpointer.setup()      # checkpoints, checkpoint_writes 테이블 생성
    order_graph = build_order_graph(checkpointer)
    ...
    yield   # ← yield는 반드시 async with 안에 있어야 checkpointer 연결 유지
```

`thread_id = str(session_id)` — 대화 세션과 그래프 상태가 1:1 대응합니다.

---

## 진행 중 플로우 재개

`SupervisorExecutor._run_loop()` 진입 시 `_has_pending_order_flow(session_id)`를 먼저 확인합니다.  
진행 중인 플로우가 있으면 **Supervisor LLM 판단 없이** 즉시 OrderGraph로 전달합니다.

```python
if session_id and await self._has_pending_order_flow(session_id):
    response = await self._call_order_agent(user_message, ...)
    return AgentResult(answer=response, ...)
```

이렇게 하지 않으면 Supervisor LLM이 interrupt 질문을 리포맷하거나  
다른 에이전트를 잘못 호출하는 문제가 생깁니다.

---

## 주문 상태(status) 전체 목록

`shop_orders.status` 컬럼에 저장되는 값과 의미입니다.

| status | 한국어 표시 | 전환 방식 | 취소 | 교환/반품 |
|---|---|---|---|:---:|
| `pending` | 주문 접수 | 주문 생성 기본값 | 즉시 자동 | ✗ |
| `preparing` | 상품 준비 중 | admin 수동 | 즉시 자동 | ✗ |
| `shipped` | 배송 중 | Shipment 생성 시 자동 | 관리자 검토 | ✗ |
| `delivered` | 배송 완료 | Shipment.status=delivered 시 자동 | 불가 | ✅ |
| `cancelled` | 취소 완료 | 자동/관리자 | 불가 | ✗ |
| `returned` | 반품 완료 | 교환/취소 티켓 completed 시 자동 | 불가 | ✗ |

> 비표준 값(`registered`, `picked_up`, `in_transit`, `shipping`, `paid` 등)은 인식되지 않습니다.  
> 테스트 데이터 삽입 시에도 위 목록 중 하나를 사용해야 합니다.  
> 기존 데이터 변환: `uv run python scripts/migrate_order_status.py --dry-run`

### 상태 전환 흐름

```text
pending ──(admin)──→ preparing ──(Shipment 생성)──→ shipped ──(admin 배송완료 처리)──→ delivered
   │                    │               │                                                   │
   │                    │               │                                                   │
(자동취소)           (자동취소)      (관리자취소)                                        (교환/취소 티켓 completed)
   │                    │               │                                                   │
   └────────────────────┴───────────────┘                                               returned
                        ↓
                    cancelled
```

Shipment.status는 별도: `registered → picked_up → in_transit → delivered`

## 주문 상태 필터

취소/교환 가능 여부를 제한하는 상수가 `nodes.py`에 정의됩니다.

```python
CANCELLABLE_STATUSES: frozenset[str] = frozenset({"pending", "preparing"})
# 취소: 배송사 픽업 전(주문 접수·상품 준비 중) 단계만

EXCHANGEABLE_STATUSES: frozenset[str] = frozenset({"delivered"})
# 교환: 배송 완료된 주문만
```

`list_orders` 노드는 `action`에 따라 해당 상수로 필터링합니다.  
이미 배송된 주문이 취소 목록에 나타나거나, 배송 중인 주문이 교환 목록에 나타나는 버그를 방지합니다.

---

## ShippingTracker 자동 상태 전환 범위

`app/services/shipping_tracker.py`는 APScheduler로 주기적으로 실행됩니다.  
배송 지연·내부 사정으로 인해 **자동 완료 처리는 금지**되어 있습니다.

```python
# 자동 전환 최대 단계: in_transit (delivered는 관리자 직접 처리)
if days_elapsed >= 2:   new_status = "in_transit"
elif days_elapsed >= 1: new_status = "picked_up"
else:                   new_status = "registered"
```

`delivered` 상태는 절대 자동으로 전환되지 않습니다 — 관리자 직접 처리 필요.

---

## N+1 쿼리 방지 — `_build_order_summaries` 패턴

### 문제

LangGraph 노드에서 주문 목록을 표시할 때 N개 주문 × 3번 쿼리(Order, OrderItem, Product)가 발생할 수 있습니다.

```python
# ❌ 나쁜 패턴 — 주문마다 DB 왕복
for order in orders:
    items = db.query(OrderItem).filter_by(order_id=order.id).all()
    for item in items:
        product = db.query(Product).filter_by(id=item.product_id).first()
```

### 해결: 일괄 IN 쿼리

```python
def _build_order_summaries(db, orders: list) -> dict[int, str]:
    """N개 주문의 표시 문자열을 2번의 IN 쿼리로 생성."""
    order_ids = [o.id for o in orders]

    # 1번째 쿼리 — 모든 주문의 품목을 한 번에
    all_items = db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).all()
    items_by_order: dict[int, list] = {oid: [] for oid in order_ids}
    product_ids: set[int] = set()
    for item in all_items:
        items_by_order[item.order_id].append(item)
        product_ids.add(item.product_id)

    # 2번째 쿼리 — 관련 상품명을 한 번에 (품목이 없으면 생략)
    products: dict[int, str] = {}
    if product_ids:
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all():
            products[p.id] = p.name

    summaries: dict[int, str] = {}
    for order in orders:
        names = [products.get(i.product_id, "상품") for i in items_by_order[order.id]]
        label = ", ".join(names[:2]) + ("…" if len(names) > 2 else "")
        summaries[order.id] = f"주문 #{order.id} — {label} ({order.created_at:%Y-%m-%d})"
    return summaries
```

이 패턴은 `list_orders`, `select_items`, `check_stock`, `_parse_item_selections` 노드에 동일하게 적용됩니다.

### interrupt/resume과 멱등성

노드가 두 번 실행되는 특성상 IN 쿼리도 두 번 실행됩니다.  
읽기 전용 쿼리는 멱등이므로 안전합니다 — `interrupt()` 이전의 DB 읽기는 반복 실행해도 결과가 동일합니다.

---

## create_ticket 멱등성 (중복 티켓 방지)

`show_summary` 확인 후 네트워크 재시도 등으로 `create_ticket`이 두 번 호출될 수 있습니다.  
기존 티켓이 있으면 INSERT 없이 재사용합니다.

```python
async def create_ticket(state: OrderState, config: RunnableConfig) -> dict:
    db = _get_db(config)
    # 이미 생성된 티켓 확인 (idempotency)
    existing = (
        db.query(ShopTicket)
        .filter_by(order_id=state["order_id"], user_id=state["user_id"],
                   action_type=state["action"], status="received")
        .order_by(ShopTicket.created_at.desc())
        .first()
    )
    if existing:
        return {"ticket_id": existing.id, "response": f"티켓 #{existing.id}가 이미 접수되었습니다.", ...}
    # 없으면 신규 INSERT
    ...
```

---

## ShopTicket 모델

`create_ticket` 노드가 플로우 완료 시 `shop_tickets` 테이블에 저장합니다.

| 컬럼 | 타입 | 내용 |
|------|------|------|
| `id` | int PK | 티켓 번호 |
| `user_id` | int FK | 고객 |
| `session_id` | int FK? | 채팅 세션 (`shop_chat_sessions.id`, nullable) |
| `order_id` | int FK | 대상 주문 |
| `action_type` | str(20) | "cancel" \| "exchange" |
| `reason` | Text | 사유 |
| `refund_method` | str(50)? | 환불 방법 (취소 전용) |
| `items` | Text? | JSON 배열 (교환 전용) |
| `status` | str(30) | "received" → "processing" → "completed" \| "cancelled" |
| `created_at` | datetime | 접수 시각 |

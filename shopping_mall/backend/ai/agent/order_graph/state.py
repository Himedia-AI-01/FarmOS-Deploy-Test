"""OrderGraph 상태 스키마."""
from typing import TypedDict


class OrderState(TypedDict):
    # ── 컨텍스트 (최초 ainvoke 시 주입) ────────────────────────────────────
    action: str           # "cancel" | "exchange" | "change"
    user_id: int
    session_id: int
    user_message: str     # interrupt resume 시 사용자 입력값

    # ── 단계별 수집 데이터 ────────────────────────────────────────────────
    order_id: int | None
    order_display: str | None   # "주문 #12 - 딸기 2kg (2026-04-18)" 형식 (사용자 표시용)
    selected_items: list        # [{"item_id": int, "name": str, "qty": int}]
    reason: str | None          # 교환/취소 사유
    refund_method: str | None   # 취소 시 환불 방법
    change_type: str | None     # 주문 변경 유형
    change_detail: str | None   # 주문 변경 요청 상세
    stock_note: str             # 교환 품목 재고 부족 안내 (check_stock → show_summary 전달용)

    # ── 흐름 제어 ─────────────────────────────────────────────────────────
    confirmed: bool | None  # None=미결, True=최종 승인, False=거부
    abort: bool             # True → 즉시 handle_flow_cancel로 라우팅
    confirmation_attempts: int  # show_summary 재진입 횟수 — 3회 초과 시 강제 탈출

    # ── 출력 ──────────────────────────────────────────────────────────────
    ticket_id: int | None
    response: str    # 완료 노드가 설정하는 최종 메시지 (Supervisor에게 반환)
    is_pending: bool  # True: 사용자 입력 대기 / False: 최종 완료

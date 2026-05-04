"""OrderGraph 빌드 — LangGraph StateGraph."""
import logging

from langgraph.graph import StateGraph, START, END

from .state import OrderState
from .nodes import (
    route_action,
    list_orders,
    select_items,
    check_stock,
    get_reason,
    get_refund_method,
    get_change_type,
    get_change_detail,
    show_summary,
    create_ticket,
    handle_flow_cancel,
    route_after_list_orders,
    route_after_get_reason,
    route_after_show_summary,
    route_abort_check,
)

logger = logging.getLogger(__name__)


def build_order_graph(checkpointer) -> "CompiledStateGraph":
    """OrderGraph 컴파일. checkpointer는 AsyncPostgresSaver 또는 MemorySaver."""
    builder = StateGraph(OrderState)

    # ── 노드 등록 ────────────────────────────────────────────────────────────
    builder.add_node("route_action",       route_action)
    builder.add_node("list_orders",        list_orders)
    builder.add_node("select_items",       select_items)
    builder.add_node("check_stock",        check_stock)
    builder.add_node("get_reason",         get_reason)
    builder.add_node("get_refund_method",  get_refund_method)
    builder.add_node("get_change_type",    get_change_type)
    builder.add_node("get_change_detail",  get_change_detail)
    builder.add_node("show_summary",       show_summary)
    builder.add_node("create_ticket",      create_ticket)
    builder.add_node("handle_flow_cancel", handle_flow_cancel)

    # ── 엣지 설정 ────────────────────────────────────────────────────────────

    # 진입: route_action → list_orders (취소/교환 모두 주문 선택부터 시작)
    builder.add_edge(START, "route_action")
    builder.add_edge("route_action", "list_orders")

    # list_orders → 취소: get_reason / 교환: select_items / 변경: get_change_type
    builder.add_conditional_edges(
        "list_orders",
        route_after_list_orders,
        {
            "get_reason":         "get_reason",
            "select_items":       "select_items",
            "get_change_type":    "get_change_type",
            "handle_flow_cancel": "handle_flow_cancel",
        },
    )

    # 교환 플로우: select_items → check_stock
    builder.add_conditional_edges(
        "select_items",
        route_abort_check,
        {
            "handle_flow_cancel": "handle_flow_cancel",
            "__continue__":       "check_stock",
        },
    )
    builder.add_conditional_edges(
        "check_stock",
        route_abort_check,
        {
            "handle_flow_cancel": "handle_flow_cancel",
            "__continue__":       "get_reason",
        },
    )

    # get_reason → 취소: get_refund_method / 교환: show_summary / abort: handle_flow_cancel
    builder.add_conditional_edges(
        "get_reason",
        route_after_get_reason,
        {
            "get_refund_method":  "get_refund_method",
            "show_summary":       "show_summary",
            "handle_flow_cancel": "handle_flow_cancel",
        },
    )

    # get_refund_method → show_summary / abort: handle_flow_cancel
    builder.add_conditional_edges(
        "get_refund_method",
        route_abort_check,
        {
            "handle_flow_cancel": "handle_flow_cancel",
            "__continue__":       "show_summary",
        },
    )

    # 주문 변경 플로우: 변경 유형 → 변경 상세 → 최종 확인
    builder.add_conditional_edges(
        "get_change_type",
        route_abort_check,
        {
            "handle_flow_cancel": "handle_flow_cancel",
            "__continue__":       "get_change_detail",
        },
    )
    builder.add_conditional_edges(
        "get_change_detail",
        route_abort_check,
        {
            "handle_flow_cancel": "handle_flow_cancel",
            "__continue__":       "show_summary",
        },
    )

    # show_summary → create_ticket / handle_flow_cancel / show_summary(재확인)
    builder.add_conditional_edges(
        "show_summary",
        route_after_show_summary,
        {
            "create_ticket":      "create_ticket",
            "handle_flow_cancel": "handle_flow_cancel",
            "show_summary":       "show_summary",
        },
    )

    # 종료 노드
    builder.add_edge("create_ticket",      END)
    builder.add_edge("handle_flow_cancel", END)

    compiled = builder.compile(checkpointer=checkpointer)
    logger.info("OrderGraph 컴파일 완료.")
    return compiled

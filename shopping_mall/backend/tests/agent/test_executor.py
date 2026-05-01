"""AgentExecutor 테스트 — LangChain 기반 (LangChain 전환 후 재작성).

구 FakeAgentClient / _dispatch_tool / ToolCall 패턴 제거.
대체 패턴:
  - FakeLLM / FakeLLMChain: bind_tools / with_fallbacks / ainvoke 스텁
  - 도구 직접 테스트: build_cs_tools(rag, db, user_id) 팩토리 + tool.ainvoke(args)
  - run() 호출: input_system(도구 선택) + output_system(응답 생성) 두 파라미터 필수
  - 단일 패스 플로우: LLM 2회 고정 (CS_INPUT_PROMPT 도구 선택 → 도구 실행 → CS_OUTPUT_PROMPT 응답 생성)
  - 도구 없는 케이스(인사말 등): LLM 1회 (CS_INPUT_PROMPT 직접 응답)
"""
import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ai.agent import AgentExecutor, ToolMetricData
from ai.agent.executor import _is_empty_result
from tests.conftest import (
    FakeRAGService,
    make_mock_db,
    make_order,
    make_shipment,
    make_product,
)


INPUT_SYSTEM = "당신은 FarmOS 마켓의 AI 고객 지원 에이전트입니다."
OUTPUT_SYSTEM = "수집된 도구 결과를 바탕으로 친절하게 답변하세요."


# ── FakeLLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    """AIMessage 시퀀스를 순서대로 반환하는 LangChain LLM 스텁.

    responses 원소가 Exception이면 그 호출에서 예외를 발생시킵니다.
    bind_tools()는 self를 반환하여 도구 바인딩을 투명하게 통과시킵니다.
    with_fallbacks()는 FakeLLMChain을 반환합니다.
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._idx = 0
        self.calls: list = []

    def bind_tools(self, tools):
        return self

    def with_fallbacks(self, fallbacks):
        return FakeLLMChain(self, list(fallbacks))

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        if self._idx >= len(self._responses):
            raise RuntimeError(
                f"FakeLLM 응답 소진 — {self._idx + 1}번째 호출에 응답 없음"
            )
        resp = self._responses[self._idx]
        self._idx += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeLLMChain:
    """primary.with_fallbacks([fallback, ...]) 체인 스텁.

    primary.ainvoke()가 예외를 던지면 fallback 목록을 순서대로 시도합니다.
    모든 fallback도 실패하면 마지막 예외를 재발생시킵니다.
    """

    def __init__(self, primary: FakeLLM, fallbacks: list):
        self._primary = primary
        self._fallbacks = fallbacks

    def bind_tools(self, tools):
        return self

    def with_fallbacks(self, fallbacks):
        return FakeLLMChain(self._primary, self._fallbacks + list(fallbacks))

    async def ainvoke(self, messages, config=None):
        try:
            return await self._primary.ainvoke(messages)
        except Exception as last_exc:
            for fb in self._fallbacks:
                try:
                    return await fb.ainvoke(messages)
                except Exception as e:
                    last_exc = e
            raise last_exc


# ── 메시지 헬퍼 ───────────────────────────────────────────────────────────────

def make_tool_call_message(*tool_calls: tuple[str, dict]) -> AIMessage:
    """(tool_name, args) 튜플 목록 → tool_calls 있는 AIMessage."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"tc_{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(tool_calls)
        ],
    )


def make_text_message(content: str) -> AIMessage:
    """텍스트 내용만 있는 AIMessage (tool_calls 없음)."""
    return AIMessage(content=content, tool_calls=[])


# ── 테스트 헬퍼 ───────────────────────────────────────────────────────────────

def make_executor(primary, fallback=None, rag=None, max_iterations=10):
    return AgentExecutor(
        primary=primary,
        fallback=fallback,
        rag_service=rag or FakeRAGService(),
        max_iterations=max_iterations,
    )


async def run(executor, db, question, user_id=None, history=None):
    """executor.run() 축약 헬퍼 — input/output_system 고정."""
    return await executor.run(
        db=db,
        user_message=question,
        user_id=user_id,
        history=history or [],
        input_system=INPUT_SYSTEM,
        output_system=OUTPUT_SYSTEM,
    )


def get_tool(rag, db, user_id, tool_name):
    """build_cs_tools 팩토리에서 특정 도구를 꺼내는 헬퍼."""
    from ai.agent.cs_tools import build_cs_tools
    tools, _ctx = build_cs_tools(rag, db, user_id)
    return {t.name: t for t in tools}[tool_name]


# ══════════════════════════════════════════════════════════════════
# 정상 케이스
# ══════════════════════════════════════════════════════════════════

class TestNormalCases:

    async def test_direct_text_answer(self, empty_db):
        """도구 호출 없이 LLM이 직접 텍스트로 답변 — LLM 1회 호출."""
        llm = FakeLLM([make_text_message("안녕하세요! 무엇을 도와드릴까요?")])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "안녕하세요")

        assert result.answer == "안녕하세요! 무엇을 도와드릴까요?"
        assert result.tools_used == []
        assert result.intent == "other"
        assert result.escalated is False
        assert len(llm.calls) == 1

    async def test_single_tool_then_answer(self, empty_db):
        """단일 도구 호출 후 최종 답변 — LLM 2회 호출(도구 선택 + 최종 답변)."""
        llm = FakeLLM([
            make_tool_call_message(("search_products", {"query": "딸기"})),
            make_text_message("딸기는 현재 150개 재고가 있습니다."),  # 최종 답변
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "딸기 재고 있어?")

        assert "딸기" in result.answer
        assert result.tools_used == ["search_products"]
        assert result.intent == "stock"
        assert result.escalated is False
        assert len(llm.calls) == 2

    async def test_multi_tool_single_pass(self, empty_db):
        """두 개 도구를 한 번에 선택 후 최종 답변 — LLM 2회 호출."""
        rag = FakeRAGService({"faq": ["딸기는 냉장 보관하세요."]})
        llm = FakeLLM([
            make_tool_call_message(
                ("search_products", {"query": "딸기"}),
                ("search_faq", {"query": "딸기 보관법", "subcategory": "storage"}),
            ),
            make_text_message("딸기는 150개 재고가 있으며, 냉장 보관하시면 됩니다."),
        ])
        executor = make_executor(llm, rag=rag)

        result = await run(executor, empty_db, "딸기 재고랑 보관법 알려줘")

        assert set(result.tools_used) == {"search_products", "search_faq"}
        assert result.intent == "stock"  # 첫 번째 도구 기준
        assert result.escalated is False
        assert len(llm.calls) == 2

    async def test_parallel_tool_calls_in_one_turn(self, empty_db):
        """한 턴에 여러 도구를 동시에 호출 (RAG 도구는 asyncio.gather로 병렬 실행)."""
        llm = FakeLLM([
            make_tool_call_message(
                ("search_products", {"query": "사과"}),
                ("search_faq", {"query": "배송 기간"}),
            ),
            make_text_message("사과 재고 확인 및 배송 안내 드립니다."),
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "사과 있어요? 배송은 얼마나 걸려요?")

        assert set(result.tools_used) == {"search_products", "search_faq"}

    async def test_rag_docs_appear_in_output_llm_call(self, empty_db):
        """RAG 결과가 ToolMessage로 CS_OUTPUT_PROMPT 호출(2번째)에 전달되는지 검증."""
        policy_doc = "반품은 수령 후 24시간 이내 신청 가능합니다."
        rag = FakeRAGService({"return_policy": [policy_doc]})
        llm = FakeLLM([
            make_tool_call_message(("search_policy", {"query": "반품 기간", "policy_type": "return"})),
            make_text_message("반품은 수령 후 24시간 이내 가능합니다."),
        ])
        executor = make_executor(llm, rag=rag)

        await run(executor, empty_db, "반품 얼마나 기다려요?")

        # 2번째 LLM 호출 = CS_OUTPUT_PROMPT(output_system)로 응답 생성하는 호출
        second_call = llm.calls[1]
        system_msgs = [m for m in second_call if isinstance(m, SystemMessage)]
        tool_msgs = [m for m in second_call if isinstance(m, ToolMessage)]
        assert system_msgs[0].content.startswith(OUTPUT_SYSTEM)  # CS_OUTPUT_PROMPT 사용 확인
        assert any(policy_doc in m.content for m in tool_msgs)

    async def test_get_order_status_logged_in(self):
        """로그인 사용자의 주문/배송 조회."""
        order = make_order(order_id=100, user_id=10, status="shipped")
        shipment = make_shipment(order_id=100, carrier="CJ대한통운", tracking_number="9999")
        db = make_mock_db(orders=[order], shipments=[shipment])

        llm = FakeLLM([
            make_tool_call_message(("get_order_status", {"order_id": None})),
            make_text_message("주문 #100이 배송 중입니다."),
        ])
        executor = make_executor(llm)

        result = await run(executor, db, "내 배송 어디까지 왔어요?", user_id=10)

        assert result.tools_used == ["get_order_status"]
        assert result.intent == "delivery"
        assert result.escalated is False

    async def test_history_appended_to_messages(self, empty_db):
        """이전 대화 히스토리가 LangChain 메시지로 변환되어 LLM 호출에 포함."""
        llm = FakeLLM([make_text_message("네, 기억합니다.")])
        executor = make_executor(llm)
        history = [
            {"role": "user", "content": "딸기 있어요?"},
            {"role": "assistant", "content": "딸기는 150개 재고가 있습니다."},
        ]

        await run(executor, empty_db, "아까 말한 거 기억해?", history=history)

        first_call = llm.calls[0]
        # SystemMessage + [HumanMessage, AIMessage] (히스토리) + HumanMessage (현재)
        assert any(isinstance(m, HumanMessage) for m in first_call)
        assert any(isinstance(m, AIMessage) for m in first_call)

    async def test_escalate_to_agent_sets_flag(self, empty_db):
        """escalate_to_agent 도구 호출 시 escalated=True."""
        llm = FakeLLM([
            make_tool_call_message(("escalate_to_agent", {"reason": "고객 요청", "urgency": "normal"})),
            make_text_message("상담원에게 연결해 드리겠습니다."),
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "상담원 연결해주세요")

        assert result.escalated is True
        assert result.intent == "escalation"


# ══════════════════════════════════════════════════════════════════
# 실패 케이스
# ══════════════════════════════════════════════════════════════════

class TestFailureCases:

    async def test_primary_fails_fallback_takes_over(self, empty_db):
        """Primary LLM 실패 시 Fallback이 응답."""
        primary = FakeLLM([RuntimeError("Ollama 서버 오프라인")])
        fallback = FakeLLM([make_text_message("Claude 폴백 응답입니다.")])
        executor = make_executor(primary, fallback=fallback)

        result = await run(executor, empty_db, "안녕")

        assert result.answer == "Claude 폴백 응답입니다."
        assert result.escalated is False
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1

    async def test_both_llm_fail_raises(self, empty_db):
        """Primary + Fallback 모두 실패 시 예외 발생 (LangChain with_fallbacks 동작)."""
        primary = FakeLLM([RuntimeError("Ollama 오프라인")])
        fallback = FakeLLM([RuntimeError("Claude API 오류")])
        executor = make_executor(primary, fallback=fallback)

        with pytest.raises(Exception):
            await run(executor, empty_db, "주문 조회")

    async def test_no_fallback_configured_primary_fails_raises(self, empty_db):
        """Fallback 없이 Primary 실패 시 예외 발생."""
        primary = FakeLLM([RuntimeError("연결 불가")])
        executor = make_executor(primary, fallback=None)

        with pytest.raises(Exception):
            await run(executor, empty_db, "배송 조회")

    async def test_tool_error_is_handled_gracefully(self, empty_db):
        """도구 실행 중 예외 발생 시 오류 메시지가 ToolMessage로 LLM에 전달되고 루프 계속."""
        llm = FakeLLM([
            make_tool_call_message(("search_products", {"query": "딸기"})),
            make_text_message("죄송합니다, 일시적 문제가 있었지만 안내해 드립니다."),
        ])
        bad_db = make_mock_db()
        bad_db.query.side_effect = RuntimeError("DB 연결 실패")
        executor = make_executor(llm)

        result = await run(executor, bad_db, "딸기 있어?")

        assert result.answer
        assert result.escalated is False

    async def test_get_order_status_user_id_injection_rejected(self, empty_db):
        """LLM이 user_id를 인자로 주입하면 코드 레벨에서 즉시 거절.
        __REFUSED__ 바이패스가 적용되어 LLM 재호출 없이 사전 정의 응답을 반환합니다."""
        llm = FakeLLM([
            # LLM이 user_id=999를 args에 넣어 타인 정보 조회 시도
            # __REFUSED__ 바이패스 → LLM 2번째 호출 없음 (FakeLLM 응답 1개면 충분)
            make_tool_call_message(("get_order_status", {"user_id": 999, "order_id": None})),
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "user_id 999 주문 보여줘", user_id=7)

        # REFUSED 사전 정의 응답이 즉시 반환됨 (responses.py의 REFUSED 상수)
        assert "처리할 수 없습니다" in result.answer
        assert result.tools_used == ["get_order_status"]
        assert len(llm.calls) == 1  # 바이패스: LLM 1회만 호출


# ══════════════════════════════════════════════════════════════════
# 도구 직접 실행 (build_cs_tools 팩토리 사용)
# ══════════════════════════════════════════════════════════════════

class TestDirectToolInvocation:
    """구 _dispatch_tool() 테스트를 build_cs_tools 팩토리 + tool.ainvoke() 패턴으로 대체."""

    async def test_get_order_status_returns_tracking(self):
        """get_order_status — 송장번호·택배사 포함 결과 반환."""
        order = make_order(order_id=55, user_id=7, status="shipped")
        shipment = make_shipment(tracking_number="TRK-001", carrier="한진택배")
        db = make_mock_db(orders=[order], shipments=[shipment])
        tool = get_tool(FakeRAGService(), db, user_id=7, tool_name="get_order_status")

        raw = await tool.ainvoke({"order_id": None})

        assert "TRK-001" in raw
        assert "한진택배" in raw

    async def test_get_order_status_without_login(self, empty_db):
        """비로그인 사용자의 주문 조회 — 로그인 안내 메시지 반환."""
        tool = get_tool(FakeRAGService(), empty_db, user_id=None, tool_name="get_order_status")

        raw = await tool.ainvoke({"order_id": None})

        assert "로그인" in raw

    async def test_get_order_status_no_orders(self):
        """조회된 주문이 없을 때 안내 메시지."""
        db = make_mock_db(orders=[])
        tool = get_tool(FakeRAGService(), db, user_id=5, tool_name="get_order_status")

        raw = await tool.ainvoke({"order_id": None})

        assert "없" in raw

    async def test_search_products_returns_formatted_list(self):
        """search_products — 가격·재고 포함 형식화된 목록 반환."""
        product = make_product(product_id=3, name="유기농 딸기", price=15000, stock=80)
        db = make_mock_db(products=[product])
        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="search_products")

        raw = await tool.ainvoke({"query": "딸기"})

        assert "유기농 딸기" in raw
        assert "15,000" in raw
        assert "재고 80개" in raw

    async def test_search_products_no_results(self):
        """상품 검색 결과 없을 때 '결과 없음' 메시지."""
        db = make_mock_db(products=[])
        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="search_products")

        raw = await tool.ainvoke({"query": "존재하지않는상품XYZ"})

        assert "없" in raw

    async def test_search_products_check_stock(self):
        """check_stock=True 시 재고 있는 상품만 반환."""
        product = make_product(product_id=1, name="딸기", stock=10)
        db = make_mock_db(products=[product])
        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="search_products")

        raw = await tool.ainvoke({"query": "딸기", "check_stock": True})

        assert "딸기" in raw

    async def test_search_products_limit_clamped_at_20(self):
        """limit > 20 요청 시 20으로 clamp — DB에 limit(20) 이하로 호출."""
        from app.models.product import Product

        products = [make_product(product_id=i, name=f"상품{i}", stock=5) for i in range(25)]
        product_mock = MagicMock()
        captured_limit: list[int] = []

        def limit_side_effect(n):
            captured_limit.append(n)
            ret = MagicMock()
            ret.all.return_value = products[:n]
            return ret

        product_mock.filter.return_value.order_by.return_value.limit.side_effect = limit_side_effect
        db = MagicMock()
        db.query.side_effect = lambda m: product_mock if m is Product else MagicMock()

        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="search_products")
        await tool.ainvoke({"query": "상품", "limit": 100})

        assert captured_limit and captured_limit[0] <= 20, (
            f"limit이 {captured_limit[0]}로 호출됨 — 20 이하이어야 함"
        )

    async def test_escalate_high_urgency_contains_priority(self, empty_db):
        """urgency=high 에스컬레이션 메시지에 '우선' 또는 '빠르게' 포함."""
        tool = get_tool(FakeRAGService(), empty_db, user_id=None, tool_name="escalate_to_agent")

        raw = await tool.ainvoke({"reason": "긴급", "urgency": "high"})

        assert "우선" in raw or "빠르게" in raw

    async def test_search_policy_all_types(self):
        """policy_type=all 시 여러 컬렉션에서 통합 검색."""
        rag = FakeRAGService({
            "return_policy": ["반품 정책 내용"],
            "payment_policy": ["결제 정책 내용"],
        })
        db = make_mock_db()
        tool = get_tool(rag, db, user_id=None, tool_name="search_policy")

        raw = await tool.ainvoke({"query": "환불", "policy_type": "all"})

        assert "반품 정책 내용" in raw
        assert "결제 정책 내용" in raw

    async def test_empty_rag_returns_fallback_message(self, empty_db):
        """RAG에 관련 문서 없을 때 폴백 텍스트 반환."""
        tool = get_tool(FakeRAGService(), empty_db, user_id=None, tool_name="search_faq")

        raw = await tool.ainvoke({"query": "희귀버섯 보관법", "subcategory": "storage"})

        assert "찾을 수 없" in raw or "없습니다" in raw

    async def test_known_tools_set(self, empty_db):
        """build_cs_tools가 정의된 9개 도구를 정확히 반환하는지 확인."""
        from ai.agent.cs_tools import build_cs_tools
        tools, _ctx = build_cs_tools(FakeRAGService(), empty_db, user_id=None)
        tool_names = {t.name for t in tools}

        expected = {
            "search_faq",
            "search_policy",
            "get_order_status", "search_products", "get_product_detail",
            "escalate_to_agent", "refuse_request",
            "cancel_order", "process_refund",
        }
        assert tool_names == expected


# ══════════════════════════════════════════════════════════════════
# get_product_detail 직접 결과 검증
# ══════════════════════════════════════════════════════════════════

class TestGetProductDetail:

    async def test_by_id_returns_detail(self):
        """product_id로 상품 상세 조회 성공."""
        from app.models.product import Product

        product = make_product(product_id=10, name="유기농 사과", price=20000, stock=50)
        product_mock = MagicMock()
        product_mock.filter.return_value.first.return_value = product
        db = MagicMock()
        db.query.side_effect = lambda m: product_mock if m is Product else MagicMock()

        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="get_product_detail")
        raw = await tool.ainvoke({"product_id": 10})

        assert "유기농 사과" in raw
        assert "20,000" in raw
        assert "50개 재고" in raw

    async def test_by_name_returns_detail(self):
        """product_name으로 상품 상세 조회 성공."""
        from app.models.product import Product

        product = make_product(product_id=5, name="제주 감귤", price=8000, stock=200)
        product_mock = MagicMock()
        product_mock.filter.return_value.first.return_value = product
        db = MagicMock()
        db.query.side_effect = lambda m: product_mock if m is Product else MagicMock()

        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="get_product_detail")
        raw = await tool.ainvoke({"product_name": "감귤"})

        assert "제주 감귤" in raw

    async def test_product_not_found(self):
        """존재하지 않는 상품 조회 시 안내 메시지."""
        from app.models.product import Product

        product_mock = MagicMock()
        product_mock.filter.return_value.first.return_value = None
        db = MagicMock()
        db.query.side_effect = lambda m: product_mock if m is Product else MagicMock()

        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="get_product_detail")
        raw = await tool.ainvoke({"product_name": "없는상품XYZ"})

        assert "찾을 수 없" in raw

    async def test_no_args_returns_guidance(self, empty_db):
        """product_id / product_name 모두 없을 때 안내 메시지."""
        tool = get_tool(FakeRAGService(), empty_db, user_id=None, tool_name="get_product_detail")
        raw = await tool.ainvoke({})

        assert "입력해 주세요" in raw or "ID" in raw

    async def test_out_of_stock_shows_soldout(self):
        """재고 0인 상품에 '품절' 표시."""
        from app.models.product import Product

        product = make_product(product_id=7, name="품절상품", price=5000, stock=0)
        product.restock_date = None
        product_mock = MagicMock()
        product_mock.filter.return_value.first.return_value = product
        db = MagicMock()
        db.query.side_effect = lambda m: product_mock if m is Product else MagicMock()

        tool = get_tool(FakeRAGService(), db, user_id=None, tool_name="get_product_detail")
        raw = await tool.ainvoke({"product_id": 7})

        assert "품절" in raw


# ══════════════════════════════════════════════════════════════════
# intent 역산 로직
# ══════════════════════════════════════════════════════════════════

class TestIntentMapping:

    @pytest.mark.parametrize("tool_name,args,expected_intent", [
        ("get_order_status",   {"order_id": None},                               "delivery"),
        ("search_products",    {"query": "test"},                                "stock"),
        ("get_product_detail", {"product_name": "딸기"},                          "stock"),
        ("search_policy",      {"query": "test"},                                "policy"),
        ("search_faq",         {"query": "test"},                                "faq"),
        ("escalate_to_agent",  {"reason": "고객 요청"},                            "escalation"),
        ("cancel_order",       {"order_id": 1, "reason": "변심"},                 "cancel"),
        ("process_refund",     {"order_id": 1, "refund_method": "원결제 수단"},    "cancel"),
    ])
    async def test_intent_from_tool(self, tool_name, args, expected_intent, empty_db):
        """각 도구 호출 시 올바른 intent가 AgentResult에 역산되는지 검증."""
        llm = FakeLLM([
            make_tool_call_message((tool_name, args)),
            make_text_message("답변"),   # 최종 답변
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "테스트", user_id=1)

        assert result.intent == expected_intent, (
            f"tool={tool_name}: expected={expected_intent}, got={result.intent}"
        )

    async def test_no_tool_used_intent_is_other(self, empty_db):
        """도구 미사용 시 intent='other'."""
        llm = FakeLLM([make_text_message("직접 답변")])
        result = await run(make_executor(llm), empty_db, "안녕")
        assert result.intent == "other"


# ══════════════════════════════════════════════════════════════════
# 메트릭 수집
# ══════════════════════════════════════════════════════════════════

class TestToolMetrics:

    async def test_single_tool_collects_metric(self, empty_db):
        """단일 도구 호출 시 metrics에 1건 수집."""
        llm = FakeLLM([
            make_tool_call_message(("search_products", {"query": "딸기"})),
            make_text_message("딸기 재고 150개입니다."),
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "딸기 있어?")

        assert len(result.metrics) == 1
        m = result.metrics[0]
        assert m.tool_name == "search_products"
        assert m.intent == "stock"
        assert m.success is True
        assert m.latency_ms >= 0
        assert m.iteration == 1

    async def test_parallel_tools_collect_metrics(self, empty_db):
        """한 턴 2개 병렬 도구 호출 시 metrics 2건 수집."""
        llm = FakeLLM([
            make_tool_call_message(
                ("search_products", {"query": "사과"}),
                ("search_faq", {"query": "배송 기간"}),
            ),
            make_text_message("사과 재고 확인 및 배송 안내 드립니다."),
        ])
        executor = make_executor(llm)

        result = await run(executor, empty_db, "사과 있어요? 배송은?")

        assert len(result.metrics) == 2
        names = {m.tool_name for m in result.metrics}
        assert names == {"search_products", "search_faq"}
        for m in result.metrics:
            assert m.latency_ms >= 0
            assert m.iteration == 1

    async def test_error_tool_metric_success_false(self, empty_db):
        """도구 실행 오류 시 success=False 메트릭."""
        llm = FakeLLM([
            make_tool_call_message(("search_products", {"query": "딸기"})),
            make_text_message("오류가 있었지만 안내 드립니다."),
        ])
        bad_db = make_mock_db()
        bad_db.query.side_effect = RuntimeError("DB 연결 실패")
        executor = make_executor(llm)

        result = await run(executor, bad_db, "딸기 있어?")

        assert len(result.metrics) == 1
        assert result.metrics[0].success is False

    async def test_empty_result_detected(self):
        """빈 결과(검색 결과 없음)가 empty_result=True로 수집."""
        llm = FakeLLM([
            make_tool_call_message(("search_products", {"query": "존재하지않는XYZ"})),
            make_text_message("해당 상품을 찾지 못했습니다."),
        ])
        db = make_mock_db(products=[])
        executor = make_executor(llm)

        result = await run(executor, db, "XYZ 있어?")

        assert len(result.metrics) == 1
        assert result.metrics[0].empty_result is True

    async def test_multi_tool_metrics_same_iteration(self, empty_db):
        """단일 패스에서 복수 도구 호출 시 모든 메트릭의 iteration=1."""
        rag = FakeRAGService({"faq": ["냉장 보관하세요."]})
        llm = FakeLLM([
            make_tool_call_message(
                ("search_products", {"query": "딸기"}),
                ("search_faq", {"query": "딸기 보관법", "subcategory": "storage"}),
            ),
            make_text_message("딸기 재고와 보관법 안내입니다."),
        ])
        executor = make_executor(llm, rag=rag)

        result = await run(executor, empty_db, "딸기 재고랑 보관법")

        assert len(result.metrics) == 2
        assert result.metrics[0].iteration == 1
        assert result.metrics[1].iteration == 1

    def test_is_empty_result_helper(self):
        """_is_empty_result 헬퍼 함수 검증."""
        assert _is_empty_result("FAQ에서 관련 내용을 찾을 수 없습니다.") is True
        assert _is_empty_result("'딸기' 검색 결과가 없습니다.") is True
        assert _is_empty_result("조회된 주문이 없습니다.") is True
        assert _is_empty_result("딸기는 150개 재고가 있습니다.") is False
        assert _is_empty_result("주문번호: #100") is False

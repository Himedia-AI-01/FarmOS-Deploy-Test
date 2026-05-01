"""SupervisorExecutor — LangChain tool calling 기반 오케스트레이터."""
import asyncio
import logging
import re
import time

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ai.agent.executor import (
    AgentExecutor,
    AgentResult,
    RequestContext,
    TraceStep,
    ToolMetricData,
    _history_to_lc,
    _log_trace,
    _parse_answer,
)
from ai.agent.responses import (
    LOGIN_REQUIRED,
    MAX_ITERATIONS_EXCEEDED,
    LLM_GENERATION_FAILED,
)

logger = logging.getLogger(__name__)

# 취소/교환 의도 감지 키워드 (_detect_order_action에서 사용)
_CANCEL_KEYWORDS: frozenset[str] = frozenset({"취소", "cancel", "환불"})
_EXCHANGE_KEYWORDS: frozenset[str] = frozenset({
    "교환", "exchange", "반품", "교체",
    "벌레", "이물질", "불량", "상함", "파손", "하자", "오배송",
    "썩음", "곰팡이", "망가", "깨짐", "냄새", "이상해", "상했",
})

# LLM 없이 OrderGraph로 직접 라우팅하는 명확한 접수 패턴.
# 단순 언급·문의가 아니라 "접수 실행" 의도가 명확한 복합 표현만 포함합니다.
# 이 외 모든 메시지는 Supervisor LLM(_run_loop)이 판단합니다.
_ORDER_FASTPATH_PATTERNS: frozenset[str] = frozenset({
    # 동사 결합형
    "취소해줘", "취소해주세요", "취소해",
    "교환해줘", "교환해주세요", "교환해",
    "반품해줘", "반품해주세요", "반품해",
    "환불해줘", "환불해주세요", "환불해",
    # 신청/접수 명사형
    "취소 신청", "취소신청",
    "교환 신청", "교환신청",
    "반품 신청", "반품신청",
})

# CS 에이전트가 교환/반품 선택지를 제시한 직후 사용자 응답을 OrderGraph로 라우팅
# CS_OUTPUT_PROMPT의 "교환과 반품·환불 중 원하시는 처리 방법" 문구를 기준으로 감지
_CS_HANDOFF_MARKER = "교환과 반품·환불 중 원하시는 처리 방법"
_CS_HANDOFF_SELECTIONS: frozenset[str] = frozenset({
    # 교환 선택
    "교환", "1", "1번", "교환이요", "교환요", "교환 원해요", "교환할게요",
    "교환 원합니다", "교환하고 싶어요", "교환 신청",
    # 반품/환불 선택
    "반품", "2", "2번", "반품이요", "반품요", "환불", "환불이요",
    "반품·환불", "반품/환불", "반품 원해요", "반품할게요",
    "반품 원합니다", "환불 원해요", "반품 신청",
})

_SUPERVISOR_TOOL_TO_INTENT: dict[str, str] = {"call_cs_agent": "other"}


# ── Supervisor 도구 스키마 (Pydantic → LLM 라우팅용) ──────────────────────────

class CallCSAgentInput(BaseModel):
    """CS 에이전트에게 조회·안내 질문을 위임합니다.
    상품 재고/가격, 보관법, 제철 정보, 정책 안내, FAQ, 배송 현황 조회 등에 사용합니다.
    """
    model_config = ConfigDict(title="call_cs_agent")
    query: str = Field(description="CS 에이전트에게 전달할 질문")


class CallOrderAgentInput(BaseModel):
    """주문 취소·교환·반품 접수를 Order 에이전트에게 위임합니다.
    반드시 로그인한 사용자에게만 사용합니다. 정책 안내가 아닌 실제 접수 처리입니다.
    """
    model_config = ConfigDict(title="call_order_agent")
    query: str = Field(description="Order 에이전트에게 전달할 내용")


# ── Supervisor 실행기 ─────────────────────────────────────────────────────────

class SupervisorExecutor:
    """Supervisor — LangChain tool calling으로 CS / Order 에이전트를 오케스트레이션."""

    def __init__(
        self,
        primary,
        fallback,
        cs_executor: AgentExecutor,
        cs_input_prompt: str,
        cs_output_prompt: str,
        order_graph,
        max_iterations: int = 5,
    ):
        self.primary = primary
        self.fallback = fallback
        self.cs_executor = cs_executor
        self.cs_input_prompt = cs_input_prompt
        self.cs_output_prompt = cs_output_prompt
        self.order_graph = order_graph
        self.max_iterations = max_iterations

        # model_config의 title이 LangChain에서 도구 이름으로 사용됨
        supervisor_tools = [CallCSAgentInput, CallOrderAgentInput]
        primary_bound = primary.bind_tools(supervisor_tools)
        if fallback:
            self._llm = primary_bound.with_fallbacks([fallback.bind_tools(supervisor_tools)])
            self._output_llm = primary.with_fallbacks([fallback])
        else:
            self._llm = primary_bound
            self._output_llm = primary

    # ── 진입점 ────────────────────────────────────────────────────────────────

    async def run(
        self,
        db: Session,
        user_message: str,
        user_id: int | None,
        history: list[dict],
        input_system: str,
        output_system: str,
        session_id: int | None = None,
        context: RequestContext | None = None,
    ) -> AgentResult:
        ctx = context or RequestContext.build(user_id)
        suffix = ctx.to_system_suffix()
        input_with_ctx = input_system + suffix
        # output_system 파라미터는 인터페이스 호환성 유지용 — 내부적으로 미사용

        # 1순위: 진행 중인 OrderGraph 플로우 → Supervisor LLM 판단 없이 직접 재개
        if session_id and user_id and await self._has_pending_order_flow(session_id):
            response_text = await self._call_order_agent(user_message, user_id, session_id, db)
            return AgentResult(
                answer=response_text,
                intent=_detect_order_action(user_message),
                escalated=False,
                tools_used=["call_order_agent"],
            )

        # 2순위: CS 에이전트 교환/반품 선택지 제시 후 사용자 선택 → OrderGraph 직접 라우팅
        # "교환" 단독 메시지는 action verb가 없어 _fast_route가 "cs"로 잘못 라우팅하는 문제 수정
        if _is_cs_handoff_reply(user_message, history):
            if not user_id or not session_id:
                return AgentResult(
                    answer=LOGIN_REQUIRED,
                    intent=_resolve_handoff_action(user_message),
                    escalated=False,
                    tools_used=[],
                )
            handoff_action = _resolve_handoff_action(user_message)
            response_text = await self._call_order_agent(
                user_message, user_id, session_id, db, force_action=handoff_action
            )
            return AgentResult(
                answer=response_text,
                intent=_resolve_handoff_action(user_message),
                escalated=False,
                tools_used=["call_order_agent"],
            )

        # 3순위: 명확한 접수 패턴 fast-path — Supervisor LLM 호출 생략
        # 동사 결합형·신청 명사형처럼 접수 의도가 100% 명확한 패턴만 해당합니다.
        # 그 외 모든 경우(정책 문의, 단순 키워드, 애매한 표현)는 Supervisor LLM이 판단합니다.
        if _is_order_fastpath(user_message):
            if not user_id or not session_id:
                return AgentResult(
                    answer=LOGIN_REQUIRED,
                    intent=_detect_order_action(user_message),
                    escalated=False,
                    tools_used=[],
                )
            response_text = await self._call_order_agent(user_message, user_id, session_id, db)
            return AgentResult(
                answer=response_text,
                intent=_detect_order_action(user_message),
                escalated=False,
                tools_used=["call_order_agent"],
            )

        # 4순위: Supervisor LLM — CS / Order 에이전트 선택을 LLM에 위임
        return await self._run_loop(
            db=db,
            user_message=user_message,
            user_id=user_id,
            session_id=session_id,
            history=history,
            input_system=input_with_ctx,
            output_system=output_system,
        )

    # ── 루프 ──────────────────────────────────────────────────────────────────

    async def _run_loop(
        self,
        db: Session,
        user_message: str,
        user_id: int | None,
        session_id: int | None,
        history: list[dict],
        input_system: str,
        output_system: str,
    ) -> AgentResult:
        messages: list[BaseMessage] = _history_to_lc(history) + [HumanMessage(content=user_message)]
        tools_used: list[str] = []
        trace: list[TraceStep] = []
        metrics: list[ToolMetricData] = []

        for iteration in range(self.max_iterations):
            response: AIMessage = await self._llm.ainvoke(
                [SystemMessage(content=input_system)] + messages
            )

            # 도구 호출 없음 → 직접 답변 (엣지 케이스)
            if not response.tool_calls:
                if tools_used:
                    synth: AIMessage = await self._output_llm.ainvoke(
                        [SystemMessage(content=output_system)] + messages + [response]
                    )
                    raw_answer = synth.content or LLM_GENERATION_FAILED
                else:
                    raw_answer = response.content or LLM_GENERATION_FAILED

                answer = _parse_answer(raw_answer)
                first = tools_used[0] if tools_used else None
                intent = (
                    _detect_order_action(user_message) if first == "call_order_agent"
                    else _SUPERVISOR_TOOL_TO_INTENT.get(first, "other") if first else "other"
                )
                _log_trace(trace, user_message)
                return AgentResult(
                    answer=answer, intent=intent, escalated=False,
                    tools_used=tools_used, trace=trace, metrics=metrics,
                )

            # CS는 병렬, Order는 순차
            cs_indexed = [(i, tc) for i, tc in enumerate(response.tool_calls) if tc["name"] == "call_cs_agent"]
            order_indexed = [(i, tc) for i, tc in enumerate(response.tool_calls) if tc["name"] == "call_order_agent"]

            timed_cs: list[tuple | None] = [None] * len(response.tool_calls)

            # CS 에이전트 병렬 실행
            if cs_indexed:
                cs_results = await asyncio.gather(*(
                    self._timed_cs_call(tc, db, user_id, session_id)
                    for _, tc in cs_indexed
                ))
                for (i, _), res in zip(cs_indexed, cs_results):
                    timed_cs[i] = res

            # Order 에이전트 순차 실행 → 결과를 즉시 반환
            for i, tc in order_indexed:
                if not user_id or not session_id:
                    result_str = LOGIN_REQUIRED
                    latency_ms = 0
                else:
                    t0 = time.monotonic()
                    result_str = await self._call_order_agent(
                        tc["args"].get("query", user_message), user_id, session_id, db
                    )
                    latency_ms = int((time.monotonic() - t0) * 1000)

                tools_used.append("call_order_agent")
                trace.append(TraceStep(
                    tool="call_order_agent",
                    arguments=tc.get("args", {}),
                    result=result_str[:500],
                    iteration=iteration + 1,
                    source="action",
                ))
                metrics.append(ToolMetricData(
                    tool_name="call_order_agent",
                    intent=_detect_order_action(user_message),
                    success=True,
                    latency_ms=latency_ms,
                    empty_result=False,
                    iteration=iteration + 1,
                ))
                # OrderGraph 응답은 Supervisor LLM 재합성 없이 즉시 반환
                _log_trace(trace, user_message)
                return AgentResult(
                    answer=result_str,
                    intent=_detect_order_action(user_message),
                    escalated=False,
                    tools_used=tools_used, trace=trace, metrics=metrics,
                )

            # CS 단독 호출 pass-through — Supervisor LLM 재합성 생략
            valid = [(i, r) for i, r in enumerate(timed_cs) if r is not None]
            if len(valid) == 1:
                _, (tc, cs_result, latency_ms) = valid[0]
                tools_used.append("call_cs_agent")
                result_str = cs_result.answer if isinstance(cs_result, AgentResult) else str(cs_result)
                trace.append(TraceStep(
                    tool="call_cs_agent",
                    arguments=tc.get("args", {}),
                    result=result_str[:500],
                    iteration=iteration + 1,
                    source="action",
                ))
                metrics.append(ToolMetricData(
                    tool_name="call_cs_agent",
                    intent="other",
                    success=True,
                    latency_ms=latency_ms,
                    empty_result=False,
                    iteration=iteration + 1,
                ))
                if isinstance(cs_result, AgentResult):
                    _log_trace(trace, user_message)
                    return AgentResult(
                        answer=_parse_answer(cs_result.answer),
                        intent=cs_result.intent,
                        escalated=cs_result.escalated,
                        tools_used=tools_used + cs_result.tools_used,
                        trace=trace,
                        metrics=metrics + cs_result.metrics,
                    )

            # 복수 CS 호출 → LLM 재합성
            tool_messages = []
            covered: set[int] = set()
            for i, res in valid:
                tc, cs_result, latency_ms = res
                result_str = cs_result.answer if isinstance(cs_result, AgentResult) else str(cs_result)
                tools_used.append("call_cs_agent")
                trace.append(TraceStep(
                    tool="call_cs_agent",
                    arguments=tc.get("args", {}),
                    result=result_str[:500],
                    iteration=iteration + 1,
                    source="action",
                ))
                metrics.append(ToolMetricData(
                    tool_name="call_cs_agent",
                    intent="other",
                    success=True,
                    latency_ms=latency_ms,
                    empty_result=False,
                    iteration=iteration + 1,
                ))
                tool_messages.append(ToolMessage(content=result_str, tool_call_id=response.tool_calls[i]["id"]))
                covered.add(i)

            # 처리되지 않은 tool_call(알 수 없는 도구명)에 fallback ToolMessage 삽입
            # AIMessage에는 모든 tool_calls가 있으므로, 대응하는 ToolMessage가 없으면
            # 다음 LLM 호출에서 "No tool output found" 400이 발생함
            for i, tc in enumerate(response.tool_calls):
                if i not in covered:
                    logger.warning(
                        "[supervisor] 처리되지 않은 tool_call '%s' (iter=%d) — fallback ToolMessage 삽입",
                        tc["name"], iteration + 1,
                    )
                    tool_messages.append(ToolMessage(
                        content=f"'{tc['name']}'은 지원하지 않는 도구입니다.",
                        tool_call_id=tc["id"],
                    ))

            messages.append(response)
            messages.extend(tool_messages)

        # 최대 반복 초과
        logger.warning("[supervisor] 최대 반복 초과. 에스컬레이션.")
        _log_trace(trace, user_message)
        return AgentResult(
            answer=MAX_ITERATIONS_EXCEEDED,
            intent="escalation",
            escalated=True,
            tools_used=tools_used, trace=trace, metrics=metrics,
        )

    # ── CS 에이전트 호출 ──────────────────────────────────────────────────────

    async def _timed_cs_call(
        self, tc: dict, db: Session, user_id: int | None, session_id: int | None
    ) -> tuple[dict, AgentResult, int]:
        t0 = time.monotonic()
        result = await self.cs_executor.run(
            db=db,
            user_message=tc["args"].get("query", ""),
            user_id=user_id,
            session_id=session_id,
            history=[],
            input_system=self.cs_input_prompt,
            output_system=self.cs_output_prompt,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info("[supervisor] call_cs_agent latency=%dms", latency_ms)
        return (tc, result, latency_ms)

    # ── OrderGraph 호출 ───────────────────────────────────────────────────────

    async def _has_pending_order_flow(self, session_id: int) -> bool:
        try:
            config = {"configurable": {"thread_id": str(session_id)}}
            snapshot = await self.order_graph.aget_state(config)
            return bool(snapshot.next)
        except Exception:
            return False

    async def _call_order_agent(
        self, query: str, user_id: int, session_id: int, db: Session,
        force_action: str | None = None,
    ) -> str:
        from langgraph.types import Command
        from ai.agent.order_graph.state import OrderState

        config = {"configurable": {"thread_id": str(session_id), "db": db}}

        try:
            snapshot = await self.order_graph.aget_state(config)
            # force_action: CS 핸드오프 경로에서 "1"/"2" 같은 짧은 선택 응답이
            # _detect_order_action 기본값("cancel")으로 잘못 판정되는 것을 방지.
            new_action = force_action if force_action else _detect_order_action(query)

            pending_action = snapshot.values.get("action") if snapshot.next else None
            intent_mismatch = (
                pending_action is not None
                and pending_action != new_action
                and any(kw in query for kw in _EXCHANGE_KEYWORDS | _CANCEL_KEYWORDS)
            )

            if snapshot.next and not intent_mismatch:
                logger.info("[order_graph] 플로우 재개 — session=%d", session_id)
                await self.order_graph.ainvoke(Command(resume=query), config)
            else:
                if intent_mismatch:
                    logger.info(
                        "[order_graph] 의도 불일치 — 기존=%s, 신규=%s (session=%d)",
                        pending_action, new_action, session_id,
                    )
                initial_state: OrderState = {
                    "action": new_action,
                    "user_id": user_id,
                    "session_id": session_id,
                    "user_message": query,
                    "order_id": None,
                    "order_display": None,
                    "selected_items": [],
                    "reason": None,
                    "refund_method": None,
                    "stock_note": "",
                    "confirmed": None,
                    "abort": False,
                    "confirmation_attempts": 0,
                    "ticket_id": None,
                    "response": "",
                    "is_pending": True,
                }
                logger.info("[order_graph] 신규 플로우 시작 — session=%d action=%s", session_id, new_action)
                await self.order_graph.ainvoke(initial_state, config)

            new_snapshot = await self.order_graph.aget_state(config)

            if new_snapshot.next and new_snapshot.tasks:
                interrupts = new_snapshot.tasks[0].interrupts
                if interrupts:
                    return str(interrupts[0].value)

            final_state = new_snapshot.values
            response = final_state.get("response", "처리가 완료되었습니다.")
            logger.info("[order_graph] 완료 — session=%d ticket=%s", session_id, final_state.get("ticket_id"))
            return response

        except Exception as e:
            logger.error("[order_graph] 오류 — session=%d: %s", session_id, e)
            return "주문 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 고객센터로 문의해 주세요."


# ── 헬퍼 ───────────────────────────────────────────────────────────────────────


def _is_order_fastpath(user_message: str) -> bool:
    """접수 실행 의도가 명확한 패턴인지 확인 — Supervisor LLM 호출 생략 조건.

    _ORDER_FASTPATH_PATTERNS에 정의된 복합 표현이 메시지에 단어 경계로 포함된 경우만 True를 반환합니다.
    패턴 앞뒤에 한글 글자가 이어지면 False — 부분 단어 오매칭 방지.
    단순 키워드 언급("취소"), 정책 문의("취소 방법"), 애매한 표현은 모두 False → LLM이 판단.

    예시:
      "이 주문 취소해줘"          → True  (패턴 앞뒤 경계 확인)
      "교환 신청"                 → True  (패턴 정확 일치)
      "교환신청서 작성방법"       → False (패턴 뒤 한글 '서' 연속 → 오매칭 방지)
      "취소해줘야 하나요?"        → False (패턴 뒤 한글 '야' 연속 → 오매칭 방지)
      "취소 방법 알려줘"          → False (LLM 판단)
      "교환하면 비용 드나요?"     → False (LLM 판단)
      "취소"                      → False (LLM 판단)
    """
    msg = user_message.strip().lower()
    for p in _ORDER_FASTPATH_PATTERNS:
        # 패턴 내 공백은 \s* 로 대체 (띄어쓰기 변형 허용)
        # 앞뒤에 한글 완성형 글자(AC00–D7A3)가 없어야 함 — 부분 단어 일치 방지
        pattern = r'(?<![가-힣])' + re.sub(r'\s+', r'\\s*', re.escape(p)) + r'(?![가-힣])'
        if re.search(pattern, msg):
            return True
    return False


def _detect_order_action(query: str) -> str:
    """쿼리에서 교환/취소 의도 감지. 기본값: 'cancel'."""
    q = query.lower()
    exchange_score = sum(1 for kw in _EXCHANGE_KEYWORDS if kw in q)
    cancel_score = sum(1 for kw in _CANCEL_KEYWORDS if kw in q)
    return "exchange" if exchange_score > cancel_score else "cancel"


def _is_cs_handoff_reply(user_message: str, history: list[dict]) -> bool:
    """CS 에이전트가 교환/반품 선택지를 제시한 직후 사용자의 선택 응답인지 확인.

    직전 봇 메시지에 _CS_HANDOFF_MARKER가 포함되어 있고,
    사용자 응답이 _CS_HANDOFF_SELECTIONS 중 하나인 경우 True.
    내부 공백을 정규화하여 "교환 이요", "반품  원해요" 같은 변형도 매칭한다.
    """
    if not history:
        return False
    last_bot = next(
        (h.get("content") or h.get("text", "") for h in reversed(history) if h.get("role") in ("bot", "assistant")),
        None,
    )
    if not last_bot or _CS_HANDOFF_MARKER not in last_bot:
        return False
    normalized = re.sub(r"\s+", "", user_message.strip().lower())
    return normalized in {re.sub(r"\s+", "", s) for s in _CS_HANDOFF_SELECTIONS}


def _resolve_handoff_action(user_message: str) -> str:
    """CS 핸드오프 맥락에서 숫자 선택(1/2)을 포함한 교환/취소 액션 해석.

    _detect_order_action 대신 사용 — "1"→교환, "2"→취소를 명시적으로 처리.
    """
    q = user_message.strip().lower()
    if q in {"1", "1번"}:
        return "exchange"
    if q in {"2", "2번"}:
        return "cancel"
    return _detect_order_action(user_message)

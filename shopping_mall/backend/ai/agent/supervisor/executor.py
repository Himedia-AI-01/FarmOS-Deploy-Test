"""SupervisorExecutor — LangChain tool calling 기반 오케스트레이터."""
import asyncio
import logging
import re
import time
from typing import Literal

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
    _finalize_customer_answer,
    _parse_answer,
)
from ai.agent.responses import (
    LOGIN_REQUIRED,
    MAX_ITERATIONS_EXCEEDED,
    LLM_GENERATION_FAILED,
    STOCK_QUERY_NEEDS_TARGET,
    refusal_response,
)

logger = logging.getLogger(__name__)

# 취소/교환/변경 의도 감지 키워드 (_detect_order_action에서 사용)
_CANCEL_KEYWORDS: frozenset[str] = frozenset({"취소", "cancel", "환불"})
_CHANGE_KEYWORDS: frozenset[str] = frozenset({
    "변경", "수정", "바꾸", "바꿔", "change",
    "배송지", "주소", "연락처", "전화번호", "요청사항",
    "수량 변경", "수량수정",
})
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
    "변경해줘", "변경해주세요", "변경해",
    "수정해줘", "수정해주세요", "수정해",
    "바꿔줘", "바꿔주세요",
    # 신청/접수 명사형
    "취소 신청", "취소신청",
    "교환 신청", "교환신청",
    "반품 신청", "반품신청",
    "변경 신청", "변경신청",
})

_ORDER_SHORT_ACTION_PHRASES: frozenset[str] = frozenset({
    "주문 취소",
    "주문취소",
    "주문 교환",
    "주문교환",
    "주문 반품",
    "주문반품",
    "주문 환불",
    "주문환불",
    "주문 변경",
    "주문변경",
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

_VAGUE_STOCK_MESSAGES: frozenset[str] = frozenset({
    "재고 확인",
    "재고 확인해줘",
    "재고 확인해주세요",
    "재고 확인해 주세요",
    "재고 알려줘",
    "재고 알려주세요",
    "재고 있나요",
    "재고 있어요",
})
_VAGUE_STOCK_COMPACT_MESSAGES: frozenset[str] = frozenset(
    re.sub(r"\s+", "", msg) for msg in _VAGUE_STOCK_MESSAGES
)

_OTHER_USER_INFO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:다른|타인|남의|타고객|타\s*고객|제\s*3자).{0,20}(?:주문|배송|송장|주소|연락처|전화번호|휴대폰|이메일|개인정보|정보|구매\s*내역)"),
    re.compile(r"(?:홍길동|김철수|이영희|박영희|아무개)\s*(?:님|씨)?\s*.{0,20}(?:주문|배송|송장|주소|연락처|전화번호|휴대폰|이메일|개인정보|정보)"),
    re.compile(r"(?:회원|고객|사용자|유저)\s*(?:id|아이디|번호)?\s*#?\d+.{0,20}(?:주문|배송|송장|주소|연락처|전화번호|휴대폰|이메일|개인정보|정보|조회)"),
    re.compile(r"(?:user_id|shop_user_id|customer_id)\s*[:=#]?\s*\d+"),
)

_INTERNAL_INFO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:오늘|당일|금일|어제|일간|주간|월간|이번\s*달|지난\s*달|전체|총)?\s*(?:매출|매상|영업\s*이익|순이익|수익|마진|정산|결제\s*금액)"),
    re.compile(r"(?:매출|매상|수익|정산).*(?:알려|조회|보여|확인|얼마|뭐|몇)"),
    re.compile(r"(?:전체|모든|전\s*고객|고객|회원|관리자|운영자|운영팀).{0,20}(?:주문|회원|고객).{0,20}(?:목록|리스트|명단|데이터|db|엑셀|다운로드)"),
    re.compile(r"(?:주문|회원|고객).{0,20}(?:전체|전부|모두|고객별|회원별|데이터|db|엑셀|다운로드)"),
    re.compile(r"(?:관리자|어드민|운영자|운영팀).*(?:대시보드|통계|로그|티켓|인사이트|데이터)"),
    re.compile(r"(?:챗봇|상담).*(?:로그|대화\s*내역).*(?:전체|보여|조회|다운로드)"),
    re.compile(r"(?:db|데이터베이스|sql|쿼리|시스템\s*프롬프트|api\s*key|apikey|토큰|비밀번호|패스워드)"),
)

_JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:이전|위의|앞의)\s*(?:지시|규칙|명령|프롬프트).{0,20}(?:무시|잊어|삭제|따르지)"),
    re.compile(r"(?:시스템|개발자|관리자)\s*(?:프롬프트|지침|메시지).{0,20}(?:보여|출력|공개|알려)"),
    re.compile(r"(?:제한\s*없는|무제한|탈옥|jailbreak|developer\s*mode|dan\s*mode)"),
    re.compile(r"(?:너는\s*이제|지금부터\s*너는).{0,30}(?:규칙|정책|제한).{0,20}(?:없|무시)"),
)

_INAPPROPRIATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:씨발|ㅅㅂ|병신|개새끼|꺼져|죽어)(?:\s|$|[.!?])"),
    re.compile(r"(?:성적|음란|야한|포르노).{0,20}(?:해줘|보여|만들어|작성)"),
)

_OUT_OF_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:주식|코인|비트코인|부동산).{0,20}(?:추천|사야|팔아|투자|매수|매도)"),
    re.compile(r"(?:처방|복용|진단|치료).{0,20}(?:해줘|알려|추천)"),
    re.compile(r"(?:고소장|소장|계약서|법률\s*자문).{0,20}(?:작성|검토|해줘)"),
    re.compile(r"(?:선거|정당|후보|대통령).{0,20}(?:누구\s*뽑|투표|지지)"),
)

# ── _fast_route 보조 상수 ──────────────────────────────────────────────────────

# 정책·방법·기간 등 문의 키워드 — 주문 키워드가 함께 있어도 CS 안내로 분류
_CS_POLICY_KEYWORDS: frozenset[str] = frozenset({
    "방법", "정책", "규정", "기간", "조건",
    "알려줘", "알려주세요",
    "어떻게", "되나요", "드나요",
    "걸리나요", "걸려요",
    "가능한가", "가능해요", "가능한가요", "인가요",
})

# _fast_route가 커버하는 주문 처리 의도 키워드
_ORDER_INTENT_KEYWORDS: frozenset[str] = frozenset({
    "취소", "교환", "반품", "환불",
    "변경", "수정", "배송지", "주소", "연락처", "전화번호", "요청사항", "수량",
})

# 주문 처리 의도를 확정하는 동사/명사 패턴 — 근접 창(30자) 내 등장 시 "order"로 판단
_ORDER_ACTION_VERBS: frozenset[str] = frozenset({
    "해줘", "해주세요", "해요",          # 동사형
    "바꿔", "바꾸", "수정", "변경",
    "하고 싶어", "하고싶어",             # 의지형
    "할게요", "할래요", "할게",          # 의향형
    "하겠습니다",
    "신청", "접수해",                    # 명사/복합 동사
    "도와주세요",
    "원해", "원합니다", "원해요",
})

# 주문 키워드와 동사 사이 최대 거리(문자 수) — 초과 시 관련 없는 문장으로 판단
_ORDER_VERB_PROXIMITY: int = 30


# ── Supervisor 도구 스키마 (Pydantic → LLM 라우팅용) ──────────────────────────

class CallCSAgentInput(BaseModel):
    """CS 에이전트에게 조회·안내 질문을 위임합니다.
    상품 재고/가격, 보관법, 제철 정보, 정책 안내, FAQ, 배송 현황 조회 등에 사용합니다.
    """
    model_config = ConfigDict(title="call_cs_agent")
    query: str = Field(description="CS 에이전트에게 전달할 질문")
    tool_hint: Literal[
        "search_faq",
        "search_policy",
        "get_order_status",
        "search_products",
        "get_product_detail",
    ] | None = Field(
        default=None,
        description=(
            "CS 에이전트가 바로 실행해도 되는 read-only 도구 힌트. "
            "확실하지 않으면 null로 두세요."
        ),
    )
    tool_args: dict | None = Field(
        default=None,
        description=(
            "tool_hint에 전달할 도구 인자. 도구 인자를 확실히 구성할 수 있을 때만 제공하세요. "
            "예: search_products는 {'query':'딸기','check_stock':true,'limit':5}."
        ),
    )


class CallOrderAgentInput(BaseModel):
    """주문 취소·교환·반품·변경 접수를 Order 에이전트에게 위임합니다.
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
        # ── [DIAG] 진단용 로그 — 확인 후 제거 예정 ──────────────────────────
        logger.info("[DIAG] SupervisorExecutor.run 진입 — user_id=%s session_id=%s", user_id, session_id)
        # ────────────────────────────────────────────────────────────────────
        ctx = context or RequestContext.build(user_id)
        suffix = ctx.to_system_suffix()
        input_with_ctx = input_system + suffix
        # output_system 파라미터는 인터페이스 호환성 유지용 — 내부적으로 미사용

        # 0순위: 내부 운영 정보·민감 정보 요청은 LLM/도구 호출 전에 구조적으로 차단한다.
        refusal_reason = _preflight_refusal_reason(user_message)
        if refusal_reason:
            logger.info("[supervisor] preflight refusal — user_id=%s session_id=%s intent=other reason=%s", ctx.user_id, session_id, refusal_reason)
            return AgentResult(
                answer=refusal_response(refusal_reason),
                intent="other",
                escalated=False,
                tools_used=["preflight_refusal"],
            )

        # 1순위: 상품명/카테고리 없는 재고 조회는 LLM 표현 변동 없이 필요한 정보만 요청한다.
        if _is_vague_stock_request(user_message):
            return AgentResult(
                answer=STOCK_QUERY_NEEDS_TARGET,
                intent="stock",
                escalated=False,
                tools_used=[],
            )

        # 2순위: 진행 중인 OrderGraph 플로우 → Supervisor LLM 판단 없이 직접 재개
        if session_id and user_id and await self._has_pending_order_flow(session_id):
            response_text = await self._call_order_agent(user_message, user_id, session_id, db)
            return AgentResult(
                answer=response_text,
                intent=_detect_order_action(user_message),
                escalated=False,
                tools_used=["call_order_agent"],
            )

        # 3순위: CS 에이전트 교환/반품 선택지 제시 후 사용자 선택 → OrderGraph 직접 라우팅
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

        # 4순위: 명확한 주문 처리 요청은 OrderGraph로 직접 라우팅한다.
        # _fast_route는 정책·방법 문의를 먼저 차단한 뒤, 주문 키워드와 실행 의지가
        # 가까이 붙은 표현("주문 취소하고 싶어")만 order로 판정한다.
        if _fast_route(user_message) == "order":
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

        # 5순위: Supervisor LLM — CS / Order 에이전트 선택을 LLM에 위임
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
            # ── [DIAG] LLM 호출 직전 ─────────────────────────────────────
            logger.info("[DIAG] _run_loop LLM 호출 시작 — iteration=%d", iteration)
            # ─────────────────────────────────────────────────────────────
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
                    answer=_finalize_customer_answer(answer, trace), intent=intent, escalated=False,
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
                        answer=_finalize_customer_answer(_parse_answer(cs_result.answer), trace),
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
            tool_hint=tc["args"].get("tool_hint"),
            tool_args=tc["args"].get("tool_args"),
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
        from ai.agent.order_graph.nodes import _is_flow_abort_intent

        config = {"configurable": {"thread_id": str(session_id), "db": db}}

        try:
            snapshot = await self.order_graph.aget_state(config)
            # force_action: CS 핸드오프 경로에서 "1"/"2" 같은 짧은 선택 응답이
            # _detect_order_action 기본값("cancel")으로 잘못 판정되는 것을 방지.
            new_action = force_action if force_action else _detect_order_action(query)

            pending_action = snapshot.values.get("action") if snapshot.next else None
            pending_abort = (
                pending_action is not None
                and _is_flow_abort_intent(query, pending_action)
            )
            intent_mismatch = (
                pending_action is not None
                and not pending_abort
                and pending_action != new_action
                and any(kw in query for kw in _EXCHANGE_KEYWORDS | _CANCEL_KEYWORDS | _CHANGE_KEYWORDS)
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
                    "change_type": None,
                    "change_detail": None,
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


def _preflight_refusal_reason(user_message: str) -> str | None:
    """LLM/도구 호출 전에 차단해야 하는 요청 사유를 판별한다.

    고객 상담 채널에서 다루면 안 되는 요청을 supervisor 진입부에서 먼저
    끊어, 잘못된 도구 선택이나 오류 메시지로 민감 정보 조회 가능성이
    암시되는 상황을 방지한다.
    """
    msg = re.sub(r"\s+", " ", user_message.strip().lower())
    if not msg:
        return None
    if any(pattern.search(msg) for pattern in _JAILBREAK_PATTERNS):
        return "jailbreak"
    if any(pattern.search(msg) for pattern in _OTHER_USER_INFO_PATTERNS):
        return "other_user_info"
    if any(pattern.search(msg) for pattern in _INTERNAL_INFO_PATTERNS):
        return "internal_info"
    if any(pattern.search(msg) for pattern in _INAPPROPRIATE_PATTERNS):
        return "inappropriate"
    if any(pattern.search(msg) for pattern in _OUT_OF_SCOPE_PATTERNS):
        return "out_of_scope"
    return None


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
    """쿼리에서 교환/취소/변경 의도 감지. 기본값: 'cancel'."""
    q = query.lower()
    exchange_score = sum(1 for kw in _EXCHANGE_KEYWORDS if kw in q)
    cancel_score = sum(1 for kw in _CANCEL_KEYWORDS if kw in q)
    change_score = sum(1 for kw in _CHANGE_KEYWORDS if kw in q)
    if change_score > max(exchange_score, cancel_score):
        return "change"
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


def _fast_route(message: str) -> str:
    """규칙 기반 사전 라우팅 — 'order' 또는 'cs' 반환.

    _is_order_fastpath()보다 넓은 범위를 커버하는 완전한 규칙 기반 분류기.
    동사형("취소해줘"), 의지형("반품하고 싶어요"), 접수 명사형("교환 접수해줘") 모두 처리.

    우선순위:
    1. CS 문의 키워드(정책/방법/규정 등) → "cs"  — 주문 키워드가 함께 있어도 우선
    2. 명확한 접수 패턴(_is_order_fastpath) → "order"
    3. 주문 키워드 + 근접 동사(30자 이내) → "order"
    4. 그 외(키워드 단독, 무관한 내용) → "cs"

    평가 실험(tests/eval/test_routing_accuracy.py)에서 단일 에이전트 baseline과
    비교 측정에 사용됩니다.
    """
    msg = message.strip().lower()

    # 1. CS 문의 키워드 최우선 차단
    if any(kw in msg for kw in _CS_POLICY_KEYWORDS):
        return "cs"

    # 2. 로그인 후 자주 누르는 단축 실행 표현
    if msg in _ORDER_SHORT_ACTION_PHRASES:
        return "order"

    # 3. 명확한 접수 패턴(fastpath)
    if _is_order_fastpath(message):
        return "order"

    # 4. 주문 키워드 + 근접 동사 — 키워드 위치에서 30자 창 내 탐색
    for kw in _ORDER_INTENT_KEYWORDS:
        if kw not in msg:
            continue
        kw_idx = msg.find(kw)
        window = msg[kw_idx: kw_idx + _ORDER_VERB_PROXIMITY]
        if any(v in window for v in _ORDER_ACTION_VERBS):
            return "order"

    # 5. 키워드 단독·무관 내용 → CS
    return "cs"


def _is_vague_stock_request(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    compact = re.sub(r"\s+", "", normalized)
    return normalized in _VAGUE_STOCK_MESSAGES or compact in _VAGUE_STOCK_COMPACT_MESSAGES


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

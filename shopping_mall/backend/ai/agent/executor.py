"""CS 에이전트 실행기 — LangChain tool calling 기반."""
import asyncio
import logging
import re
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from ai.agent.cs_tools import (
    TOOL_SOURCE,
    TOOL_TO_INTENT,
    build_cs_tools,
)
from ai.agent.responses import (
    LOGIN_REQUIRED,
    MAX_ITERATIONS_EXCEEDED,
    LLM_GENERATION_FAILED,
    TRUNCATION_SUFFIX,
    refusal_response,
)

# 하위 호환성 별칭 — 이 모듈 내부 및 테스트에서 사용
LOGIN_REQUIRED_RESPONSE = LOGIN_REQUIRED

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10
MAX_ANSWER_LENGTH = 1000

# RAG 도구 — asyncio.gather로 병렬 실행 가능 (DB 세션 불필요)
_RAG_TOOL_NAMES: frozenset[str] = frozenset({
    "search_faq",
    "search_policy",
})

# Supervisor가 확정해서 넘길 수 있는 read-only 도구만 직접 실행한다.
# 주문 취소/환불 같은 action 도구는 OrderGraph 확인 플로우를 유지한다.
_DIRECT_HINT_TOOL_NAMES: frozenset[str] = frozenset({
    "search_faq",
    "search_policy",
    "get_order_status",
    "search_products",
    "get_product_detail",
})


# ── 요청 컨텍스트 ──────────────────────────────────────────────────────────────

@dataclass
class RequestContext:
    """LLM에 주입할 요청 시점의 세션 상태."""
    user_id: int | None
    is_logged_in: bool
    current_date: str
    current_time: str

    @classmethod
    def build(cls, user_id: int | None) -> "RequestContext":
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        return cls(
            user_id=user_id,
            is_logged_in=user_id is not None,
            current_date=now.strftime("%Y-%m-%d"),
            current_time=now.strftime("%H:%M"),
        )

    def to_system_suffix(self) -> str:
        login_status = "로그인" if self.is_logged_in else "비로그인"
        return (
            f"\n\n## 현재 요청 컨텍스트\n"
            f"- 날짜/시각: {self.current_date} {self.current_time}\n"
            f"- 사용자 상태: {login_status}\n"
            f"- 주문 조회 가능: {'예' if self.is_logged_in else '아니오 (로그인 필요)'}"
        )


@dataclass
class TraceStep:
    """도구 호출 한 단계의 추론 기록."""
    tool: str
    arguments: dict
    result: str
    iteration: int
    source: str = "rag"


@dataclass
class ToolMetricData:
    """도구 호출 1건의 성능/품질 메트릭."""
    tool_name: str
    intent: str
    success: bool
    latency_ms: int
    empty_result: bool
    iteration: int


@dataclass
class AgentResult:
    answer: str
    intent: str
    escalated: bool
    tools_used: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    metrics: list[ToolMetricData] = field(default_factory=list)
    # search_faq 도구가 인용한 FaqDoc DB ID 목록 (FaqCitation 저장에 사용)
    cited_faq_ids: list[int] = field(default_factory=list)


# ── 빈 결과 판별 ───────────────────────────────────────────────────────────────

_EMPTY_RESULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"찾을\s*수\s*없습니다"),
    re.compile(r"결과가\s*없습니다"),
    re.compile(r"조회된\s*주문이\s*없습니다"),
    re.compile(r"정보를\s*찾을\s*수\s*없습니다"),
    re.compile(r"검색\s*결과가\s*없습니다"),
)

_INTERNAL_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*\(\s*order_id\s*\)", re.IGNORECASE), ""),
    (re.compile(r"(?<![A-Za-z0-9_])order_id(?![A-Za-z0-9_])", re.IGNORECASE), "주문 번호"),
    (re.compile(r"(?<![A-Za-z0-9_])user_id(?![A-Za-z0-9_])", re.IGNORECASE), "고객 정보"),
    (re.compile(r"(?<![A-Za-z0-9_])session_id(?![A-Za-z0-9_])", re.IGNORECASE), "상담 정보"),
    (re.compile(r"(?<![A-Za-z0-9_])tracking_number(?![A-Za-z0-9_])", re.IGNORECASE), "송장번호"),
    (re.compile(r"(?<![A-Za-z0-9_])status(?![A-Za-z0-9_])", re.IGNORECASE), "상태"),
    (re.compile(r"(?<![A-Za-z0-9_])picked_up(?![A-Za-z0-9_])", re.IGNORECASE), "배송 준비 완료"),
    (re.compile(r"(?<![A-Za-z0-9_])in_transit(?![A-Za-z0-9_])", re.IGNORECASE), "배송 중"),
    (re.compile(r"(?<![A-Za-z0-9_])shipping(?![A-Za-z0-9_])", re.IGNORECASE), "배송 중"),
    (re.compile(r"(?<![A-Za-z0-9_])delivered(?![A-Za-z0-9_])", re.IGNORECASE), "배송 완료"),
    (re.compile(r"(?<![A-Za-z0-9_])pending(?![A-Za-z0-9_])", re.IGNORECASE), "결제 완료"),
    (re.compile(r"(?<![A-Za-z0-9_])preparing(?![A-Za-z0-9_])", re.IGNORECASE), "배송 준비 중"),
    (re.compile(r"(?<![A-Za-z0-9_])cancelled(?![A-Za-z0-9_])", re.IGNORECASE), "취소 완료"),
    (re.compile(r"(?<![A-Za-z0-9_])returned(?![A-Za-z0-9_])", re.IGNORECASE), "반품 완료"),
)

_POLICY_CITATION_RE = re.compile(r"\[([^\]\n]*(?:정책|규정)[^\]\n]*)\]")

_DISALLOWED_LEADING_TONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*다\s*알겠습니다[!.。．]*\s*", re.IGNORECASE),
    re.compile(r"^\s*네[,，]?\s*알겠습니다[!.。．]*\s*", re.IGNORECASE),
    re.compile(r"^\s*알겠습니다[!.。．]*\s*", re.IGNORECASE),
)


def _is_empty_result(result: str) -> bool:
    normalized = re.sub(r"\s+", " ", result).strip()
    return any(p.search(normalized) for p in _EMPTY_RESULT_PATTERNS)


# ── 응답 후처리 ────────────────────────────────────────────────────────────────

def _parse_answer(raw: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", raw, flags=re.MULTILINE)
    text = re.sub(r";\s+", ". ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) <= MAX_ANSWER_LENGTH:
        return text

    truncated = text[:MAX_ANSWER_LENGTH]
    last_sentence_end = max(
        truncated.rfind("다."),
        truncated.rfind("요."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    if last_sentence_end > MAX_ANSWER_LENGTH // 2:
        truncated = truncated[: last_sentence_end + 1]

    return truncated + TRUNCATION_SUFFIX


def _sanitize_internal_terms(answer: str) -> str:
    text = answer
    for pattern, replacement in _INTERNAL_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _sanitize_leading_tone(answer: str) -> str:
    text = answer
    for pattern in _DISALLOWED_LEADING_TONE_PATTERNS:
        text = pattern.sub("", text, count=1)
    return text.lstrip()


def _format_policy_citation(raw: str) -> str | None:
    match = _POLICY_CITATION_RE.search(raw)
    if not match:
        return None

    parts = [p.strip() for p in match.group(1).split(">") if p.strip()]
    if not parts:
        return None

    doc = parts[0]
    article = next((p for p in parts if re.search(r"제\s*\d+\s*조", p)), None)
    if article:
        article_no = re.search(r"제\s*\d+\s*조", article)
        return f"{doc} {article_no.group(0).replace(' ', '')}"
    if len(parts) >= 2:
        return f"{doc} {parts[-1]}"
    return doc


def _ensure_policy_citation(answer: str, trace: list[TraceStep]) -> str:
    if "근거:" in answer or not any(step.tool == "search_policy" for step in trace):
        return answer

    citations: list[str] = []
    for step in trace:
        if step.tool != "search_policy":
            continue
        citation = _format_policy_citation(step.result)
        if citation and citation not in citations:
            citations.append(citation)

    if not citations:
        return answer

    return f"{answer}\n\n(근거: {', '.join(citations[:3])})"


def _finalize_customer_answer(answer: str, trace: list[TraceStep] | None = None) -> str:
    text = _sanitize_leading_tone(_sanitize_internal_terms(answer))
    return _ensure_policy_citation(text, trace or [])


def _log_trace(trace: list[TraceStep], question: str) -> None:
    if not trace:
        logger.info("[trace] 질문='%s' → 도구 호출 없음 (직접 답변)", question[:60])
        return
    logger.info("[trace] 질문='%s' → %d단계 도구 호출", question[:60], len(trace))
    for step in trace:
        logger.info("  [%d] %s(%s) [%s]", step.iteration, step.tool, step.arguments, step.source)


def _history_to_lc(history: list[dict]) -> list[BaseMessage]:
    """dict 히스토리 → LangChain 메시지 변환."""
    messages: list[BaseMessage] = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role in ("assistant", "bot"):
            messages.append(AIMessage(content=content))
    return messages


def _format_order_status_answer(tool_result: str) -> str:
    """get_order_status 결과를 고객용 배송 현황 답변으로 결정적 포맷팅한다."""
    text = tool_result.strip()
    if not text or "조회된 주문이 없습니다" in text or text == LOGIN_REQUIRED_RESPONSE:
        return text

    total_orders: int | None = None
    shown_orders: int | None = None
    header_match = re.search(r"전체 주문:\s*(\d+)건\s*/\s*아래 최근\s*(\d+)건 표시", text)
    if header_match:
        total_orders = int(header_match.group(1))
        shown_orders = int(header_match.group(2))

    sections = [
        s.strip()
        for s in re.split(r"\n\s*---\s*\n", re.sub(r"^\[.*?\]\s*", "", text).strip())
        if s.strip()
    ]
    if not sections:
        return text

    lines = [f"최근 {len(sections)}건의 주문 배송 현황입니다."]
    has_expected_arrival = False

    for idx, section in enumerate(sections, start=1):
        fields: dict[str, str] = {}
        for raw_line in section.splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            fields[key.strip()] = value.strip()

        order_no = fields.get("주문번호", f"#{idx}")
        if order_no and not order_no.startswith("#"):
            order_no = f"#{order_no}"
        product = fields.get("상품", "상품 정보 없음")
        order_status = fields.get("주문상태")
        carrier = fields.get("택배사")
        tracking = fields.get("송장번호")
        shipping_status = fields.get("배송상태")
        expected = fields.get("예상 도착일")
        no_shipping = fields.get("배송정보")

        lines.append("")
        lines.append(f"{idx}. 주문 {order_no}")
        lines.append(f"   - 상품: {product}")
        if order_status:
            lines.append(f"   - 주문 상태: {order_status}")

        if no_shipping:
            lines.append("   - 배송 정보: 아직 등록되지 않았습니다.")
        else:
            if carrier:
                lines.append(f"   - 택배사: {carrier}")
            if tracking:
                lines.append(f"   - 송장번호: {tracking}")
            if shipping_status:
                lines.append(f"   - 배송 상태: {shipping_status}")
            if expected:
                has_expected_arrival = True
                lines.append(f"   - 도착 예정일: {expected}")

    if has_expected_arrival:
        lines.append("")
        lines.append("※ 도착 예정일은 주말·공휴일을 제외한 영업일 기준으로 안내됩니다.")

    if (
        total_orders is not None
        and shown_orders is not None
        and total_orders > shown_orders
    ):
        lines.append("")
        lines.append("다른 주문의 배송 현황도 확인해 드릴까요?")

    return "\n".join(lines)


def _reject_cross_user_order_lookup(tc: dict) -> tuple[str, int] | None:
    """get_order_status에 user_id 인자가 들어오면 즉시 거절한다."""
    if tc["name"] == "get_order_status" and "user_id" in tc.get("args", {}):
        logger.warning("get_order_status에 user_id 인자 감지 — 타인 정보 조회 시도 거절")
        return "__REFUSED__\n사유: other_user_info", 0
    return None


# ── CS 에이전트 실행기 ────────────────────────────────────────────────────────

class AgentExecutor:
    """CS 에이전트 — LangChain tool calling 루프.

    Primary LLM 실패 시 Fallback(Claude)으로 자동 전환합니다.
    """

    def __init__(self, primary, fallback, rag_service, tools=None, max_iterations: int | None = None):
        if tools is not None:
            warnings.warn(
                "tools 파라미터는 deprecated — build_cs_tools()로 요청마다 생성됩니다.",
                DeprecationWarning,
                stacklevel=2,
            )
        self.primary = primary
        self.fallback = fallback
        self.rag = rag_service
        self.max_iterations = max_iterations or settings.agent_max_iterations or MAX_ITERATIONS

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
        tool_hint: str | None = None,
        tool_args: dict | None = None,
    ) -> AgentResult:
        ctx = context or RequestContext.build(user_id)
        suffix = ctx.to_system_suffix()
        input_with_ctx = input_system + suffix
        output_with_ctx = output_system + suffix

        # 요청마다 db/user_id를 클로저로 바인딩한 도구 생성
        tools, tool_ctx = build_cs_tools(self.rag, db, user_id)
        tool_map = {t.name: t for t in tools}

        # Primary + Fallback 체인 구성
        primary_with_tools = self.primary.bind_tools(tools)
        if self.fallback:
            llm_with_tools = primary_with_tools.with_fallbacks(
                [self.fallback.bind_tools(tools)]
            )
            output_llm = self.primary.with_fallbacks([self.fallback])
        else:
            llm_with_tools = primary_with_tools
            output_llm = self.primary

        if tool_hint and tool_hint in _DIRECT_HINT_TOOL_NAMES:
            result = await self._run_with_tool_hint(
                output_llm, tool_map,
                user_message, history, output_with_ctx,
                tool_hint=tool_hint, tool_args=tool_args,
            )
        else:
            if tool_hint:
                logger.warning("[CS 에이전트] 유효하지 않은 tool_hint=%s — 기존 도구 선택 경로 사용", tool_hint)
            result = await self._run_single_pass(
                llm_with_tools, output_llm, tool_map,
                user_message, history, input_with_ctx, output_with_ctx,
            )
        # search_faq가 인용한 문서 ID를 결과에 주입 (FaqCitation 저장용)
        result.cited_faq_ids = tool_ctx.cited_faq_ids
        return result

    async def _run_with_tool_hint(
        self,
        output_llm,
        tool_map: dict,
        user_message: str,
        history: list[dict],
        output_system: str,
        tool_hint: str,
        tool_args: dict | None,
    ) -> AgentResult:
        """Supervisor가 지정한 read-only 도구를 바로 실행해 도구 선택 LLM 호출을 생략."""
        safe_args = tool_args if isinstance(tool_args, dict) else {}
        tc = {"name": tool_hint, "args": safe_args, "id": f"hint-{tool_hint}"}
        guarded = _reject_cross_user_order_lookup(tc)
        if guarded is not None:
            result, latency_ms = guarded
        else:
            result, latency_ms = await _invoke_tool(tc, tool_map)

        tools_used: list[str] = []
        trace: list[TraceStep] = []
        metrics: list[ToolMetricData] = []
        _record(tc, result, latency_ms, 1, tools_used, trace, metrics)

        if result == LOGIN_REQUIRED_RESPONSE:
            _log_trace(trace, user_message)
            return AgentResult(
                answer=LOGIN_REQUIRED_RESPONSE,
                intent=TOOL_TO_INTENT.get(tool_hint, "other"),
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        if result.startswith("__REFUSED__"):
            raw_reason = result.split("사유:", 1)[1].strip() if "사유:" in result else ""
            _log_trace(trace, user_message)
            return AgentResult(
                answer=refusal_response(raw_reason),
                intent=TOOL_TO_INTENT.get(tool_hint, "other"),
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        if tool_hint == "get_order_status":
            _log_trace(trace, user_message)
            return AgentResult(
                answer=_format_order_status_answer(result),
                intent=TOOL_TO_INTENT.get(tool_hint, "delivery"),
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        messages: list[BaseMessage] = _history_to_lc(history) + [HumanMessage(content=user_message)]
        synth_messages = [
            SystemMessage(content=output_system),
            *messages,
            SystemMessage(
                content=(
                    "아래 도구 실행 결과만 근거로 고객 답변을 작성하세요.\n"
                    f"- tool: {tool_hint}\n"
                    f"- arguments: {safe_args}\n"
                    f"- result:\n{result}"
                )
            ),
        ]
        try:
            synth: AIMessage = await output_llm.ainvoke(synth_messages)
            raw_answer = synth.content or LLM_GENERATION_FAILED
        except Exception as e:
            logger.error("[CS 에이전트] LLM 호출 오류 (hint 응답 생성): %s", e)
            raise

        has_empty_rag = _is_empty_result(result) and TOOL_SOURCE.get(tool_hint) == "rag"
        if has_empty_rag:
            trace.append(TraceStep(
                tool="_parametric_fallback",
                arguments={},
                result="RAG 결과 없음 — LLM 사전 지식으로 보완",
                iteration=1,
                source="parametric",
            ))

        _log_trace(trace, user_message)
        return AgentResult(
            answer=_finalize_customer_answer(_parse_answer(raw_answer), trace),
            intent=TOOL_TO_INTENT.get(tool_hint, "other"),
            escalated=False,
            tools_used=tools_used, trace=trace, metrics=metrics,
        )

    async def _run_single_pass(
        self,
        llm_with_tools,
        output_llm,
        tool_map: dict,
        user_message: str,
        history: list[dict],
        input_system: str,
        output_system: str,
    ) -> AgentResult:
        """단일 패스 실행 — 도구 선택(1회) → 병렬 실행 → 응답 생성(1회).

        CS_INPUT_PROMPT로 필요한 도구를 한 번에 결정하고,
        도구 결과를 수집한 뒤 CS_OUTPUT_PROMPT로 최종 응답을 생성합니다.
        """
        messages: list[BaseMessage] = _history_to_lc(history) + [HumanMessage(content=user_message)]
        tools_used: list[str] = []
        trace: list[TraceStep] = []
        metrics: list[ToolMetricData] = []
        escalated = False

        # Step 1: 도구 선택 (CS_INPUT_PROMPT, 1회 LLM 호출)
        try:
            response: AIMessage = await llm_with_tools.ainvoke(
                [SystemMessage(content=input_system)] + messages
            )
        except Exception as e:
            logger.error("[CS 에이전트] LLM 호출 오류 (도구 선택): %s", e)
            raise

        # 도구 호출 없음 → 인사말·확인 응답 등 직접 반환
        if not response.tool_calls:
            raw_answer = response.content or LLM_GENERATION_FAILED
            _log_trace(trace, user_message)
            return AgentResult(
                answer=_finalize_customer_answer(_parse_answer(raw_answer), trace),
                intent="other",
                escalated=False,
                tools_used=[],
                trace=trace,
                metrics=metrics,
            )

        # Step 2: 도구 병렬/순차 실행
        rag_indexed = [
            (i, tc) for i, tc in enumerate(response.tool_calls)
            if tc["name"] in _RAG_TOOL_NAMES
        ]
        other_indexed = [
            (i, tc) for i, tc in enumerate(response.tool_calls)
            if tc["name"] not in _RAG_TOOL_NAMES
        ]

        tool_messages: list[ToolMessage | None] = [None] * len(response.tool_calls)
        # 도구 실행 결과를 원래 tool_calls 인덱스 순서로 기록하기 위해 수집
        results_by_index: dict[int, tuple[dict, str, int]] = {}

        # RAG 병렬 실행
        if rag_indexed:
            rag_results = await asyncio.gather(*(
                _invoke_tool(tc, tool_map) for _, tc in rag_indexed
            ))
            for (i, tc), (result, latency_ms) in zip(rag_indexed, rag_results):
                tool_messages[i] = ToolMessage(content=result, tool_call_id=tc["id"])
                results_by_index[i] = (tc, result, latency_ms)

        # DB·액션 순차 실행
        for i, tc in other_indexed:
            guarded = _reject_cross_user_order_lookup(tc)
            if guarded is not None:
                result, latency_ms = guarded
            else:
                result, latency_ms = await _invoke_tool(tc, tool_map)

            tool_messages[i] = ToolMessage(content=result, tool_call_id=tc["id"])
            results_by_index[i] = (tc, result, latency_ms)

            if tc["name"] == "escalate_to_agent":
                escalated = True

        # tool_calls 원래 순서대로 기록 — intent가 첫 번째 도구 기준으로 결정되도록 보장
        for i in sorted(results_by_index):
            tc, result, latency_ms = results_by_index[i]
            _record(tc, result, latency_ms, 1, tools_used, trace, metrics)

        # ID 정합성 검증 — tool_call ID ↔ ToolMessage.tool_call_id 불일치 감지
        tc_ids_in_response = {tc["id"] for tc in response.tool_calls}
        tc_ids_in_messages = {m.tool_call_id for m in tool_messages if m is not None}
        missing_ids = tc_ids_in_response - tc_ids_in_messages
        if missing_ids:
            logger.error(
                "[CS 에이전트] tool_call ID 불일치 감지 — AIMessage에는 있으나 ToolMessage 없음: %s",
                missing_ids,
            )

        # 로그인 필요 단락 — LLM 재가공 없이 즉시 반환
        if (
            len(response.tool_calls) == 1
            and tool_messages[0] is not None
            and tool_messages[0].content == LOGIN_REQUIRED_RESPONSE
        ):
            _log_trace(trace, user_message)
            return AgentResult(
                answer=LOGIN_REQUIRED_RESPONSE,
                intent=TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other",
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        # __REFUSED__ 바이패스 — LLM 재호출 없이 사전 정의 응답 즉시 반환
        refused_tm = next(
            (m for m in tool_messages if m is not None and m.content.startswith("__REFUSED__")),
            None,
        )
        if refused_tm is not None:
            raw_reason = (
                refused_tm.content.split("사유:", 1)[1].strip()
                if "사유:" in refused_tm.content else ""
            )
            answer = refusal_response(raw_reason)
            logger.info("[CS 에이전트] 거절 바이패스 — reason=%s → '%s'", raw_reason, answer[:40])
            _log_trace(trace, user_message)
            return AgentResult(
                answer=answer,
                intent=TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other",
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        # 배송 조회 단일 도구는 LLM 재가공 없이 고객용 고정 포맷으로 반환한다.
        # 내부 상태값 노출, 불필요한 사과, 근거만 남는 정책 인용을 방지하기 위함이다.
        if (
            len(response.tool_calls) == 1
            and tools_used == ["get_order_status"]
            and tool_messages[0] is not None
        ):
            _log_trace(trace, user_message)
            return AgentResult(
                answer=_format_order_status_answer(tool_messages[0].content),
                intent="delivery",
                escalated=False,
                tools_used=tools_used, trace=trace, metrics=metrics,
            )

        # Step 3: 응답 생성 (CS_OUTPUT_PROMPT, 1회 LLM 호출)
        synth_messages = (
            [SystemMessage(content=output_system)]
            + messages
            + [response]
            + [m for m in tool_messages if m is not None]
        )
        try:
            synth: AIMessage = await output_llm.ainvoke(synth_messages)
            raw_answer = synth.content or LLM_GENERATION_FAILED
        except Exception as e:
            logger.error("[CS 에이전트] LLM 호출 오류 (응답 생성): %s", e)
            raise

        answer = _parse_answer(raw_answer)
        intent = TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other"

        has_empty_rag = any(
            _is_empty_result(s.result) and s.source == "rag" for s in trace
        )
        if has_empty_rag:
            trace.append(TraceStep(
                tool="_parametric_fallback",
                arguments={},
                result="RAG 결과 없음 — LLM 사전 지식으로 보완",
                iteration=1,
                source="parametric",
            ))

        _log_trace(trace, user_message)
        return AgentResult(
            answer=_finalize_customer_answer(answer, trace), intent=intent, escalated=escalated,
            tools_used=tools_used, trace=trace, metrics=metrics,
        )


# ── 헬퍼 ───────────────────────────────────────────────────────────────────────

async def _invoke_tool(tc: dict, tool_map: dict) -> tuple[str, int]:
    """도구 실행 + 소요 시간 측정."""
    t0 = time.monotonic()
    tool = tool_map.get(tc["name"])
    if tool is None:
        return f"[오류] 알 수 없는 도구: {tc['name']}", 0
    try:
        result = await tool.ainvoke(tc.get("args", {}))
        return str(result), int((time.monotonic() - t0) * 1000)
    except Exception as e:
        logger.error("도구 실행 오류 (%s): %s", tc["name"], e)
        return f"[오류] {tc['name']} 실행 중 문제가 발생했습니다.", int((time.monotonic() - t0) * 1000)


def _record(
    tc: dict,
    result: str,
    latency_ms: int,
    iteration: int,
    tools_used: list[str],
    trace: list[TraceStep],
    metrics: list[ToolMetricData],
) -> None:
    """도구 실행 결과를 tools_used / trace / metrics에 기록."""
    tools_used.append(tc["name"])
    success = not result.startswith("[오류]") and "오류가 발생했습니다" not in result
    trace.append(TraceStep(
        tool=tc["name"],
        arguments=tc.get("args", {}),
        result=result[:500],
        iteration=iteration,
        source=TOOL_SOURCE.get(tc["name"], "rag"),
    ))
    metrics.append(ToolMetricData(
        tool_name=tc["name"],
        intent=TOOL_TO_INTENT.get(tc["name"], "other"),
        success=success,
        latency_ms=latency_ms,
        empty_result=_is_empty_result(result),
        iteration=iteration,
    ))
    logger.info(
        "[trace] iter=%d tool=%s latency=%dms → %s",
        iteration, tc["name"], latency_ms, result[:120],
    )

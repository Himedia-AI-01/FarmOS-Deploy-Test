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
    REFUSED,
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

        result = await self._run_single_pass(
            llm_with_tools, output_llm, tool_map,
            user_message, history, input_with_ctx, output_with_ctx,
        )
        # search_faq가 인용한 문서 ID를 결과에 주입 (FaqCitation 저장용)
        result.cited_faq_ids = tool_ctx.cited_faq_ids
        return result

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
                answer=_parse_answer(raw_answer),
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
            # get_order_status에 user_id 인자 감지 → 타인 정보 조회 시도로 즉시 거절
            if tc["name"] == "get_order_status" and "user_id" in tc.get("args", {}):
                logger.warning("get_order_status에 user_id 인자 감지 — 타인 정보 조회 시도 거절")
                result, latency_ms = "__REFUSED__\n사유: other_user_info", 0
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
            answer = REFUSED
            logger.info("[CS 에이전트] 거절 바이패스 — reason=%s → '%s'", raw_reason, answer[:40])
            _log_trace(trace, user_message)
            return AgentResult(
                answer=answer,
                intent=TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other",
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
            answer=answer, intent=intent, escalated=escalated,
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

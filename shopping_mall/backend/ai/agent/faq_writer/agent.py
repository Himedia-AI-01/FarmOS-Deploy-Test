"""FAQ 작성 에이전트 — LangChain tool calling 기반 (동기).

기존 faq_draft_generator.py를 대체합니다.
모든 FAQ 작성 규칙을 FaqWriterAgent 하나로 집중 관리하므로,
향후 FAQ 등록 흐름이 추가되더라도 이 에이전트만 수정하면 됩니다.
"""
from __future__ import annotations

import json
import logging
import re as _re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ai.agent.faq_writer.prompts import FAQ_WRITER_SYSTEM_PROMPT
from ai.agent.faq_writer.tools import build_faq_writer_tools

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6

# intent → 한글 레이블 (faq.py _INTENT_LABEL과 동일)
_INTENT_LABEL: dict[str, str] = {
    "delivery": "배송·조회",
    "faq": "자주 묻는 질문",
    "stock": "상품·재고",
    "cancel": "취소·환불",
    "escalation": "처리 불가",
    "policy": "정책·약관",
    "refusal": "거절됨",
    "other": "기타",
    "greeting": "인사",
}


@dataclass
class FaqDraftResult:
    title: str
    content: str                          # 순수 답변 — (근거:...) 미포함
    suggested_category_slug: str | None
    model_used: str
    citation_doc: str | None = None
    citation_chapter: str | None = None
    citation_article: str | None = None
    citation_clause: str | None = None


class FaqWriterAgent:
    """FAQ 초안 자동 생성 에이전트.

    Primary LLM 실패 시 with_fallbacks()로 자동 전환합니다.
    generate() 메서드는 동기(sync) — FastAPI 동기 엔드포인트에서 직접 호출 가능합니다.
    """

    def __init__(self, primary, fallback, rag_service):
        self.primary = primary
        self.fallback = fallback
        self.rag = rag_service

    def generate(
        self,
        db: "Session",
        *,
        representative_question: str,
        top_intent: str,
        gap_type: str,
        count: int,
        escalated_count: int,
    ) -> FaqDraftResult:
        """FAQ 제목과 답변 초안을 생성합니다.

        Args:
            db: SQLAlchemy 동기 세션
            representative_question: Gap Analyzer가 선정한 대표 원문 질문
            top_intent: 주요 intent 코드 (delivery, stock, faq 등)
            gap_type: "missing" | "escalated"
            count: 해당 클러스터 총 발생 건수
            escalated_count: 에스컬레이션 건수

        Returns:
            FaqDraftResult

        Raises:
            ValueError: LLM 응답 파싱 실패 또는 최대 반복 초과
        """
        tools, tool_map = build_faq_writer_tools(self.rag, db)

        # Primary + Fallback 체인 구성
        primary_with_tools = self.primary.bind_tools(tools)
        if self.fallback:
            llm_with_tools = primary_with_tools.with_fallbacks(
                [self.fallback.bind_tools(tools)]
            )
        else:
            llm_with_tools = primary_with_tools

        # 사용자 메시지 구성
        intent_label = _INTENT_LABEL.get(top_intent, top_intent)
        gap_label = "처리 불가 (에스컬레이션)" if gap_type == "escalated" else "FAQ 미등록"

        user_message = (
            f"다음 질문 패턴에 대한 FAQ를 작성해주세요.\n\n"
            f"대표 질문: \"{representative_question}\"\n"
            f"의도 분류: {intent_label}\n"
            f"갭 유형: {gap_label}\n"
            f"총 발생 건수: {count}회 (에스컬레이션: {escalated_count}건)\n\n"
            f"search_faq_context, search_policy, get_faq_categories 도구를 활용하여 "
            f"정보를 수집한 후 FAQ JSON을 출력해주세요."
        )

        messages = [
            SystemMessage(content=FAQ_WRITER_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]

        for iteration in range(_MAX_ITERATIONS):
            response: AIMessage = llm_with_tools.invoke(messages)

            # 도구 호출 없음 → 최종 응답
            if not response.tool_calls:
                content = response.content
                if isinstance(content, str):
                    raw = content
                elif isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if item is None:
                            continue
                        elif isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            parts.append(item.get("text") or json.dumps(item, ensure_ascii=False))
                        else:
                            parts.append(str(item))
                    raw = "".join(parts)
                else:
                    raw = ""
                logger.info("[faq_writer] 에이전트 완료 (iter=%d)", iteration + 1)
                return _parse_result(raw, self.primary)

            # 도구 실행
            messages.append(response)
            for tc in response.tool_calls:
                tool = tool_map.get(tc["name"])
                if tool is None:
                    result = f"[오류] 알 수 없는 도구: {tc['name']}"
                else:
                    try:
                        result = str(tool.invoke(tc.get("args", {})))
                    except Exception as e:
                        logger.warning("[faq_writer] 도구 실행 오류 (%s): %s", tc["name"], e)
                        result = f"[오류] {tc['name']} 실행 실패: {e}"

                logger.info("[faq_writer] iter=%d tool=%s → %s", iteration + 1, tc["name"], result[:120])
                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        raise ValueError(f"FAQ 초안 생성 최대 반복 횟수({_MAX_ITERATIONS})를 초과했습니다.")


# ── 결과 파싱 ─────────────────────────────────────────────────────────────────

def _parse_result(raw: str, llm) -> FaqDraftResult:
    """LLM 최종 응답(JSON)을 FaqDraftResult로 변환합니다."""
    # 마크다운 코드 블록 제거
    text = raw
    if "```" in text:
        match = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("[faq_writer] JSON 파싱 실패. raw=%r, error=%s", raw[:200], e)
        raise ValueError(f"LLM 응답을 JSON으로 파싱할 수 없습니다: {e}") from e

    title = str(data.get("title", "")).strip()
    content = str(data.get("content", "")).strip()
    if not title or not content:
        raise ValueError("LLM 응답에 title 또는 content가 비어 있습니다.")

    # 방어: LLM이 실수로 포함한 prefix/wrapper 제거
    # title: "[카테고리] Q: 질문" → "질문"
    title = _re.sub(r"^\[.*?\]\s*", "", title).strip()
    title = _re.sub(r"^Q:\s*", "", title).strip()

    # content: 카테고리 prefix 먼저 제거, 그 다음 Q:/A: wrapper 처리
    content = _re.sub(r"^\[.*?\]\s*", "", content).strip()
    if "\nA:" in content:
        content = content.split("\nA:", 1)[1].strip()
    content = _re.sub(r"^A:\s*", "", content).strip()

    # citation 파싱 (선택적)
    citation = data.get("citation")
    if not isinstance(citation, dict):
        citation = None

    model_name = getattr(llm, "model_name", "unknown")
    return FaqDraftResult(
        title=title,
        content=content,
        suggested_category_slug=data.get("suggested_category_slug") or None,
        model_used=model_name,
        citation_doc=citation.get("doc") or None if citation else None,
        citation_chapter=citation.get("chapter") or None if citation else None,
        citation_article=citation.get("article") or None if citation else None,
        citation_clause=citation.get("clause") or None if citation else None,
    )

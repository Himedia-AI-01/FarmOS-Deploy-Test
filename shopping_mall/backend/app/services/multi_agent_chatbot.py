"""멀티 에이전트 챗봇 서비스 — AgentChatbotService와 동일한 인터페이스."""
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ai.agent.supervisor import SupervisorExecutor
from ai.agent.responses import SERVICE_TEMPORARY_ERROR

if TYPE_CHECKING:
    from ai.agent import ToolMetricData

logger = logging.getLogger(__name__)

HISTORY_WINDOW_SIZE = 6

# 서비스 장애로 생성된 오류 메시지 패턴 — 해당 턴은 히스토리에서 제외
_SERVICE_ERROR_PATTERNS: tuple[str, ...] = (
    "현재 서비스에 일시적인 문제가 발생했습니다",
    "요청을 처리하는 데 시간이 걸리고 있습니다",
)


def _is_service_error(content: str) -> bool:
    return any(p in content for p in _SERVICE_ERROR_PATTERNS)


class MultiAgentChatbotService:
    """SupervisorExecutor를 래핑하여 AgentChatbotService와 동일한 answer() 인터페이스를 구현."""

    def __init__(self, supervisor: SupervisorExecutor, input_prompt: str, output_prompt: str):
        self.supervisor = supervisor
        self.input_prompt = input_prompt
        self.output_prompt = output_prompt

    async def answer(
        self,
        db: Session,
        question: str,
        user_id: int | None = None,
        history: list | None = None,
        session_id: int | None = None,
    ) -> dict:
        # history → LLM 메시지 형식 변환 (최근 N턴)
        messages = self._build_history(history)

        # 요청 컨텍스트 생성 (날짜/시각, 로그인 상태)
        from ai.agent import RequestContext
        context = RequestContext.build(user_id)

        # Supervisor 에이전트 실행
        try:
            result = await self.supervisor.run(
                db=db,
                user_message=question,
                user_id=user_id,
                session_id=session_id,
                history=messages,
                input_system=self.input_prompt,
                output_system=self.output_prompt,
                context=context,
            )
        except Exception as e:
            logger.error("에이전트 실행 오류: %s", e, exc_info=True)
            from ai.agent.executor import AgentResult
            result = AgentResult(
                answer=SERVICE_TEMPORARY_ERROR,
                intent="other",
                escalated=True,
            )

        # ChatLog 저장 + 세션 메타데이터 갱신 (단일 트랜잭션)
        from app.models.chat_log import ChatLog
        log = ChatLog(
            user_id=user_id,
            session_id=session_id,
            intent=result.intent,
            question=question,
            answer=result.answer,
            escalated=result.escalated,
        )
        db.add(log)

        if session_id:
            from app.models.chat_session import ChatSession
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session:
                if not session.title:
                    session.title = question[:50]
                session.updated_at = datetime.now(timezone.utc)

        db.commit()

        # FAQ 인용 기록 저장 (cited_faq_ids가 있을 때만, 별도 트랜잭션)
        cited_faq_ids = getattr(result, "cited_faq_ids", [])
        if cited_faq_ids:
            self._save_faq_citations(db, cited_faq_ids, log.id)

        # 메트릭 저장 (ChatLog commit 이후 별도 트랜잭션)
        if result.metrics:
            self._save_metrics(db, result.metrics, log.id, session_id)

        return {
            "answer": result.answer,
            "intent": result.intent,
            "escalated": result.escalated,
            "trace": result.trace,
            "chat_log_id": log.id,
            "cited_faq_ids": cited_faq_ids,
        }

    def _save_faq_citations(
        self,
        db: Session,
        faq_ids: list[int],
        chat_log_id: int,
    ) -> None:
        """search_faq가 인용한 FAQ 문서를 FaqCitation 테이블에 저장합니다."""
        try:
            from app.models.faq_citation import FaqCitation

            db.add_all([
                FaqCitation(chat_log_id=chat_log_id, faq_doc_id=faq_id)
                for faq_id in faq_ids
            ])
            db.commit()
        except Exception as e:
            logger.warning("FAQ 인용 저장 실패: %s", e)
            db.rollback()

    def _save_metrics(
        self,
        db: Session,
        metrics: "list[ToolMetricData]",
        chat_log_id: int | None,
        session_id: int | None,
    ) -> None:
        """도구 메트릭을 DB에 저장. 실패해도 응답에 영향 없음."""
        try:
            from app.models.tool_metric import ToolMetric

            db.add_all([
                ToolMetric(
                    chat_log_id=chat_log_id,
                    session_id=session_id,
                    tool_name=m.tool_name,
                    intent=m.intent,
                    success=m.success,
                    latency_ms=m.latency_ms,
                    empty_result=m.empty_result,
                    iteration=m.iteration,
                )
                for m in metrics
            ])
            db.commit()
        except Exception as e:
            logger.warning("도구 메트릭 저장 실패: %s", e)
            db.rollback()

    # 프론트엔드 role → LLM role 매핑 ("bot" → "assistant")
    _ROLE_MAP: dict[str, str] = {
        "user": "user",
        "assistant": "assistant",
        "bot": "assistant",  # 프론트엔드가 bot으로 전송
    }

    def _build_history(self, history: list | None) -> list[dict]:
        """기존 history 형식 → LLM 메시지 형식 변환.

        escalated=True인 bot 응답(서비스 오류 포함)은 해당 user+bot 쌍 전체를 제거합니다.
        LLM이 미해결 질문으로 오인하여 현재 메시지 대신 이전 질문에 재답변하는 문제를 방지합니다.
        """
        if not history:
            return []

        messages: list[dict] = []
        # window의 2배를 읽어 필터 후 HISTORY_WINDOW_SIZE로 잘라냄
        for item in history[-(HISTORY_WINDOW_SIZE * 2):]:
            raw_role = item.get("role", "user")
            role = self._ROLE_MAP.get(raw_role)
            if not role:
                logger.debug("알 수 없는 history role 무시: %s", raw_role)
                continue
            content = item.get("content") or item.get("text", "")
            if not content:
                continue

            if role == "assistant":
                escalated = item.get("escalated", False)
                if escalated or _is_service_error(content):
                    # 이 응답과 대응하는 직전 user 메시지를 함께 제거
                    if messages and messages[-1]["role"] == "user":
                        messages.pop()
                    continue

            messages.append({"role": role, "content": content})

        return messages[-HISTORY_WINDOW_SIZE:]

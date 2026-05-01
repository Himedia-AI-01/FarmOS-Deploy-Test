from __future__ import annotations
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


def to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:])


class ChatHistoryItem(BaseModel):
    role: str  # "user" | "bot"
    text: str
    escalated: bool | None = None  # bot 응답의 escalated 상태 (null 허용 — user 메시지는 null로 전송됨)


class ChatQuestion(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    question: str
    session_id: Optional[int] = None
    history: List[ChatHistoryItem] = []


class TraceStepSchema(BaseModel):
    tool: str
    arguments: dict
    result: str
    iteration: int
    source: str = "rag"  # "rag" | "db" | "action" | "parametric"


class ChatAnswer(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    answer: str
    intent: str
    escalated: bool = False
    trace: Optional[List[TraceStepSchema]] = None  # debug=true 시에만 포함
    # FAQ 피드백 수집에 필요한 필드
    chat_log_id: Optional[int] = None            # 피드백 제출 시 참조 ID
    cited_faq_ids: List[int] = Field(default_factory=list)  # 인용된 FAQ 문서 DB ID 목록


class ChatLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    user_id: Optional[int] = None
    intent: str
    question: str
    answer: str
    escalated: bool
    rating: Optional[int] = None
    created_at: Optional[datetime] = None


class ChatRating(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    rating: int = Field(..., ge=1, le=5)


# ── 도구 분석 스키마 ─────────────────────────────────────────────────────────

class ToolAnalyticsItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    tool_name: str
    call_count: int = Field(..., ge=0)
    success_rate: float = Field(..., ge=0.0, le=1.0, description="0.0 ~ 1.0")
    avg_latency_ms: float = Field(..., ge=0)
    empty_result_rate: float = Field(..., ge=0.0, le=1.0, description="0.0 ~ 1.0")


class ToolAnalyticsResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    period: str
    tools: List[ToolAnalyticsItem]
    total_calls: int = Field(..., ge=0)

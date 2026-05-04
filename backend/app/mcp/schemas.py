"""MCP tool 전용 입력/출력 스키마.

Design Ref: §3.1 (입력/출력 스키마 결정), §3.2 (PDF 반환 결정)

- 기존 schemas.review_analysis.* (SearchRequest 등) 는 그대로 재사용해 코드 중복을 피한다.
- 본 모듈은 MCP 에서만 의미 있는 신규 응답 스키마(PdfReport, AnalysisDetail) 만 정의한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.review_analysis import (
    AnomalyAlert,
    KeywordItem,
    SentimentSummary,
    SummaryData,
    TrendData,
)


class PdfReport(BaseModel):
    """generate_pdf_report tool 응답.

    PDF 바이너리를 base64 로 인코딩하여 JSON 응답에 inline 으로 담는다.
    클라이언트는 content_base64 를 디코드해 파일로 저장하면 된다.
    Design §3.2 에서 base64 inline 채택 (5MB 이하 가정).
    """

    filename: str
    content_base64: str
    content_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int


class AnalysisDetail(BaseModel):
    """get_latest_analysis / get_analysis_by_id tool 응답.

    DB 의 ReviewAnalysis 레코드를 직렬화한 형태.
    summary 는 DB 에 JSON 문자열로 저장되므로 dict 로 deserialize 후 SummaryData 로 매핑.
    """

    model_config = ConfigDict(from_attributes=True)

    analysis_id: int
    analysis_type: str
    target_scope: str
    review_count: int
    sentiment_summary: SentimentSummary
    keywords: list[KeywordItem] = []
    summary: SummaryData = SummaryData()
    trends: list[TrendData] = []
    anomalies: list[AnomalyAlert] = []
    processing_time_ms: int = 0
    llm_provider: str = ""
    llm_model: str = ""
    created_at: str | None = None  # ISO 8601


__all__ = ["PdfReport", "AnalysisDetail"]

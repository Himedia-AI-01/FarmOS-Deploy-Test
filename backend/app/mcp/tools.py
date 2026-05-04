"""MCP tool 정의 및 등록.

Design Ref: §3 (Tool Specification), §11.2 (Implementation Order)

본 module-1 (foundation) 단계에서는 spike 검증용 `ping` tool 만 등록한다.
T1~T10 (embed/search/analyze/.../settings) 는 module-2 이후에 추가한다.

Decision Trace (Plan + Design):
- D-1 1:1 low-level tool granularity
- D-2 JWT 미들웨어 재사용 → mcp/auth.py:get_current_user_from_ctx
- D-3 progress = ctx.report_progress (T4 한정, module-3)
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

import base64
import json

from sqlalchemy import desc, select

from app.core import review_singletons as _singletons
from app.core.database import async_session
from app.core.review_helpers import get_seller_product_ids, stratified_sample
from app.mcp.auth import get_current_user_from_ctx
from app.mcp.schemas import AnalysisDetail, PdfReport
from app.models.review_analysis import ReviewAnalysis
from app.schemas.review_analysis import (
    AnalysisSettings,
    AnalyzeResponse,
    AnomalyAlert,
    EmbedResponse,
    KeywordItem,
    SearchFilters,
    SearchResponse,
    SearchResult,
    SentimentSummary,
    SummaryData,
    TrendData,
    TrendsResponse,
)

logger = logging.getLogger("app.mcp.tools")


def register_all_tools(mcp: FastMCP) -> None:
    """FastMCP 인스턴스에 모든 review tool 을 등록한다.

    module-1: ping (spike).
    module-2: T1 embed_reviews, T2 search_reviews.
    module-3: T3~T7 analysis tools (예정).
    module-4: T8~T10 report/settings (예정).
    """
    _register_ping(mcp)
    _register_embed_and_search(mcp)        # module-2
    _register_analysis_tools(mcp)          # module-3
    _register_report_and_settings(mcp)     # module-4


# ---------------------------------------------------------------------------
# spike: ping — module-1 검증 전용 (mount + auth + lifespan 통합 확인)
# ---------------------------------------------------------------------------

def _register_ping(mcp: FastMCP) -> None:
    @mcp.tool
    async def ping(ctx: Context) -> dict[str, Any]:
        """MCP 마운트/인증/라이프사이클 통합 검증용 echo tool.

        - 인증된 사용자만 호출 가능 (Authorization Bearer 또는 Cookie).
        - 향후 실제 tool 들이 사용할 패턴(세션 + auth) 의 최소 예시.

        Returns:
            {ok, user_id, user_name, transport, request_id}
        """
        async with async_session() as db:
            user = await get_current_user_from_ctx(db)

        try:
            transport = getattr(ctx, "transport", None) or "unknown"
        except Exception:  # noqa: BLE001
            transport = "unknown"
        try:
            request_id = getattr(ctx, "request_id", None) or ""
        except Exception:  # noqa: BLE001
            request_id = ""

        logger.info(
            "mcp.ping ok user_id=%s transport=%s request_id=%s",
            user.id, transport, request_id,
        )

        return {
            "ok": True,
            "user_id": user.id,
            "user_name": user.name,
            "transport": transport,
            "request_id": request_id,
        }


# ---------------------------------------------------------------------------
# module-2: T1 embed_reviews, T2 search_reviews
# ---------------------------------------------------------------------------
# Design Ref: §3.0, §3.3 — 1:1 low-level mapping with FastAPI router
# Plan SC: SC-01 (tool 노출), SC-02 (FastAPI 응답 일치), SC-04 (멀티테넌트 컨텍스트)


def _register_embed_and_search(mcp: FastMCP) -> None:
    """T1 embed_reviews + T2 search_reviews 등록."""

    @mcp.tool
    async def embed_reviews(ctx: Context) -> EmbedResponse:
        """리뷰 데이터를 ChromaDB 에 임베딩 동기화한다 (T1).

        shop_reviews 테이블의 리뷰를 ChromaDB collection 에 동기화한다.
        FastAPI POST /api/v1/reviews/embed 와 동일 동작.

        Returns:
            EmbedResponse: embedded_count(이번 추가 수), total_count(전체 임베딩 수), source="db".
        """
        async with async_session() as db:
            await get_current_user_from_ctx(db)
            added = await _singletons.rag.sync_from_db(db)
        total = _singletons.rag.get_count()
        logger.info(
            "mcp.embed_reviews ok added=%d total=%d", added, total,
        )
        return EmbedResponse(embedded_count=added, total_count=total, source="db")

    @mcp.tool
    async def search_reviews(
        query: str,
        ctx: Context,
        top_k: int = 10,
        filters: SearchFilters | None = None,
    ) -> SearchResponse:
        """자연어 질의로 유사 리뷰를 의미 검색한다 (T2 — RAG).

        FastAPI POST /api/v1/reviews/search 와 동일 동작.
        멀티테넌트: 호출자가 seller_id 를 가진 경우 해당 판매자 상품 리뷰만 검색한다.
        ChromaDB 컬렉션이 비어 있으면 자동으로 DB 동기화 수행 (라우터와 동일).

        Args:
            query: 자연어 검색 질의.
            top_k: 반환 개수 (1~50, 기본 10).
            filters: 옵션 — platform/rating_min/rating_max/date_from/date_to 메타데이터 필터.
        """
        # top_k 가드 (FastAPI Field(ge=1, le=50) 와 동등)
        if not 1 <= top_k <= 50:
            raise ToolError("top_k must be between 1 and 50")

        async with async_session() as db:
            user = await get_current_user_from_ctx(db)

            # empty-new-collection window 완화 — 라우터와 동일 패턴
            if _singletons.rag.get_count() == 0:
                await _singletons.rag.sync_from_db(db)

            filter_dict: dict[str, Any] | None = None
            if filters:
                filter_dict = filters.model_dump(exclude_none=True)

            seller_id = getattr(user, "seller_id", None)  # User 모델에 미구현 — None 반환
            product_ids = await get_seller_product_ids(db, seller_id=seller_id)
            if product_ids is not None:
                filter_dict = (filter_dict or {}) | {"product_id": {"$in": product_ids}}

            results = _singletons.rag.search(
                query=query,
                top_k=top_k,
                filters=filter_dict,
            )

        logger.info(
            "mcp.search_reviews ok query=%r top_k=%d hits=%d",
            query, top_k, len(results),
        )
        return SearchResponse(
            results=[SearchResult(**r) for r in results],
            total=len(results),
        )


# ---------------------------------------------------------------------------
# module-3: T3~T7 analysis tools
# ---------------------------------------------------------------------------
# Design Ref: §3.0 (tool 카탈로그), §5 (progress notification 매핑)
# Plan SC: SC-01 (10 tools 노출), SC-02 (FastAPI 응답 일치), SC-05 (progress notification)


def _serialize_summary(record: ReviewAnalysis) -> dict[str, Any]:
    """ReviewAnalysis.summary (Text/JSONB) 를 dict 로 안전 디코딩한다."""
    raw = record.summary
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _record_to_detail(record: ReviewAnalysis) -> AnalysisDetail:
    """ReviewAnalysis ORM 행 → AnalysisDetail Pydantic 매핑."""
    summary_dict = _serialize_summary(record)
    sentiment_dict = record.sentiment_summary or {}
    return AnalysisDetail(
        analysis_id=record.id,
        analysis_type=record.analysis_type or "",
        target_scope=record.target_scope or "all",
        review_count=record.review_count or 0,
        sentiment_summary=SentimentSummary(**sentiment_dict) if isinstance(sentiment_dict, dict) else SentimentSummary(),
        keywords=[KeywordItem(**kw) for kw in (record.keywords or []) if isinstance(kw, dict)],
        summary=SummaryData(**summary_dict) if isinstance(summary_dict, dict) else SummaryData(),
        trends=[TrendData(**t) for t in (record.trends or []) if isinstance(t, dict)],
        anomalies=[AnomalyAlert(**a) for a in (record.anomalies or []) if isinstance(a, dict)],
        processing_time_ms=record.processing_time_ms or 0,
        llm_provider=record.llm_provider or "",
        llm_model=record.llm_model or "",
        created_at=record.created_at.isoformat() if record.created_at else None,
    )


async def _run_analysis_and_save(
    db,
    user,
    sample_size: int,
    batch_size: int,
    scope: str,
    progress_cb=None,
) -> tuple[ReviewAnalysis, dict[str, Any]]:
    """T3/T4 공통 분석 실행 + DB 저장 로직.

    progress_cb 가 제공되면 batch_with_progress 변형을 사용해 진행률을 전달한다.
    """
    if _singletons.rag.get_count() == 0:
        if progress_cb:
            await progress_cb(0, "DB 리뷰 임베딩 중...")
        await _singletons.rag.sync_from_db(db)

    seller_id = getattr(user, "seller_id", None)
    product_ids = await get_seller_product_ids(db, seller_id=seller_id)
    if product_ids is not None:
        reviews = _singletons.rag.get_reviews_by_products(product_ids)
    else:
        reviews = _singletons.rag.get_all_reviews()
    if not reviews:
        raise ToolError("분석할 리뷰가 없습니다. 먼저 embed_reviews 를 실행하세요.")

    total_count = len(reviews)
    sampled = stratified_sample(reviews, sample_size)
    if progress_cb:
        await progress_cb(5, f"전체 {total_count}건 중 {len(sampled)}건 샘플링 → 분석 시작")

    analysis_reviews = [
        {
            "id": r["id"],
            "text": r["text"],
            "rating": r["metadata"].get("rating", 0),
            "platform": r["metadata"].get("platform", ""),
            "date": r["metadata"].get("date", ""),
        }
        for r in sampled
    ]

    if progress_cb is not None:
        # T4 — async generator 변형, 매 update 마다 progress_cb 호출
        final_result: dict[str, Any] = {}
        async for update in _singletons.analyzer.analyze_batch_with_progress(
            analysis_reviews, batch_size=batch_size,
        ):
            if "result" in update:
                final_result = update["result"]
            if "progress" in update:
                await progress_cb(update["progress"], update.get("message"))
        result = final_result or {}
    else:
        # T3 — 단일 await
        result = await _singletons.analyzer.analyze_batch(
            analysis_reviews, batch_size=batch_size,
        )

    # 트렌드/이상 탐지
    sentiments_with_date = [
        {**s, "date": next((r["date"] for r in analysis_reviews if str(r["id"]) == str(s.get("id"))), "")}
        for s in result.get("sentiments", [])
    ]
    trends = _singletons.trend_detector.calculate_weekly_trends(sentiments_with_date)
    anomalies = _singletons.trend_detector.detect_anomalies(trends)

    summary_data = result.get("summary", {})
    record = ReviewAnalysis(
        analysis_type="manual",
        target_scope=scope,
        review_count=total_count,
        sentiment_summary=result.get("sentiment_summary"),
        # dict 가드 — 읽는 쪽(_record_to_summary, T7 get_trends)이 isinstance(dict) 필터를 사용하므로
        # DB 저장 시점부터 동일 정규화 적용해 메모리·DB 정합성 보장.
        keywords=[kw for kw in result.get("keywords", []) if isinstance(kw, dict)],
        summary=json.dumps(summary_data, ensure_ascii=False) if summary_data else None,
        trends=[t for t in trends if isinstance(t, dict)],
        anomalies=[a for a in anomalies if isinstance(a, dict)],
        llm_provider=result.get("llm_provider", ""),
        llm_model=result.get("llm_model", ""),
        processing_time_ms=result.get("processing_time_ms", 0),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    if progress_cb:
        await progress_cb(100, f"분석 완료! ({len(sampled)}/{total_count}건 샘플 분석, DB 저장됨)")

    return record, result


def _register_analysis_tools(mcp: FastMCP) -> None:
    """T3 analyze_reviews + T4 analyze_reviews_with_progress + T5 get_latest_analysis
    + T6 get_analysis_by_id + T7 get_trends 등록."""

    @mcp.tool
    async def analyze_reviews(
        ctx: Context,
        scope: str = "all",
        sample_size: int = 200,
        batch_size: int = 50,
    ) -> AnalyzeResponse:
        """리뷰 분석을 실행한다 (T3 — 동기 / FastAPI POST /reviews/analyze 와 동일).

        층화 샘플링 → LLM 배치 분석(감성+키워드+요약) → 트렌드/이상 탐지 → DB 저장.

        Args:
            scope: "all" 또는 "product:{id}" 또는 "platform:{name}".
            sample_size: 분석할 리뷰 샘플 수 (50~10000, 기본 200).
            batch_size: LLM 1회 호출당 리뷰 수 (5~100, 기본 50).
        """
        if not 50 <= sample_size <= 10000:
            raise ToolError("sample_size must be between 50 and 10000")
        if not 5 <= batch_size <= 100:
            raise ToolError("batch_size must be between 5 and 100")

        async with async_session() as db:
            user = await get_current_user_from_ctx(db)
            record, result = await _run_analysis_and_save(
                db, user, sample_size=sample_size, batch_size=batch_size,
                scope=scope, progress_cb=None,
            )

        summary_data = result.get("summary", {})
        return AnalyzeResponse(
            analysis_id=record.id,
            status="completed",
            review_count=record.review_count,
            sentiment_summary=SentimentSummary(**result.get("sentiment_summary", {})),
            keywords=[KeywordItem(**kw) for kw in result.get("keywords", [])],
            summary=SummaryData(**summary_data) if isinstance(summary_data, dict) else SummaryData(),
            anomalies=[AnomalyAlert(**a) for a in (record.anomalies or [])],
            processing_time_ms=result.get("processing_time_ms", 0),
            llm_provider=result.get("llm_provider", ""),
            llm_model=result.get("llm_model", ""),
        )

    @mcp.tool
    async def analyze_reviews_with_progress(
        ctx: Context,
        scope: str = "all",
        sample_size: int = 200,
        batch_size: int = 50,
    ) -> AnalyzeResponse:
        """리뷰 분석을 실행하며 진행률을 progress notification 으로 전달한다 (T4).

        FastAPI GET /reviews/analyze/stream (SSE) 의 MCP 대응.
        클라이언트는 ctx.report_progress 알림을 수신하며 최종 결과는 일반 return 값으로 받는다.
        """
        if not 50 <= sample_size <= 10000:
            raise ToolError("sample_size must be between 50 and 10000")
        if not 5 <= batch_size <= 100:
            raise ToolError("batch_size must be between 5 and 100")

        async def progress_cb(p: int, msg: str | None = None) -> None:
            await ctx.report_progress(progress=p, total=100)
            if msg:
                try:
                    await ctx.info(msg)
                except Exception as e:  # noqa: BLE001 — info 실패가 분석을 막지 않음
                    # debug 레벨 — 운영 노이즈 0, LOG_LEVEL=DEBUG 시 traceback 가시화.
                    logger.debug("ctx.info failed: %s", e, exc_info=True)

        async with async_session() as db:
            user = await get_current_user_from_ctx(db)
            record, result = await _run_analysis_and_save(
                db, user, sample_size=sample_size, batch_size=batch_size,
                scope=scope, progress_cb=progress_cb,
            )

        summary_data = result.get("summary", {})
        return AnalyzeResponse(
            analysis_id=record.id,
            status="completed",
            review_count=record.review_count,
            sentiment_summary=SentimentSummary(**result.get("sentiment_summary", {})),
            keywords=[KeywordItem(**kw) for kw in result.get("keywords", [])],
            summary=SummaryData(**summary_data) if isinstance(summary_data, dict) else SummaryData(),
            anomalies=[AnomalyAlert(**a) for a in (record.anomalies or [])],
            processing_time_ms=result.get("processing_time_ms", 0),
            llm_provider=result.get("llm_provider", ""),
            llm_model=result.get("llm_model", ""),
        )

    @mcp.tool
    async def get_latest_analysis(ctx: Context) -> AnalysisDetail:
        """최신 분석 결과를 조회한다 (T5 — FastAPI GET /reviews/analysis 동등)."""
        async with async_session() as db:
            await get_current_user_from_ctx(db)
            stmt = select(ReviewAnalysis).order_by(desc(ReviewAnalysis.created_at)).limit(1)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()

        if record is None:
            raise ToolError("분석 결과가 없습니다. 먼저 analyze_reviews 를 실행하세요.")
        return _record_to_detail(record)

    @mcp.tool
    async def get_analysis_by_id(analysis_id: int, ctx: Context) -> AnalysisDetail:
        """특정 analysis_id 로 분석 결과를 조회한다 (T6 — 신규)."""
        if analysis_id <= 0:
            raise ToolError("analysis_id must be positive")
        async with async_session() as db:
            await get_current_user_from_ctx(db)
            stmt = select(ReviewAnalysis).where(ReviewAnalysis.id == analysis_id)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()

        if record is None:
            raise ToolError(f"analysis_id={analysis_id} 인 분석 결과를 찾을 수 없습니다.")
        return _record_to_detail(record)

    @mcp.tool
    async def get_trends(
        ctx: Context,
        period: str = "weekly",
    ) -> TrendsResponse:
        """최신 분석의 트렌드/이상 탐지 데이터를 반환한다 (T7 — FastAPI GET /reviews/trends 동등).

        Args:
            period: "weekly" 또는 "monthly" (현재는 weekly 만 의미 있음, 라우터와 동일).
        """
        if period not in ("weekly", "monthly"):
            raise ToolError("period must be 'weekly' or 'monthly'")
        async with async_session() as db:
            await get_current_user_from_ctx(db)
            stmt = select(ReviewAnalysis).order_by(desc(ReviewAnalysis.created_at)).limit(1)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()

        if record is None or not record.trends:
            return TrendsResponse(trends=[], anomalies=[])

        return TrendsResponse(
            trends=[TrendData(**t) for t in (record.trends or []) if isinstance(t, dict)],
            anomalies=[AnomalyAlert(**a) for a in (record.anomalies or []) if isinstance(a, dict)],
        )


# ---------------------------------------------------------------------------
# module-4: T8 generate_pdf_report, T9 get_analysis_settings, T10 update_analysis_settings
# ---------------------------------------------------------------------------
# Design Ref: §3.0 (tool 카탈로그), §3.2 (PDF base64 inline)
# Plan SC: SC-01 (10 tools), SC-02 (FastAPI 응답 일치 — PDF/settings 동등)


def _record_to_pdf_data(record: ReviewAnalysis) -> dict[str, Any]:
    """ReviewAnalysis ORM → review_report.generate_pdf 가 기대하는 dict 형태.

    FastAPI 라우터 download_report 의 analysis_data 매핑과 1:1 동일.
    """
    summary_dict = _serialize_summary(record)
    return {
        "sentiment_summary": record.sentiment_summary or {},
        "keywords": record.keywords or [],
        "summary": summary_dict,
        "anomalies": record.anomalies or [],
        "processing_time_ms": record.processing_time_ms or 0,
        "llm_provider": record.llm_provider or "",
        "llm_model": record.llm_model or "",
    }


def _register_report_and_settings(mcp: FastMCP) -> None:
    """T8 generate_pdf_report + T9 get_analysis_settings + T10 update_analysis_settings 등록."""

    @mcp.tool
    async def generate_pdf_report(
        ctx: Context,
        analysis_id: int | None = None,
    ) -> PdfReport:
        """분석 결과를 PDF 리포트로 생성한다 (T8 — base64 inline).

        FastAPI GET /reviews/report/pdf?analysis_id=... 와 동일 동작.
        analysis_id 가 None 이면 최신 분석을 사용한다.
        PDF 바이너리는 base64 로 인코딩해 JSON 응답에 inline 전달 (5MB 이하 가정).

        Args:
            analysis_id: 특정 분석 ID. None 이면 최신.
        """
        async with async_session() as db:
            await get_current_user_from_ctx(db)

            if analysis_id is not None:
                if analysis_id <= 0:
                    raise ToolError("analysis_id must be positive")
                stmt = select(ReviewAnalysis).where(ReviewAnalysis.id == analysis_id)
            else:
                stmt = select(ReviewAnalysis).order_by(desc(ReviewAnalysis.created_at)).limit(1)

            result = await db.execute(stmt)
            record = result.scalar_one_or_none()

            if record is None:
                raise ToolError("분석 결과가 없습니다. 먼저 analyze_reviews 를 실행하세요.")

            analysis_data = _record_to_pdf_data(record)

        # generate_pdf 는 동기 메서드 — BytesIO 반환
        pdf_io = _singletons.report_generator.generate_pdf(analysis_data)
        pdf_bytes = pdf_io.getvalue()
        size_bytes = len(pdf_bytes)

        # 5MB 가드 — Design §12 리스크 (base64 페이로드 과대)
        if size_bytes > 5 * 1024 * 1024:
            raise ToolError(
                f"PDF too large for inline base64 transport: {size_bytes} bytes (limit 5MB). "
                f"Consider using GET /api/v1/reviews/report/pdf?analysis_id={record.id} instead."
            )

        logger.info(
            "mcp.generate_pdf_report ok analysis_id=%d size=%d",
            record.id, size_bytes,
        )
        return PdfReport(
            filename="review-analysis-report.pdf",
            content_base64=base64.b64encode(pdf_bytes).decode("ascii"),
            size_bytes=size_bytes,
        )

    @mcp.tool
    async def get_analysis_settings(ctx: Context) -> AnalysisSettings:
        """자동 분석 설정을 조회한다 (T9 — FastAPI GET /reviews/settings 동등)."""
        async with async_session() as db:
            await get_current_user_from_ctx(db)
        return _singletons.settings_state

    @mcp.tool
    async def update_analysis_settings(
        ctx: Context,
        auto_batch_enabled: bool | None = None,
        batch_trigger_count: int | None = None,
        batch_schedule: str | None = None,
        default_batch_size: int | None = None,
    ) -> AnalysisSettings:
        """자동 분석 설정을 변경한다 (T10 — FastAPI PUT /reviews/settings 동등).

        모든 인자는 옵션 — None 이면 해당 필드 미변경 (PATCH 의미).
        싱글턴 모듈 속성으로 재할당해 라우터/MCP 양쪽에 즉시 반영.

        Args:
            auto_batch_enabled: 자동 배치 분석 활성 여부.
            batch_trigger_count: 새 리뷰 누적 임계값 (1~100).
            batch_schedule: cron-like 스케줄 문자열 (또는 None).
            default_batch_size: 기본 배치 크기 (5~50).
        """
        # 검증 — FastAPI Pydantic Field 와 동등 가드
        if batch_trigger_count is not None and not 1 <= batch_trigger_count <= 100:
            raise ToolError("batch_trigger_count must be between 1 and 100")
        if default_batch_size is not None and not 5 <= default_batch_size <= 50:
            raise ToolError("default_batch_size must be between 5 and 50")

        async with async_session() as db:
            await get_current_user_from_ctx(db)

        # exclude_none semantics — None 인자는 미변경
        updates: dict[str, Any] = {}
        if auto_batch_enabled is not None:
            updates["auto_batch_enabled"] = auto_batch_enabled
        if batch_trigger_count is not None:
            updates["batch_trigger_count"] = batch_trigger_count
        if batch_schedule is not None:
            updates["batch_schedule"] = batch_schedule
        if default_batch_size is not None:
            updates["default_batch_size"] = default_batch_size

        current = _singletons.settings_state.model_dump()
        current.update(updates)
        _singletons.settings_state = AnalysisSettings(**current)

        logger.info("mcp.update_analysis_settings ok updates=%s", list(updates.keys()))
        return _singletons.settings_state


__all__ = ["register_all_tools"]

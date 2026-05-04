# IoT Review MCP — 리뷰 자동화 분석 MCP화 Design

| 항목 | 값 |
|------|-----|
| Feature | iot-review-mcp |
| Author | clover0309 |
| Created | 2026-05-01 |
| Branch | feat/IoT_Review_MCP |
| Phase | Design |
| Architecture | **Option C — Pragmatic (FastAPI mount, streamable-http)** |
| Plan | [iot-review-mcp.plan.md](../../01-plan/features/iot-review-mcp.plan.md) |
| Status | Draft |

---

## Context Anchor

> Copied from Plan. Propagated to Do/Analysis/Report.

| Key | Value |
|-----|-------|
| **WHY** | 분석/검색/리포트 함수가 HTTP에만 노출되어 있어, 향후 오케스트레이션 에이전트가 도구로 호출할 표준 인터페이스가 없다. 매 에이전트마다 HTTP 어댑터를 새로 짜야 한다. |
| **WHO** | (1) 다음 사이클 오케스트레이션 에이전트(Primary), (2) Claude Code/Desktop 직접 호출 시나리오, (3) 기존 FastAPI 사용 프론트엔드(불변). |
| **RISK** | (1) FastMCP+FastAPI mount lifespan 통합 패턴 검증 필요(spike). (2) JWT 헤더를 MCP 컨텍스트에서 가져오는 pattern 차이. (3) DB/ChromaDB 라이프사이클이 lifespan에 의존. (4) 기존 FastAPI 회귀 가능성. |
| **SUCCESS** | SC-01~SC-06 (Plan §Executive Summary 참조). |
| **SCOPE** | IN: FastMCP 서버, 10개 low-level tool, JWT 어댑터, progress notification, 헬퍼 추출. OUT: 오케스트레이션 에이전트, 신규 분석 기능, 자동 배치, 프론트엔드 변경. |

---

## 1. Overview

### 1.1 Design Goals

1. **Zero behavioral regression**: 기존 FastAPI 7개 엔드포인트 + 프론트엔드 무변경
2. **Single source of truth**: `core/review_*.py` 시그니처 불변, 코드 중복 0
3. **Standard MCP contract**: 10개 low-level tool, 표준 입력/출력 스키마, progress notification, JSON-RPC 표준 오류
4. **Reusable JWT trust**: 기존 `core/deps.get_current_user` 의 JWT 검증 로직 재사용
5. **Lifespan-correct**: FastMCP session lifespan + 기존 FastAPI startup 이벤트가 모두 정상 동작

### 1.2 Design Principles

- **얇은 어댑터**: tool 함수 = 입력 검증 + 인증 컨텍스트 추출 + 코어 호출 + 응답 매핑. 비즈니스 로직 0.
- **공유 싱글턴**: `_rag`, `_analyzer`, `_trend_detector`, `_report_generator` 인스턴스를 FastAPI 라우터와 MCP tool이 동일 인스턴스로 사용 (같은 프로세스 + 같은 모듈 import).
- **Pydantic 미러링**: tool 입력 스키마는 기존 `schemas/review_analysis.py` 의 `*Request`/`*Response` 를 직접 import 또는 alias.
- **Progress only when long-running**: `analyze_reviews_with_progress` 한정. 다른 tool은 동기 응답.

---

## 2. Architecture

### 2.0 Selected: Option C — FastAPI mount

| Criteria | Option C 채택 사유 |
|----------|-------------------|
| Approach | 기존 FastAPI 옆에 FastMCP를 ASGI sub-app으로 mount |
| New Files | 4 (`backend/app/mcp/{__init__,server,tools,schemas,auth}.py`) — schemas는 별도 파일로 둘 수도, tools에 inline도 가능 |
| Modified Files | 2 (`backend/app/main.py`, `pyproject.toml`) + 1 헬퍼 추출 (`api/review_analysis.py`) |
| Complexity | Medium |
| Maintainability | High (싱글 소스, 표준 패턴) |
| Effort | Medium |
| Risk | Low (단일 spike로 mount API 검증 후 진행) |
| Plan D-2 (JWT 재사용) | ✅ 완전 충족 |

**Rationale**: Plan D-2(JWT 미들웨어 재사용)와 코드 중복 0 목표를 100% 만족시키는 유일한 옵션. FastMCP 3.x 의 `http_app(path=...)` + `combine_lifespans` 패턴이 표준화되어 있어 단일 spike로 검증 가능.

### 2.1 Component Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│  MCP Client                                                        │
│  (Claude Code / Desktop / Future Orchestration Agent)              │
└──────────────┬─────────────────────────────────────────────────────┘
               │ Streamable HTTP (JSON-RPC 2.0)
               │ POST :8000/mcp
               │ Authorization: Bearer <JWT>
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  FastAPI Application (backend/app/main.py, port 8000)              │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │  Existing Routers (UNCHANGED)                           │       │
│  │  /api/v1/reviews/* (review_analysis.py)                 │       │
│  │  /api/v1/sensors/*, /api/v1/iot/*, ...                  │       │
│  └─────────────────────────────────────────────────────────┘       │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │  app.mount("/mcp", mcp_app)                              │       │
│  │  ┌───────────────────────────────────────────────────┐   │       │
│  │  │  FastMCP Sub-App (review_mcp_server.py)           │   │       │
│  │  │                                                   │   │       │
│  │  │  Tools (10개, 1:1 low-level):                      │   │       │
│  │  │    embed_reviews         analyze_reviews_*         │   │       │
│  │  │    search_reviews        get_latest_analysis       │   │       │
│  │  │    get_analysis_by_id    get_trends                │   │       │
│  │  │    generate_pdf_report   get_analysis_settings     │   │       │
│  │  │    update_analysis_settings                        │   │       │
│  │  │                                                   │   │       │
│  │  │  Auth Adapter (mcp/auth.py)                       │   │       │
│  │  │    request_ctx.get() → Authorization header       │   │       │
│  │  │    → 기존 jwt 검증 로직 재사용 → User → seller_id  │   │       │
│  │  │                                                   │   │       │
│  │  │  Progress Adapter                                 │   │       │
│  │  │    SSE update → ctx.report_progress(p, total)    │   │       │
│  │  └───────────────────────────────────────────────────┘   │       │
│  └─────────────────────────────────────────────────────────┘       │
│                                                                    │
│  Lifespan: combine_lifespans(existing_lifespan, mcp_app.lifespan)  │
└──────────────┬─────────────────────────────────────────────────────┘
               │
               ▼ (싱글턴 인스턴스 공유)
┌────────────────────────────────────────────────────────────────────┐
│  Core Services (UNCHANGED)                                         │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────┐            │
│  │ ReviewRAG    │ │ ReviewAnalyzer │ │ TrendDetector  │            │
│  │ (review_rag) │ │ (review_       │ │ (trend_        │            │
│  │              │ │  analyzer)     │ │  detector)     │            │
│  └──────┬───────┘ └────────┬───────┘ └────────┬───────┘            │
│         │                  │                  │                    │
│         ▼                  ▼                  ▼                    │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────┐            │
│  │ ChromaDB     │ │ LiteLLM/LLM    │ │ PostgreSQL     │            │
│  │ (collection: │ │ Voyage-3.5     │ │ review_        │            │
│  │  reviews_    │ │ embeddings     │ │ analyses       │            │
│  │  voyage_v35) │ │                │ │                │            │
│  └──────────────┘ └────────────────┘ └────────────────┘            │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow — `analyze_reviews_with_progress` 예시

```
1. Client: tools/call analyze_reviews_with_progress(scope="all", sample_size=200)
2. FastMCP routes to tool function
3. auth.py:get_current_user_from_ctx(): request_ctx.get() → Bearer JWT → User
4. tool: _rag.get_count() == 0 → _rag.sync_from_db(db)  (필요 시)
5. tool: _stratified_sample(reviews, sample_size)
6. tool: async for update in _analyzer.analyze_batch_with_progress(...):
        ctx.report_progress(update["progress"], 100)  ← 클라이언트에게 즉시 전달
7. tool: _trend_detector.calculate_weekly_trends(...) + detect_anomalies(...)
8. tool: db.add(ReviewAnalysis(...)) + db.commit()
9. tool returns final AnalyzeResponseDict
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `mcp.server` | `fastmcp.FastMCP`, `app.mcp.tools`, `app.mcp.auth` | MCP 서버 빌드 |
| `mcp.tools` | `core.review_rag/analyzer/report`, `core.trend_detector`, `core.database`, `models.review_analysis`, `schemas.review_analysis` | tool 함수들 |
| `mcp.auth` | `core.deps.get_current_user` 의 검증 함수, `models.user.User`, `fastmcp.server.context.request_ctx` | JWT → User → seller_id |
| `mcp.schemas` | `schemas.review_analysis.*` (Pydantic 재사용) | 입력/출력 스키마 |
| `app.main` | `mcp.server.build_review_mcp`, `fastmcp.utilities.lifespan.combine_lifespans` | mount + lifespan |

**External (신규)**: `fastmcp >= 3.2.0` (Python 3.10+).

---

## 3. Tool Specification (10개 tools)

### 3.0 Tool 카탈로그

| # | Tool | FastAPI 매핑 | 입력 | 출력 | 인증 | Progress |
|---|------|-------------|------|------|:----:|:--------:|
| T1 | `embed_reviews` | `POST /reviews/embed` | (없음) | `EmbedResponse` (added/total/source) | ✅ | ❌ |
| T2 | `search_reviews` | `POST /reviews/search` | `SearchRequest` (query/top_k/filters) | `SearchResponse` (results[], total) | ✅ | ❌ |
| T3 | `analyze_reviews` | `POST /reviews/analyze` | `AnalyzeRequest` (scope/sample_size/batch_size) | `AnalyzeResponse` (analysis_id/sentiment_summary/keywords/summary/anomalies/...) | ✅ | ❌ |
| T4 | `analyze_reviews_with_progress` | `GET /reviews/analyze/stream` (SSE) | `AnalyzeRequest` 동일 | `AnalyzeResponse` 동일 + 중간 progress notifications | ✅ | ✅ |
| T5 | `get_latest_analysis` | `GET /reviews/analysis` | (없음) | `AnalysisDetail` (DB 레코드 직렬화) | ✅ | ❌ |
| T6 | `get_analysis_by_id` | (신규, 기존엔 `?analysis_id=` 파라미터로 PDF만 가능) | `analysis_id: int` | `AnalysisDetail` 동일 | ✅ | ❌ |
| T7 | `get_trends` | `GET /reviews/trends` | `period: "weekly" \| "monthly"` (기본 weekly) | `TrendsResponse` (trends[], anomalies[]) | ✅ | ❌ |
| T8 | `generate_pdf_report` | `GET /reviews/report/pdf` | `analysis_id: int \| null` | `PdfReport` (filename, content_base64, content_type) | ✅ | ❌ |
| T9 | `get_analysis_settings` | `GET /reviews/settings` | (없음) | `AnalysisSettings` | ✅ | ❌ |
| T10 | `update_analysis_settings` | `PUT /reviews/settings` | `AnalysisSettingsUpdate` | `AnalysisSettings` | ✅ | ❌ |

### 3.1 입력/출력 스키마 결정

- **재사용**: 기존 `schemas/review_analysis.py` 의 Pydantic 모델을 그대로 import → FastMCP 가 자동으로 JSON Schema 생성.
- **신규 출력 스키마 (PDF)**:
  ```python
  class PdfReport(BaseModel):
      filename: str            # "review-analysis-report.pdf"
      content_base64: str      # base64 encoded PDF bytes
      content_type: Literal["application/pdf"] = "application/pdf"
      size_bytes: int
  ```
- **신규 출력 스키마 (AnalysisDetail, T5/T6 공용)**:
  ```python
  class AnalysisDetail(BaseModel):
      analysis_id: int
      analysis_type: str
      target_scope: str
      review_count: int
      sentiment_summary: SentimentSummary
      keywords: list[KeywordItem]
      summary: SummaryData
      trends: list[TrendData]
      anomalies: list[AnomalyAlert]
      processing_time_ms: int
      llm_provider: str
      llm_model: str
      created_at: str        # ISO 8601
  ```

### 3.2 PDF 반환 결정

| 후보 | 채택? | 근거 |
|------|:----:|------|
| base64 inline | ✅ | 가장 단순. MCP 표준 JSON 응답 안에 자체 완결. 클라이언트가 디코드 후 저장. 5MB 이하 PDF 한정. |
| FastMCP `Image`/binary content | ❌ | `Image` 는 이미지 전용. 임의 바이너리 표준 미정. |
| URL 반환 (`/api/v1/reviews/report/pdf?analysis_id=X`) | ❌ | 같은 프로세스에서 다시 HTTP round-trip. 인증 헤더 재사용 부담. |

**채택**: `base64 inline` (`PdfReport` 스키마, 위 §3.1).

### 3.3 Tool별 핵심 동작

T1 `embed_reviews`:
```
async def embed_reviews(ctx: Context) -> EmbedResponse:
    user = await get_current_user_from_ctx(ctx)        # JWT 검증
    async with get_db_session() as db:
        added = await _rag.sync_from_db(db)
    return EmbedResponse(embedded_count=added, total_count=_rag.get_count(), source="db")
```

T2 `search_reviews`:
```
async def search_reviews(req: SearchRequest, ctx: Context) -> SearchResponse:
    user = await get_current_user_from_ctx(ctx)
    async with get_db_session() as db:
        if _rag.get_count() == 0:
            await _rag.sync_from_db(db)
        filters = req.filters.model_dump(exclude_none=True) if req.filters else None
        product_ids = await _get_seller_product_ids(db, seller_id=getattr(user, "seller_id", None))
        if product_ids is not None:
            filters = (filters or {}) | {"product_id": {"$in": product_ids}}
        results = _rag.search(query=req.query, top_k=req.top_k, filters=filters)
    return SearchResponse(results=[SearchResult(**r) for r in results], total=len(results))
```

T4 `analyze_reviews_with_progress`:
```
async def analyze_reviews_with_progress(req: AnalyzeRequest, ctx: Context) -> AnalyzeResponse:
    user = await get_current_user_from_ctx(ctx)
    async with get_db_session() as db:
        # ... (analyze_reviews_stream과 동일 흐름)
        async for update in _analyzer.analyze_batch_with_progress(reviews, batch_size=req.batch_size):
            if "progress" in update:
                await ctx.report_progress(progress=update["progress"], total=100)
                if "message" in update:
                    await ctx.info(update["message"])
            if "result" in update:
                final_result = update["result"]
        # ... DB 저장 + AnalyzeResponse 생성
    return response
```

T8 `generate_pdf_report`:
```
async def generate_pdf_report(analysis_id: int | None = None, *, ctx: Context) -> PdfReport:
    user = await get_current_user_from_ctx(ctx)
    async with get_db_session() as db:
        record = await _fetch_analysis(db, analysis_id)  # 최신 또는 특정
        if not record:
            raise McpError(code=ErrorCode.NOT_FOUND, message="분석 결과가 없습니다.")
        pdf_io = _report_generator.generate_pdf(_to_analysis_data(record))
    pdf_bytes = pdf_io.getvalue()
    return PdfReport(
        filename="review-analysis-report.pdf",
        content_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        size_bytes=len(pdf_bytes),
    )
```

---

## 4. Authentication Adapter

### 4.1 Pattern

FastMCP 3.x 의 `request_ctx.get()` 으로 현재 요청의 raw `Authorization` 헤더를 얻고, 기존 JWT 검증 로직(`core/security.py` 또는 `core/deps.py` 내부 함수)을 재사용한다. **`Depends(get_current_user)` 자체는 FastAPI 전용**이라 직접 호출 불가 — 그 안의 검증 헬퍼만 추출/재사용.

```python
# backend/app/mcp/auth.py
from fastmcp.server.context import request_ctx
from fastmcp.exceptions import ToolError
from app.core.security import decode_access_token   # 기존 JWT 디코더 (없으면 추출)
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

async def get_current_user_from_ctx(db: AsyncSession) -> User:
    rc = request_ctx.get()
    auth = rc.request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ToolError("Authorization header missing")
    token = auth[7:]
    try:
        payload = decode_access_token(token)
    except Exception as e:
        raise ToolError(f"Invalid token: {e}") from e

    user_id = payload.get("sub")
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise ToolError("User not found")
    return user
```

### 4.2 seller_id 추출

기존 라우터의 `_get_seller_product_ids(db, seller_id=None)` 시그니처를 그대로 사용. `User` 모델에 `seller_id` 또는 동등 필드가 있으면 전달, 없으면 None(전체 접근) — **현 코드와 동일 동작**.

### 4.3 인증 실패 처리

| 상황 | 응답 |
|------|------|
| Authorization 헤더 없음 | `ToolError("Authorization header missing")` → MCP 표준 오류 |
| JWT 만료/위변조 | `ToolError("Invalid token: ...")` |
| User 미존재 | `ToolError("User not found")` |
| seller가 분석 권한 없음 (향후) | `ToolError("Permission denied")` |

> 기존 FastAPI는 `HTTPException(401)` 으로 응답. MCP 는 `ToolError` 가 클라이언트에 JSON-RPC error로 전달됨. 동일 의미, 다른 표현.

---

## 5. Progress Notification Mapping

### 5.1 매핑 테이블

| 기존 SSE update | MCP Action |
|----------------|-----------|
| `{"progress": N, "message": "..."}` | `ctx.report_progress(progress=N, total=100)` + `ctx.info(message)` |
| `{"progress": 100, "result": {...}}` | tool return 값으로 매핑 (마지막 progress=100) |
| `{"progress": 100, "error": "..."}` | `raise ToolError(error)` |

### 5.2 Tool에서 사용 — `analyze_reviews_with_progress` 본문 패턴

```python
# 시작
await ctx.report_progress(progress=0, total=100)
await ctx.info(f"전체 {total}건 중 {len(sampled)}건 샘플링")

# 배치 분석 진행 중
async for update in _analyzer.analyze_batch_with_progress(reviews, batch_size=req.batch_size):
    if "progress" in update:
        await ctx.report_progress(progress=update["progress"], total=100)
        if msg := update.get("message"):
            await ctx.info(msg)
    if "result" in update:
        final = update["result"]

# DB 저장 직전
await ctx.report_progress(progress=95, total=100)

# 완료
await ctx.report_progress(progress=100, total=100)
return AnalyzeResponse(analysis_id=record.id, ...)
```

---

## 6. Lifespan & Mount Integration

### 6.1 main.py 수정 패턴

```python
# backend/app/main.py (예시 — 실제 코드는 Do phase에서 기존 구조에 맞춰 적용)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans

from app.mcp.server import build_review_mcp

# 기존 lifespan (DB init 등)
@asynccontextmanager
async def existing_lifespan(app: FastAPI):
    # 기존 startup 코드
    yield
    # 기존 shutdown 코드

# FastMCP 빌드
mcp = build_review_mcp()
mcp_app = mcp.http_app(path="/")    # /mcp/ 로 mount하므로 path는 "/"

# 두 lifespan 통합
app = FastAPI(
    title="FarmOS Backend",
    lifespan=combine_lifespans(existing_lifespan, mcp_app.lifespan),
)

# 기존 라우터 등록 (UNCHANGED)
app.include_router(review_analysis.router, prefix="/api/v1")
# ... 다른 라우터들

# MCP mount
app.mount("/mcp", mcp_app)
```

### 6.2 mcp/server.py — FastMCP 구성

```python
# backend/app/mcp/server.py
from fastmcp import FastMCP
from app.mcp.tools import register_all_tools

def build_review_mcp() -> FastMCP:
    mcp = FastMCP(
        name="farmos-review-mcp",
        instructions=(
            "FarmOS 농산물 리뷰 자동화 분석 MCP 서버. "
            "ChromaDB 기반 의미검색, LLM 감성분석/키워드/요약, 트렌드/이상 탐지, "
            "PDF 리포트 생성을 제공합니다."
        ),
    )
    register_all_tools(mcp)
    return mcp
```

### 6.3 DB 세션 전략

- FastAPI 라우터는 `Depends(get_db)` → 요청 단위 AsyncSession.
- MCP tool 은 FastMCP가 같은 ASGI request 안에서 실행되지만 `Depends` 미적용. → tool 안에서 명시적 세션 컨텍스트 매니저 사용:

```python
# backend/app/core/database.py 에 이미 있을 것 (없으면 Do에서 추가)
@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

> 헬퍼가 이미 있다면 재사용. 없으면 `core.database` 의 `async_session_maker` 로 한 줄 짜리 wrapper 추가.

---

## 7. Security Considerations

- [x] **인증**: 모든 tool은 JWT Bearer 검증 (10/10 tools)
- [x] **권한 (멀티테넌트)**: `seller_id` 컨텍스트 그대로 적용 (현 라우터 패턴 유지)
- [x] **입력 검증**: Pydantic 모델 사용 → FastMCP 가 자동 JSON Schema 검증
- [x] **민감정보 로깅 금지**: JWT/Authorization 헤더는 로그에 출력하지 않음
- [x] **Rate limiting**: 본 사이클 OUT (별도 사이클 또는 reverse proxy 단)
- [x] **CSRF**: 비해당 (Bearer token 모델, 쿠키 미사용)
- [x] **에러 메시지 노출**: `ToolError` 메시지에 내부 스택트레이스 포함 금지

---

## 8. Test Plan (v2.3.0)

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1: API Tests | MCP tool 호출 + FastAPI 회귀 | curl + MCP Inspector + httpx | Do |
| L2: 동등성 | 같은 입력으로 MCP tool ↔ FastAPI 응답 비교 | pytest + httpx (snapshot) | Do |
| L3: E2E | Claude Code MCP Client 시나리오 (등록 → list_tools → call) | 수동 + 스크립트 | Check |

### 8.2 L1: 시나리오

| # | Tool / Endpoint | 검증 | 기대 |
|---|-----------------|------|------|
| 1 | `tools/list` (MCP) | 등록된 tool 수 | 10 |
| 2 | `embed_reviews` 호출 | 200 / `EmbedResponse.embedded_count >= 0` | OK |
| 3 | `search_reviews(query="단맛")` | results 배열 길이 ≤ top_k | OK |
| 4 | `analyze_reviews(sample_size=10)` | DB row 추가 + `analysis_id` 반환 | OK |
| 5 | `analyze_reviews_with_progress` | progress notification ≥ 3회 | OK |
| 6 | `generate_pdf_report` | content_base64 디코드 시 `%PDF-` 매직넘버로 시작 | OK |
| 7 | (회귀) `POST /api/v1/reviews/search` | 응답이 MCP search_reviews와 동일 (snapshot) | OK |
| 8 | (회귀) `GET /api/v1/reviews/analysis` | 변경 전과 동일 응답 | OK |
| 9 | 인증 헤더 없이 tool 호출 | `ToolError` (Authorization missing) | OK |
| 10 | 만료 토큰으로 호출 | `ToolError("Invalid token: ...")` | OK |

### 8.3 L2: 동등성 매트릭스

| Tool | 비교 대상 FastAPI | 동등성 항목 |
|------|-------------------|------------|
| search_reviews | POST /reviews/search | results 배열 (id 순서 + score) |
| analyze_reviews | POST /reviews/analyze | sentiment_summary, keywords (top 20), summary |
| get_latest_analysis | GET /reviews/analysis | 모든 필드 |
| get_trends | GET /reviews/trends | trends + anomalies |
| generate_pdf_report | GET /reviews/report/pdf | PDF 바이트 동등 (혹은 size 동등) |
| get/update_settings | GET/PUT /reviews/settings | 모든 필드 |

### 8.4 Seed Data

- `shop_reviews` 테이블에 ≥ 50건 (기존 seed 또는 기존 production 데이터)
- ChromaDB collection `reviews_voyage_v35` 임베딩 ≥ 50건 (`embed_reviews` 호출 후)
- `users` 테이블에 1명 이상 (JWT 발급 가능)

---

## 9. Clean Architecture (이 feature 한정)

| Layer | 본 feature 컴포넌트 | Location |
|-------|------------------|----------|
| Presentation (MCP boundary) | tool 함수, 입력/출력 스키마 | `backend/app/mcp/` |
| Application | (얇은 어댑터) — tool 함수 안에서 직접 코어 호출 | `backend/app/mcp/tools.py` |
| Domain | 분석/검색 로직, 엔티티 | `backend/app/core/review_*.py`, `models/review_analysis.py` |
| Infrastructure | DB 세션, ChromaDB 클라이언트, LLM 클라이언트 | `backend/app/core/database.py`, `vectordb.py`, `llm_client_*.py` |

**Dependency rule**: `mcp/*` (Presentation/Application 혼재)는 `core/*` (Domain/Infrastructure)에만 의존. 역방향 import 금지.

---

## 10. Coding Convention

### 10.1 본 feature 적용

| Item | Convention |
|------|-----------|
| Tool 함수명 | snake_case 동사_명사 (`embed_reviews`, `search_reviews`, ...) |
| 파일 | snake_case (`server.py`, `tools.py`, `auth.py`, `schemas.py`) |
| 패키지 | `backend/app/mcp/` (snake_case) |
| 비동기 | 모든 tool 은 `async def` (DB 호출 포함) |
| 로깅 | `logging.getLogger(__name__)` — `app.mcp.tools`, `app.mcp.auth` namespace |
| Docstring | 한국어 1-line summary + (옵션) 상세 — 기존 `core/review_*.py` 스타일 동일 |
| Type hints | 100% (FastMCP가 schema 자동생성) |
| 오류 | `ToolError` (FastMCP 표준), 내부 예외는 `logger.exception` 후 wrap |

---

## 11. Implementation Guide

### 11.1 File Structure

```
backend/app/
├── api/
│   └── review_analysis.py            (수정 — 헬퍼 추출만, 동작 무변경)
├── core/
│   ├── review_rag.py                 (불변)
│   ├── review_analyzer.py            (불변)
│   ├── review_report.py              (불변)
│   ├── trend_detector.py             (불변)
│   ├── review_helpers.py             (신규 — _stratified_sample, _get_seller_product_ids 추출)
│   └── database.py                   (필요 시 get_db_session ctx manager 추가)
├── mcp/                               (신규 패키지)
│   ├── __init__.py                   (export build_review_mcp)
│   ├── server.py                     (FastMCP 인스턴스 + register_all_tools)
│   ├── tools.py                      (10개 tool 함수)
│   ├── schemas.py                    (PdfReport, AnalysisDetail 신규 스키마)
│   └── auth.py                       (get_current_user_from_ctx)
├── main.py                           (수정 — combine_lifespans + app.mount("/mcp", ...))
└── core/security.py                  (있으면 재사용, 없으면 deps.py 내부 디코더 추출)

pyproject.toml                         (수정 — fastmcp >= 3.2 추가)
```

### 11.2 Implementation Order

1. **Spike**: FastMCP 빈 tool 1개로 mount + lifespan PoC. 검증: `tools/list` 응답.
2. **모듈 추출**: `_stratified_sample`, `_get_seller_product_ids` → `core/review_helpers.py` 로 이동. 라우터에서 import. **회귀 검증**.
3. **인증 어댑터**: `mcp/auth.py:get_current_user_from_ctx` + 기존 JWT 디코더 분리.
4. **공유 스키마**: `mcp/schemas.py` (PdfReport, AnalysisDetail).
5. **Tool 구현**: 카테고리별 — search/embed → analysis 3종 → trends → pdf → settings 2종.
6. **Server 빌드 + mount**: `mcp/server.py` + `main.py` 수정.
7. **L1 + 회귀 smoke**.
8. **L2 동등성 테스트**.

### 11.3 Session Guide (Module Map)

| Module | Scope Key | 설명 | Files | Estimated Turns |
|--------|-----------|------|-------|:---------------:|
| Foundation | `module-1` | 의존성 추가 + 헬퍼 추출 + auth + schemas + spike (빈 tool 1개로 mount 검증) | `pyproject.toml`, `core/review_helpers.py`, `mcp/{__init__,server,auth,schemas}.py`, `main.py` 부분 수정 | 12-18 |
| Search & Embed | `module-2` | T1, T2 — `embed_reviews`, `search_reviews` | `mcp/tools.py` (T1, T2) | 6-10 |
| Analysis | `module-3` | T3-T7 — analyze (sync/progress), get_latest, get_by_id, get_trends | `mcp/tools.py` (T3-T7) | 12-18 |
| Report & Settings | `module-4` | T8-T10 — generate_pdf_report, settings 2개 | `mcp/tools.py` (T8-T10), `mcp/schemas.py` (PdfReport) | 6-10 |
| Integration | `module-5` | mount 통합 + L1 smoke + L2 동등성 + AC-1~10 | `main.py`, 회귀 검증 + 동등성 비교 | 10-15 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 | Plan + Design | 전체 (이 세션) | ~30 |
| Session 2 | Do | `--scope module-1` | 12-18 |
| Session 3 | Do | `--scope module-2,module-3` | 18-28 |
| Session 4 | Do | `--scope module-4,module-5` | 16-25 |
| Session 5 | Check + Report | 전체 | 25-35 |

---

## 12. Risks & Mitigations (Design 단계 추가/심화)

| 리스크 | 영향 | 대응 |
|--------|------|------|
| `combine_lifespans` API 가 `fastmcp.utilities.lifespan` 에 없는 버전 | mount 패턴 수정 필요 | `fastmcp >= 3.2.0` 핀. spike 단계에서 import 검증. 대체 — 직접 nested asynccontextmanager 작성. |
| `request_ctx.get()` 내부 구조가 streamable-http 에서 raw `Authorization` 헤더 미노출 | JWT 추출 실패 | spike 시 헤더 dict 출력 로그. 대체 — FastMCP `BearerAuthProvider` 사용 (별도 구현 필요). |
| `Depends(get_current_user)` 가 FastAPI 컨텍스트 의존 — MCP tool 에서 직접 호출 불가 | 인증 코드 중복 | JWT 검증 헬퍼 함수만 분리해서 (core/security.py) 양쪽이 import. 라우터는 `Depends`로 감싸서 동일 함수 사용. |
| 기존 라우터의 싱글턴 인스턴스(`_rag` 등)를 MCP에서도 import 시 import cycle | 기동 실패 | 인스턴스를 `core/review_singletons.py` 신규 모듈로 분리하거나, MCP에서 별 인스턴스 + 같은 ChromaDB collection 공유. |
| PDF 5MB 초과 시 base64 응답 페이로드 과대 | 클라이언트 OOM/타임아웃 | 본 사이클은 가정 5MB 이하. 초과 시 size 검증 후 ToolError. 향후 resource URI 방식 검토. |
| `_stratified_sample` 추출 시 import 경로 변경으로 라우터 회귀 | 기존 동작 깨짐 | 이동 + import만 1커밋 분리. 동작 변화 0 검증(`pytest` 또는 수동 smoke) 후 다음 모듈. |
| 같은 프로세스에서 MCP가 무거운 LLM 호출 → FastAPI 응답 latency 영향 | UX | 본 사이클은 경고만. 향후 별 워커 분리 또는 옵션 B 마이그레이션 가능. |

---

## 13. Open Questions for Do

1. `core/security.py` 또는 `core/deps.py` 의 JWT 디코더 헬퍼가 이미 분리되어 있는지 확인. 없으면 추출 필요.
2. `User` 모델에 `seller_id` 또는 동등 필드가 있는지 확인. 없으면 본 사이클 가정대로 `None`(전체 접근).
3. 기존 `_rag`, `_analyzer` 등 싱글턴을 MCP/FastAPI 양쪽에서 어떻게 공유할지 — 옵션 (a) 별도 `singletons.py` 모듈, (b) `tools.py` 안에서 `from app.api.review_analysis import _rag` 직접 import. → spike 후 결정.
4. `pyproject.toml` 의존성 매니저(poetry vs uv vs pip-tools) 확인 후 `fastmcp` 추가.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-01 | Initial draft (Option C 채택, FastMCP 3.x mount 패턴 확정, 10 tools spec, JWT 어댑터, progress 매핑) | clover0309 |

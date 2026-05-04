# 리뷰 자동화 분석 구현 정리

## 한 줄 요약

> 농산물 쇼핑몰의 리뷰를 **벡터 검색(RAG) + LLM 분석(감성/키워드/요약) + 트렌드 탐지 + PDF 리포트**로 자동화하고,
> 동일 도메인 로직을 **FastAPI REST + MCP tool 듀얼 노출**하여 웹 클라이언트와 Claude Desktop 모두에서 사용 가능.

---

## 아키텍처 다이어그램

```
[shop_reviews 테이블 (Postgres)]
         │
         │ sync_from_db()
         ▼
┌──────────────────────────────────────────────────────────────┐
│ ReviewRAG (review_rag.py)                                    │
│   ChromaDB collection "reviews_voyage_v35" (1024-dim)        │
│   임베딩: LiteLLM 프록시 → Voyage v3.5                        │
│   검색: 하이브리드 (벡터 + 키워드 부스팅 0.3 + 텍스트 dedup)  │
└──────────────────────────────────────────────────────────────┘
         │
         │ get_all_reviews() / get_reviews_by_products()
         ▼
┌──────────────────────────────────────────────────────────────┐
│ stratified_sample() (review_helpers.py)                      │
│   리뷰 모집단 → 샘플 N건 (층화 샘플링)                        │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ ReviewAnalyzer (review_analyzer.py)                          │
│   배치 분할 → asyncio.gather 병렬 LLM 호출                   │
│   1콜 = 감성 + 키워드 + 요약 동시 (비용 1/3)                  │
│   JSON 3단 폴백 파싱 + 재시도 (httpx 5xx 지수 백오프)        │
│   LLM: BaseLLMClient 팩토리 (Ollama/LiteLLM/RemoteOllama)   │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ TrendDetector (trend_detector.py)                            │
│   주간 트렌드 (positive_ratio 변화)                           │
│   이상 탐지 (sudden_drop, spike)                             │
└──────────────────────────────────────────────────────────────┘
         │
         ▼ DB 저장
┌──────────────────────────────────────────────────────────────┐
│ review_analysis 테이블                                        │
│   sentiment_summary, keywords, summary, trends, anomalies    │
│   (summary는 JSONB)                                          │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ ReviewReportGenerator (review_report.py)                     │
│   fpdf2 PDF — OS별 한글 폰트 자동 탐색 (Malgun/AppleGothic   │
│   /Nanum/Noto)                                               │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Dual Exposure (review_singletons.py 공유 인스턴스)            │
│   ┌──────────────────────┐    ┌──────────────────────┐      │
│   │ FastAPI REST         │    │ MCP (FastMCP)        │      │
│   │ /api/v1/reviews/*    │    │ Tool T1~T10          │      │
│   │ 웹 프론트 호출        │    │ Claude Desktop 호출  │      │
│   └──────────────────────┘    └──────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

---

## 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/app/core/review_rag.py` | ChromaDB + LiteLLM 임베딩 + 하이브리드 검색 |
| `backend/app/core/review_analyzer.py` | LLM 분석 (1콜=감성+키워드+요약, 배치 병렬) |
| `backend/app/core/trend_detector.py` | 주간 트렌드/이상 탐지 |
| `backend/app/core/review_report.py` | fpdf2 PDF 생성 (OS별 한글 폰트) |
| `backend/app/core/llm_client_base.py` | LLM 추상화 (Ollama/LiteLLM/RemoteOllama 팩토리) |
| `backend/app/core/review_singletons.py` | FastAPI/MCP 공유 인스턴스 |
| `backend/app/core/review_helpers.py` | 층화 샘플링 + seller_id → product_ids 변환 |
| `backend/app/api/review_analysis.py` | FastAPI 라우터 |
| `backend/app/mcp/tools.py` | MCP tool 10개 등록 |

---

## 설계 결정 8가지

### 1. RAG의 "R" — 하이브리드 검색
**Why**: 순수 벡터 검색은 한국어 짧은 리뷰("별로...")가 상위에 잘 올라옴. 키워드 일치 보장이 필요.

**How**: 벡터 유사도 + 키워드 포함 시 +0.3 부스팅 + 점수순 재정렬 + 텍스트 dedup
```python
# review_rag.py:277
KEYWORD_BOOST = 0.3
for r in vector_results:
    if any(word in r["text"] for word in query.split()):
        r["similarity"] += KEYWORD_BOOST
```

### 2. RAG의 "G" — 1콜 다중 분석
**Why**: 리뷰 50건을 하나씩 분석 = LLM 50콜 = 비용 50배.

**How**: 1콜 프롬프트로 **감성+키워드+요약 동시 반환** + `batch_size=50`으로 배치 + `asyncio.gather`로 모든 배치 병렬 호출.
```python
# review_analyzer.py:144
tasks = [self._analyze_single_batch(batch) for batch in batches]
results = await asyncio.gather(*tasks, return_exceptions=True)
```
→ **호출 수 1/N × 분석 종류 1/3 = 비용 1/(3N)**

### 3. JSON 3단 폴백 파싱
**Why**: LLM이 `\`\`\`json ... \`\`\`` 코드블록으로 감싸거나 앞뒤 설명 텍스트 붙이면 `json.loads()` 실패.

**How**:
```python
# review_analyzer.py:336
1) json.loads(text)                    # 직접 파싱
2) "```json" 블록 추출 후 json.loads
3) text[first_brace:last_brace+1]      # 첫 { ~ 마지막 } 추출
```
3단계도 실패 시 `JSONDecodeError` 발생 → `_analyze_single_batch`의 재시도 루프로 진입.

### 4. LLM HTTP 에러 정교 분기
```python
# review_analyzer.py:285
if status != 429 and status < 500:    # 4xx (auth/quota) — 재시도 불가
    return None
# 429 / 5xx — exponential backoff 재시도
wait = 2 ** attempt
await asyncio.sleep(wait)
```
- 4xx 영구 오류는 즉시 포기 (계속 재시도해도 무의미)
- 429/5xx만 재시도

### 5. LLM Provider 팩토리 + 환경변수 스왑
```python
# llm_client_base.py:275
def get_llm_client() -> BaseLLMClient:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "litellm":     return LiteLLMClient()
    elif provider == "ollama_remote": return RemoteOllamaClient()
    else:                         return OllamaClient()
```
- `.env`만 바꿔서 **로컬 개발(Ollama 0원) ↔ 배포(LiteLLM 클라우드) ↔ RunPod GPU** 전환
- LangChain 도입 없이 multi-provider 추상화 달성

### 6. 임베딩 모델 마이그레이션 안전성
```python
# review_rag.py:55
COLLECTION_NAME = "reviews_voyage_v35"
# 이전: "reviews_bge_m3" (BGE-M3 1024-dim)
# 모델 차원이 바뀌면 컬렉션 이름을 반드시 바꿔 전체 재임베딩
```
**룬북**: `docs/runbooks/review-embedding-migration.md` (별도 문서로 관리)

### 7. 임베딩 실패 시 영벡터 합성 금지
```python
# review_rag.py:108
except Exception as e:
    logger.error(...)
    raise   # 영벡터를 합성하면 컬렉션이 영구 오염됨
```
청크 단위 실패는 해당 청크만 skip, 다음 청크는 계속 진행 — **컬렉션 무결성 우선**

### 8. FastAPI/MCP 듀얼 노출의 비결 — 싱글턴 분리
**Before** (안티패턴): `mcp/tools.py` → `api/review_analysis.py` import (mcp가 api에 의존)
**After**: 둘 다 `core/review_singletons.py`에서 import

```python
# review_singletons.py
rag = ReviewRAG()              # 같은 ChromaDB 클라이언트
analyzer = ReviewAnalyzer()    # 같은 LLM 인스턴스
```

→ FastAPI 라우터와 MCP tool이 **같은 ChromaDB 컬렉션·같은 LLM 클라이언트** 공유. 한 곳에서 임베딩하면 양쪽에서 즉시 검색 가능.

---

## 멀티테넌트 설계

```python
# mcp/tools.py:168
seller_id = getattr(user, "seller_id", None)
product_ids = await get_seller_product_ids(db, seller_id=seller_id)
if product_ids is not None:
    filter_dict = {"product_id": {"$in": product_ids}}
results = rag.search(query=query, filters=filter_dict)
```
- `seller_id` 보유 사용자 → 해당 판매자 상품 리뷰만 검색
- `seller_id` 없음 (관리자) → 전체 검색
- ChromaDB metadata 필터로 격리 (애플리케이션 레벨 누출 차단)

---

## 진행률 SSE — MCP의 `ctx.report_progress`

**Why**: 리뷰 1만건 분석은 30초+ 걸림. 사용자에게 진행률 보여주려면 SSE 필요.

**MCP 측**:
```python
# mcp/tools.py:367
async def progress_cb(p: int, msg: str | None = None):
    await ctx.report_progress(progress=p, total=100)
    if msg: await ctx.info(msg)
```

**FastAPI 측**: `GET /reviews/analyze/stream` (SSE) — 같은 `analyze_batch_with_progress` async generator 재사용

→ 두 진입점이 **동일 generator**를 사용해 진행률 전달 방식만 어댑터로 분리

---

## MCP Tool 카탈로그 (T1~T10)

| ID | Tool | 설명 |
|---|---|---|
| T1 | `embed_reviews` | shop_reviews → ChromaDB 동기화 |
| T2 | `search_reviews` | 자연어 질의 의미 검색 (top_k, 메타필터) |
| T3 | `analyze_reviews` | 동기 분석 (단일 await) |
| T4 | `analyze_reviews_with_progress` | 진행률 알림 분석 |
| T5 | `get_latest_analysis` | 최신 분석 결과 조회 |
| T6 | `get_analysis_by_id` | 특정 ID 조회 |
| T7 | `get_trends` | 주간 트렌드 + 이상 탐지 |
| T8 | `generate_pdf_report` | PDF base64 inline (5MB 가드) |
| T9 | `get_analysis_settings` | 자동 분석 설정 조회 |
| T10 | `update_analysis_settings` | 자동 분석 설정 변경 |

---

## PDF 리포트 — OS별 한글 폰트 자동 탐색

```python
# review_report.py:118
_FALLBACK_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),  # Windows
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", None),               # macOS
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", ...),           # Linux
]
```
- `settings.FONT_PATH` (사용자 명시) → OS 후보 → Helvetica 폴백
- 한글 폰트 못 찾으면 WARN만 남기고 진행 (한글은 깨질 수 있지만 비한글 리포트는 동작)

---

## 면접에서 강조할 포인트

1. **비용 의식** — 1콜 다중 분석 + 배치 병렬 = 비용 1/(3N) 절감 사례
2. **추상화의 적정선** — `BaseLLMClient` 팩토리 + 싱글턴 공유로 multi-provider × 듀얼 노출을 코드 중복 0으로 달성
3. **운영 안전성** — 임베딩 실패 시 영벡터 합성 금지, 컬렉션 마이그레이션 룬북 등 **데이터 무결성 우선**
4. **표준 호환** — MCP tool로 노출했기 때문에 LangChain Agent도 `langchain-mcp-adapters`로 호출 가능

---

## 한계와 개선 가능성

- **structured output 미적용** — JSON 파싱 3단 폴백은 동작하지만, `with_structured_output(Pydantic)` 도입 시 ~50줄 단순화 가능 (Option A — 발표 후)
- **벡터 DB 단일 인스턴스** — ChromaDB는 단일 노드. 트래픽 증가 시 Qdrant Cloud 또는 pgvector 검토
- **LLM Observability 부재** — LangSmith/Langfuse 같은 추론 추적 미적용. 디버깅은 로그 의존
- **자동 배치 분석 스케줄러 미구현** — `AnalysisSettings.batch_schedule` 필드는 있지만 cron 실행기는 미구현

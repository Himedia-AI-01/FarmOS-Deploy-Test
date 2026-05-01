# 쇼핑몰 백엔드 — Claude 레퍼런스 카드

포트 4000 | FastAPI + PostgreSQL + ChromaDB | 패키지: `uv`

---

## 코드 작성 규칙

### 환경변수 처리
- 환경변수는 각 파일에서 `os.getenv()`로 직접 읽지 않는다.
- **반드시** `app/core/config.py`의 `Settings` 클래스에 필드로 추가하고 `from app.core.config import settings`로 참조한다.
- 민감한 값(API 키, 시크릿 키, DB URL 등)은 기본값을 `""`으로 두고 실제 값은 `.env`에서만 제공한다.

### 경로 상수
경로를 하드코딩하지 말고 `from app.paths import ...` 사용.

```python
PROJECT_ROOT    # FarmOS/
LOG_DIR         # FarmOS/logs/
CHROMA_DB_PATH  # .../backend/chroma_data/
AI_DATA_DIR     # .../backend/ai/data/
```

정책 문서 폴더는 `paths.py`가 아닌 `settings.policy_docs_dir`로 접근합니다 (`.env`의 `POLICY_DOCS_DIR` 반영).

---

## 챗봇 에이전트

LangChain tool calling 기반 에이전트. Primary LLM 장애 시 LangChain `.with_fallbacks()` 체인으로 Claude로 자동 전환.

```text
ChatOpenAI (LITELLM_URL / LITELLM_API_KEY / LITELLM_MODEL)
  .bind_tools(tools).with_fallbacks([ChatAnthropic(...).bind_tools(tools)])
```

**아키텍처**: `SupervisorExecutor` 오케스트레이터 → CS 서브 에이전트(10개 도구) + OrderGraph(LangGraph, 취소/교환 멀티스텝 HitL), `MultiAgentChatbotService`

**모니터링**: LangSmith — `LANGCHAIN_TRACING_V2=true` 시 에이전트 루프 전체 자동 트레이싱

### 핵심 파일 맵

**공통 인프라**

| 파일 | 역할 |
|------|------|
| `ai/agent/llm.py` | LangChain LLM 팩토리 — `build_primary_llm()` (ChatOpenAI), `build_fallback_llm()` (ChatAnthropic) |
| `ai/agent/executor.py` | AgentExecutor — LangChain tool calling 루프, RequestContext, TraceStep, ToolMetricData |
| `ai/agent/cs_tools.py` | `build_cs_tools(rag, db, user_id)` 팩토리 + 10개 StructuredTool + Pydantic 스키마 |
| `ai/agent/holiday.py` | 공휴일 API + 월별 캐시 |
| `ai/rag.py` | `retrieve()` / `retrieve_multiple()` / `hybrid_retrieve()` + distance_threshold 필터 |
| `app/core/config.py` | Settings 클래스 — 전체 환경변수 관리 (LangSmith 포함) |
| `app/main.py` | 에이전트 초기화 + UTF-8 로그 핸들러 |
| `app/routers/chatbot.py` | POST /api/chatbot/ask?debug=true |

**에이전트 구조**

| 파일 | 역할 |
|------|------|
| `ai/agent/supervisor/executor.py` | SupervisorExecutor — LangChain tool calling 오케스트레이션 루프 |
| `ai/agent/supervisor/prompts.py` | SUPERVISOR_INPUT_PROMPT / SUPERVISOR_OUTPUT_PROMPT |
| `ai/agent/subagents/cs/prompts.py` | CS_INPUT_PROMPT / CS_OUTPUT_PROMPT |
| `ai/agent/order_graph/state.py` | OrderState TypedDict |
| `ai/agent/order_graph/nodes.py` | 노드 함수 + 조건부 라우팅 (interrupt/resume 패턴) |
| `ai/agent/order_graph/graph.py` | build_order_graph(checkpointer) |
| `ai/agent/order_graph/prompts.py` | ORDER_PROMPTS, CANCEL_KEYWORDS, CONFIRM_KEYWORDS |
| `app/services/multi_agent_chatbot.py` | MultiAgentChatbotService — ChatLog/ToolMetric 저장 포함 |
| `app/models/ticket.py` | ShopTicket — shop_tickets 테이블, 취소/교환 접수 결과 |

### 주문 상태(status) 목록

`shop_orders.status` 컬럼에 저장되는 유효한 값:

| status | 한국어 | 전환 방식 | 취소 | 교환/반품 |
|---|---|---|---|:---:|
| `pending` | 주문 접수 | 주문 생성 기본값 | 즉시 자동 | ✗ |
| `preparing` | 상품 준비 중 | admin 수동 (`PATCH /api/admin/orders/{id}/status`) | 즉시 자동 | ✗ |
| `shipped` | 배송 중 | Shipment 생성 시 자동 | 관리자 검토 | ✗ |
| `delivered` | 배송 완료 | Shipment.status=delivered 시 자동 | 불가 | ✅ |
| `cancelled` | 취소 완료 | 자동/관리자 | 불가 | ✗ |
| `returned` | 반품 완료 | 교환/취소 티켓 completed 시 자동 | 불가 | ✗ |

**상태 전환 흐름:**

```text
pending → preparing → shipped → delivered
   ↓           ↓         ↓          ↓
(자동취소)  (자동취소) (관리자취소) → returned
```

Shipment.status는 별도 관리: `registered → picked_up → in_transit → delivered`

비표준 값(`paid`, `registered`, `shipping`, `picked_up`, `in_transit` 등)은 인식되지 않습니다.  
테스트 데이터 삽입 시에도 위 목록 중 하나를 사용해야 합니다.  
기존 데이터 변환: `uv run python scripts/migrate_order_status.py --dry-run`

### 보안 규칙

`refuse_request` 도구 — 타인 정보·내부 정보·서비스 범위 외 질문·탈옥·부적절 콘텐츠를 필터링합니다.
도구가 `__REFUSED__\n사유: <코드>` 마커를 반환하면, executor.py가 이를 감지해 `ai/agent/responses.py`의 `REFUSED` 상수를 즉시 반환합니다 (LLM 재호출 없음).
reason 코드: `other_user_info` | `internal_info` | `out_of_scope` | `jailbreak` | `inappropriate`

`RequestContext.to_system_suffix()`는 로그인 여부(`로그인` / `비로그인`)만 LLM에 전달합니다. 실제 `user_id` 숫자는 시스템 프롬프트에 포함하지 않습니다 — LLM이 응답에서 내부 ID를 노출하는 것을 방지합니다.

`get_order_status` 호출 시 LLM이 `user_id`를 인자로 넘기면 **타인 정보 조회 시도**로 간주하고 즉시 거절합니다.
`GetOrderStatusInput` 스키마에 `user_id` 파라미터가 없으므로, LLM이 이를 명시한다는 것은 사용자가 특정 user_id를 요청한 신호입니다.

```python
# executor.py — _run_loop 내부
if tc["name"] == "get_order_status" and "user_id" in tc.get("args", {}):
    result = "__REFUSED__\n사유: other_user_info"   # 코드 레벨 차단
else:
    result, latency_ms = await _invoke_tool(tc, tool_map)  # 클로저 내 user_id 사용
```

### 히스토리 역할 매핑

프론트엔드가 어시스턴트 메시지를 `role: "bot"` 으로 전송합니다. `_build_history` 에서 반드시 매핑:

```python
# multi_agent_chatbot.py
_ROLE_MAP = {"user": "user", "assistant": "assistant", "bot": "assistant"}
```

누락 시 LLM이 어시스턴트 답변을 받지 못해 이전 질문을 누적해서 재답변합니다.

### 시스템 프롬프트 필수 구성 (`ai/agent/prompts.py`)

1. **도구 선택 규칙** — 로그인 필요 도구와 불필요 도구를 명시적으로 분리
2. **정책 인용 원칙** — `[doc > 조]` 형식 출처 태그가 있으면 `(근거: ...)` 형식으로 반드시 인용
3. **내부 용어 노출 금지** — 도구 이름(`search_policy`, `get_order_status` 등), 필드명(`order_item_id`, `user_id`)을 고객 응답에 포함하면 안 됨

### RAG 임계값

**현재 모델: `BAAI/bge-m3`** / 청킹: 제N조 단위 (`chunk_by_articles`)

**문서 파싱**: `parse_document()` — 확장자 기반 라우팅
- `.pdf` → `parse_pdf()`: pymupdf `find_tables()`(표→파이프 테이블) + `get_text("blocks")`(Y축 정렬) → `_apply_heading_markdown()`(제N장→##, 제N조→###) → `chunk_by_articles()`
- `.docx` → `parse_docx()`: python-docx Heading→`#`, Bold run→`**`, 표→파이프 테이블 → `chunk_by_articles()`

| 도구 | 컬렉션 | Settings 필드 | 기본값 | bge-m3 실측 거리 |
|------|--------|--------------|--------|----------------|
| `search_policy` | 정책 6종 | `rag_distance_threshold` | **0.50** | 관련 청크 0.24~0.45 |
| `search_faq` | `faq` (통합) | `rag_distance_threshold` | **0.50** | 관련 청크 0.24~0.42 |

`search_faq`는 단일 `faq` 컬렉션을 사용하며, `subcategory_slug` 메타데이터 필터로 분류를 좁힌다.
보관법·제철정보·농장소개는 구 별도 컬렉션(`storage_guide`, `season_info`, `farm_intro`)에서 `faq` 통합 컬렉션으로 이전됨.

임계값은 `app/core/config.py`의 `Settings` 클래스에서 관리하고 `.env`로 오버라이드합니다.  
임베딩 모델 변경 시 반드시 re-seed + 재측정 → `/rag-diagnostic` 스킬 참고.

### FAQ 카테고리 구조 (10개)

`search_faq` 도구의 `subcategory` 파라미터 슬러그 목록.  
변경 시 `cs_tools.py SearchFaqInput`, `migrate_json_to_faq_v2.py`, `seed_rag.py` 세 곳을 동시에 수정해야 한다.

**이커머스 기본 (sort_order 1~5)**

| slug | 이름 | 주요 소스 |
|------|------|-----------|
| `order` | 주문·결제 | faq(payment/order) + policy(payment) |
| `delivery` | 배송·물류 | faq(delivery) + policy(delivery) + product(delivery) |
| `exchange-return` | 교환·반품·환불 | faq(exchange/cancel) + policy(return) |
| `membership` | 회원·적립금 | faq(membership) + policy(membership) |
| `service` | 고객서비스 | faq(service/stock) + policy(service) |

**농산물 특화 (sort_order 6~10)**

| slug | 이름 | 주요 소스 |
|------|------|-----------|
| `product-quality` | 상품·품질·신선도 | faq(product) + product(quality/freshness/safety) + **policy(quality)** |
| `certification` | 인증·친환경 | product(certification/environment) |
| `storage` | 보관 방법 | storage_guide.json |
| `season` | 제철·수확 정보 | season_info.json |
| `origin` | 원산지·농장 | farm_info.json + product(origin) |

**폐기된 슬러그** (DB에 is_active=False로 보관): `faq`, `farm`, `product`, `policy`

### 정책 FAQ 인용 형식

`policy_faq.json`의 각 항목에는 `citation` 필드가 있고, 마이그레이션 시 답변 말미에 자동 삽입된다.

```text
...답변 본문...
(근거: 반품교환환불정책 제5조(반품 조건 및 배송비 부담) 제1항·제2항)
```

ChromaDB 메타데이터에도 `citation_doc`, `citation_article`, `citation_clause` 키로 저장된다.

### TraceStep

- `FarmOS/logs/chatbot.log` — INFO 레벨 항상 기록
- `POST /api/chatbot/ask?debug=true` — 응답 `trace` 필드에 포함

---

## 환경변수 (.env)

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | PostgreSQL 접속 URL |
| `JWT_SECRET_KEY` | FarmOS 공유 JWT 시크릿 |
| `FARMOS_API_URL` | FarmOS 백엔드 주소 (기본: `http://localhost:8000/api/v1`) |
| `ANNIVERSARY_API_KEY` | 공공데이터포털 공휴일 API 키 |
| `EMBED_PROVIDER` | 임베딩 provider — `sentence_transformers` / `openrouter` / `openai` |
| `EMBED_MODEL` | 임베딩 모델명 (기본: `BAAI/bge-m3`) |
| `EMBED_API_KEY` | openai provider 전용 API 키 |
| `LITELLM_URL` | LiteLLM 프록시 엔드포인트 — Primary + Utility LLM 공용 |
| `LITELLM_API_KEY` | LiteLLM API 키 |
| `LITELLM_MODEL` | LiteLLM 모델명 (기본: `openai/gpt-4o-mini`) |
| `LLM_PROVIDER` | LLM provider 참고값 (기본: `openrouter`) |
| `ANTHROPIC_API_KEY` | Fallback LLM — 없으면 폴백 비활성화 |
| `CLAUDE_FALLBACK_MODEL` | Claude Fallback 모델 (기본: `claude-haiku-4-5`) |
| `AGENT_MAX_ITERATIONS` | 에이전트 최대 반복 횟수 (기본: `10`) |
| `RERANKER_MODEL` | Cross-Encoder 재랭킹 모델 (기본: `dragonkue/bge-reranker-v2-m3-ko`) — 비워두면 비활성화 |
| `LANGCHAIN_TRACING_V2` | LangSmith 트레이싱 활성화 (`true` / `false`, 기본: `false`) |
| `LANGCHAIN_API_KEY` | LangSmith API 키 — `smith.langchain.com`에서 발급 |
| `LANGCHAIN_PROJECT` | LangSmith 프로젝트명 (기본: `farmos-shoppingmall-chatbot`) |
| `POLICY_DOCS_DIR` | 정책 문서 폴더 (기본: `ai/docs/`, gitignore — 로컬 배치 필요) |

Provider 전환은 `LITELLM_URL` / `LITELLM_API_KEY` / `LITELLM_MODEL` 세 값만 교체하면 됩니다.

---

## 헬스체크

```bash
# 쇼핑몰 백엔드 DB 연결 상태 확인
curl http://localhost:4000/health
# → {"status": "ok", "storage": "postgres"}
# → {"status": "degraded", "storage": "postgres"}  (DB 장애 시)
```

FarmOS 백엔드: `GET /api/v1/health` (동일 응답 형식)

---

## 자주 쓰는 명령

```bash
# 서버 실행
uv run uvicorn app.main:app --reload --port 4000

# RAG 시딩 + 검증
uv run python scripts/seed_and_verify.py

# 테스트
uv run pytest

# 챗봇 API 테스트
curl -X POST http://localhost:4000/api/chatbot/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"딸기 재고 있어?","session_id":null,"history":[]}'

# 추론 과정 확인
curl -X POST "http://localhost:4000/api/chatbot/ask?debug=true" \
  -H "Content-Type: application/json" \
  -d '{"question":"주문 배송 언제와?","session_id":null,"history":[]}'
```

---

## 상세 문서

| 위치 | 내용 |
|------|------|
| `ai/README.md` | AI 모듈 전체 구조, RAG 셋업 가이드 |
| `ai/agent/README.md` | 운영 모드, 도구 전체 목록, AgentExecutor 동작 원리 |
| `ai/agent/supervisor/README.md` | SupervisorExecutor 오케스트레이션, 도구 선택 기준, 진행 중 플로우 처리 |
| `ai/agent/order_graph/README.md` | LangGraph interrupt/resume 패턴, 취소·교환 플로우, DB 주입, ShopTicket |
| `ai/agent/subagents/cs/README.md` | CS_TOOLS 구성, CS 에이전트 제약 사항 |
| `ai/agent/clients/README.md` | LLM 클라이언트, provider 전환 예시 |
| `app/core/README.md` | Settings 필드 전체 목록 |
| `app/services/README.md` | 서비스 레이어 구조 |
| `/chatbot-agent` 스킬 | 챗봇 에이전트 전체 기획 문서 |
| `/rag-diagnostic` 스킬 | RAG 검색 이상 시 진단 절차 (컬렉션 확인·거리 측정·재시딩) |
| `/agent-pipeline-test` 스킬 | 서버 없이 에이전트 파이프라인 전체 검증 (도구 선택·인용·내부 용어) |
| `/agent-rag-health-review` 스킬 | 에이전트+RAG 체계적 헬스 리뷰 — N+1 쿼리, TypedDict 누락, 라우팅 오탐, 컬렉션 미시딩, 임계값 하드코딩 점검 |
| `/check-fe-be-sync` 스킬 | 백엔드 ORDER_PROMPTS 마커·Supervisor 라우팅 ↔ 프론트 parseOrderFlowMessage 버튼 동기화 점검 |

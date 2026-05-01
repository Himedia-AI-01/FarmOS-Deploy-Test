# FarmOS — 스마트 팜 통합 관리 시스템 & 농산물 쇼핑몰

한국 농업인을 위한 **농장 운영 관리 + 직거래 쇼핑몰** 통합 플랫폼입니다.
취업 포트폴리오 목적으로 설계되었으며, 현업 수준의 아키텍처 설계와 AI 기능 구현에 초점을 맞춥니다.

---

## 목차

- [프로젝트 구조](#프로젝트-구조)
- [기술 스택](#기술-스택)
- [주요 기능](#주요-기능)
- [CS 챗봇 아키텍처](#cs-챗봇-아키텍처)
- [FAQ 지식베이스 시스템](#faq-지식베이스-시스템)
- [설치 및 실행](#설치-및-실행)
- [API 문서](#api-문서)
- [아키텍처 결정 기록](#아키텍처-결정-기록)

---

## 프로젝트 구조

```text
FarmOS/
├── frontend/                  React 19 + TypeScript + Vite   포트 5173
│   └── src/
│       ├── modules/           기능별 모듈 (farm, harvest, revenue, ...)
│       └── components/        공통 컴포넌트
│
├── backend/                   FastAPI + PostgreSQL            포트 8000
│   ├── app/
│   │   ├── api/               라우터
│   │   ├── models/            SQLAlchemy ORM 모델
│   │   └── schemas/           Pydantic 스키마
│   └── ai/                    AI/ML 서비스
│
└── shopping_mall/
    ├── frontend/              React 19 + TypeScript + Vite   포트 5173 (별도)
    │   └── src/
    │       ├── admin/         어드민 대시보드
    │       └── components/    쇼핑몰 공통 컴포넌트
    │
    └── backend/               FastAPI + PostgreSQL + ChromaDB 포트 4000
        ├── app/
        │   ├── routers/       API 라우터 (chatbot, knowledge, faq_categories, ...)
        │   ├── models/        SQLAlchemy ORM 모델
        │   └── services/      비즈니스 로직 (챗봇, 지식베이스 동기화, ...)
        └── ai/
            ├── agent/         멀티에이전트 챗봇 (LangChain + LangGraph)
            └── rag.py         BM25 + Dense 하이브리드 RAG
```

---

## 기술 스택

### 프론트엔드 (공통)

| 분류 | 기술 |
|------|------|
| 프레임워크 | React 19 + TypeScript |
| 빌드 도구 | Vite |
| 서버 상태 | TanStack Query v5 |
| 스타일 | Tailwind CSS |
| 아이콘 | Material Symbols |
| HTTP | Axios (credential: include) |
| 알림 | react-hot-toast |

### 백엔드 (공통)

| 분류 | 기술 |
|------|------|
| 프레임워크 | FastAPI |
| ORM | SQLAlchemy (동기 Session) |
| DB | PostgreSQL |
| 패키지 매니저 | uv |
| 스케줄러 | APScheduler |

### 쇼핑몰 AI 레이어

| 분류 | 기술 |
|------|------|
| 에이전트 | LangChain StructuredTool + LangGraph |
| LLM (Primary) | OpenAI 호환 (OpenRouter/Ollama 전환 가능) |
| LLM (Fallback) | Anthropic Claude Haiku |
| 체크포인터 | LangGraph + AsyncPostgresSaver |
| 벡터 DB | ChromaDB (persistent) |
| 검색 방식 | BM25 + Dense 하이브리드 (RRF 랭킹) |
| Reranker | sentence-transformers (선택적) |
| 임베딩 | sentence-transformers / OpenAI embeddings |

---

## 주요 기능

### FarmOS (농장 관리)

- **작물 재배 일정**: 파종~수확 사이클 관리 및 캘린더 뷰
- **수확량 추적**: 품목별 수확량 입력 및 통계
- **수익/지출 관리**: 농장 손익 계산서 자동 생성
- **주간 리포트**: LLM 기반 농장 운영 요약 자동 생성
- **고객 세그먼트 분석**: RFM 기반 구매 패턴 분석

### 쇼핑몰

- **상품/카테고리 관리**: 재고 실시간 추적
- **주문/배송 관리**: 상태 기반 워크플로우
- **교환/반품 처리**: Human-in-the-Loop 교환 신청 시스템
- **CS AI 챗봇**: 멀티에이전트 자동 응대 (아래 상세 설명)
- **FAQ 지식베이스**: 관리자 편집 가능한 챗봇 학습 데이터 + 애널리틱스

---

## CS 챗봇 아키텍처

### 개요

고객 문의를 자동 처리하는 멀티에이전트 시스템입니다. LangGraph 기반 그래프로 복합 질문, 멀티스텝 추론, Human-in-the-Loop 교환 신청을 지원합니다.

```text
고객 질문
    │
POST /api/chatbot/ask
    │
MultiAgentChatbotService
    │
SupervisorExecutor (LangGraph)
    ├── CS 일반 질문  →  AgentExecutor (LangChain tool_use)
    │                        ├── search_faq       (FAQ 통합 RAG)
    │                        ├── search_policy    (정책 문서 RAG)
    │                        ├── get_order_status (DB 조회)
    │                        ├── search_products  (DB 조회)
    │                        ├── get_product_detail
    │                        └── escalate_to_agent
    │
    └── 교환/반품     →  OrderGraph (LangGraph StateGraph)
                             ├── create_exchange_request  (pending 저장)
                             ├── confirm_pending_action   (최종 실행)
                             └── cancel_pending_action    (취소)
```

### LLM 이중화 (Primary / Fallback)

Primary LLM 장애 시 자동으로 Fallback으로 전환합니다.

```python
primary  = build_primary_llm()   # OpenAI 호환 — .env의 PRIMARY_MODEL
fallback = build_fallback_llm()  # Claude Haiku  — .env의 FALLBACK_MODEL
```

### RAG 파이프라인

BM25(키워드) + Dense(의미론) 하이브리드 검색 후 RRF로 통합 랭킹:

```text
질문
  │
normalize_query()   — 조사 제거, 소문자화
  │
  ├── BM25 검색  (json 인덱스)
  └── Dense 검색 (ChromaDB 코사인)
        │
      RRF 통합
        │
  [선택] Reranker (cross-encoder)
        │
  거리 임계값 필터 (distance_threshold=0.5)
        │
  인용 문서 ID 수집 (CSToolContext.cited_faq_ids)
```

### 인증 / 접근 제어

- JWT in HttpOnly 쿠키
- 비로그인 사용자도 챗봇 사용 가능 (user_id=None)
- 교환 신청, 주문 조회는 로그인 필수

---

## FAQ 지식베이스 시스템

> v2에서 기존 4개 분리 카테고리(faq / storage_guide / season_info / farm_info)를 **단일 FAQ 시스템**으로 통합했습니다.

### 구조

```text
FaqCategory (어드민 관리)
    id, name, slug, color, icon, sort_order, is_active
    └── KnowledgeDoc (FAQ 문서)
            id, title, content, faq_category_id, chroma_doc_id
            is_active, extra_metadata
            └── FaqCitation      (챗봇 인용 이력)
                FaqFeedback      (사용자 피드백 👍/👎)
```

### 데이터 흐름

```text
[관리자] KnowledgePage 어드민에서 FAQ 등록/수정
    │
PostgreSQL 저장 + ChromaDB 백그라운드 동기화 (KnowledgeSync)
    │
[챗봇] search_faq 도구 호출 시 ChromaDB 검색
    │   metadata.db_id → CSToolContext.cited_faq_ids에 누적
    │
[서비스] MultiAgentChatbotService.answer() 완료 후
    │   cited_faq_ids → shop_faq_citations 테이블 기록
    │
[고객] 챗봇 응답 하단 👍/👎 버튼 클릭
    │   POST /api/chatbot/faq-feedback
    │
[어드민] KnowledgePage 애널리틱스 컬럼
    └── 인용 수 · 도움됨 % · 개선 필요 표시
```

### 어드민 UI (`KnowledgePage.tsx`)

- **좌측 사이드바**: 카테고리 목록 (추가/수정/삭제 hover 버튼)
- **카테고리 관리**: 이름, 슬러그, 색상, 아이콘 커스터마이징
- **FAQ 테이블**: 질문 제목, 카테고리 칩, 인용 수, 도움됨 % 미니 바
- **정렬 옵션**: 최근 등록순 / 많이 인용된 순 / 도움됨 높은 순 / 개선 필요 순
- **개선 필요 표시**: 인용은 됐지만 👎 > 👍 인 문서에 ⚠️ 아이콘

### API 엔드포인트

```text
# FAQ 카테고리
GET    /api/admin/faq-categories          목록 (include_inactive 옵션)
POST   /api/admin/faq-categories          카테고리 생성
PUT    /api/admin/faq-categories/{id}     수정
DELETE /api/admin/faq-categories/{id}     삭제 (force=true 시 문서 미분류 이동)

# FAQ 문서
GET    /api/admin/knowledge               목록 (카테고리 필터, 애널리틱스 포함)
POST   /api/admin/knowledge               문서 생성 + ChromaDB 동기화
GET    /api/admin/knowledge/{id}          단건 조회
PUT    /api/admin/knowledge/{id}          수정 + ChromaDB 동기화
DELETE /api/admin/knowledge/{id}          소프트 삭제

# 챗봇 피드백
POST   /api/chatbot/faq-feedback          👍/👎 피드백 제출 (chat_log_id 기준 upsert)
```

### ChromaDB 컬렉션 구조

| 컬렉션명 | 내용 | 메타데이터 |
|----------|------|-----------|
| `faq` | 모든 FAQ 문서 (통합) | `db_id`, `subcategory_slug`, `subcategory_name`, `tags` |
| `return_policy` | 반품·교환·환불 정책 | — |
| `payment_policy` | 결제·적립금 정책 | — |
| `delivery_policy` | 배송 정책 | — |
| `quality_policy` | 상품 품질 보증 정책 | — |
| `service_policy` | 고객서비스 운영 정책 | — |
| `membership_policy` | 개인정보·회원 정책 | — |

---

## 설치 및 실행

### 사전 요구사항

- Python 3.11+, Node.js 20+
- PostgreSQL 15+
- [`uv`](https://github.com/astral-sh/uv) 패키지 매니저

### 환경 변수

```bash
# shopping_mall/backend/.env
DATABASE_URL=postgresql://...
LANGGRAPH_POSTGRES_URL=postgresql://...
PRIMARY_MODEL=openai/gpt-4o-mini          # OpenRouter 형식 또는 모델명
FALLBACK_MODEL=claude-haiku-4-5-20251001
OPENROUTER_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2   # 선택
```

### 개발 서버 실행

```bash
# FarmOS 백엔드
cd backend && uv run uvicorn main:app --reload

# FarmOS 프론트엔드
cd frontend && npm install && npm run dev

# 쇼핑몰 백엔드
cd shopping_mall/backend && uv run uvicorn main:app --reload --port 4000

# 쇼핑몰 프론트엔드
cd shopping_mall/frontend && npm install && npm run dev
```

### ChromaDB 초기 시딩

```bash
# 정책 문서(PDF/DOCX)를 ai/docs/에 배치 후 실행
cd shopping_mall/backend
uv run python scripts/seed_and_verify.py
```

---

## API 문서

서버 실행 후 자동 생성 Swagger UI에서 확인:

- FarmOS: http://localhost:8000/docs
- 쇼핑몰: http://localhost:4000/docs

---

## 아키텍처 결정 기록

### ADR-001: 인텐트 라우터 → 멀티에이전트 전환

**배경**: 기존 `if/elif` 인텐트 라우터는 복합 질문("딸기 재고 있어? 보관법도 알려줘")을 인텐트 하나로만 처리.

**결정**: LangChain `tool_use` 기반 AgentExecutor + LangGraph SupervisorExecutor로 전환.

**결과**: 멀티스텝 추론, 복합 질문 처리, Human-in-the-Loop 교환 신청 구현.

---

### ADR-002: FAQ 4개 컬렉션 → 단일 통합 컬렉션

**배경**: `faq` / `storage_guide` / `season_info` / `farm_info` 4개 ChromaDB 컬렉션이 별도 RAG 도구로 분리되어 유지보수 복잡도 증가. 카테고리 추가/변경이 코드 배포를 요구.

**결정**: 단일 `faq` ChromaDB 컬렉션 + PostgreSQL `FaqCategory` 테이블로 통합. 어드민이 카테고리를 코드 없이 관리.

**결과**: 도구 수 12 → 9개로 감소. 카테고리 추가/변경이 어드민 UI에서 즉시 반영.

---

### ADR-003: FAQ 인용 애널리틱스 — CSToolContext 패턴

**배경**: RAG 도구가 어떤 문서를 인용했는지 추적하려면 도구 실행 중 side-effect가 필요.

**결정**: `CSToolContext` dataclass를 클로저로 캡처해 `cited_faq_ids` 누적. `AgentResult`로 상위 서비스에 전달 후 `shop_faq_citations`에 일괄 기록.

**결과**: 도구 함수가 순수 함수에 가깝게 유지되면서 인용 추적 가능.

---

### ADR-004: BM25 + Dense 하이브리드 RAG

**배경**: Dense 단독 검색은 정확한 상품명·정책 번호 매칭에 취약.

**결정**: BM25(키워드 정확도) + Dense(의미론적 유사도)를 RRF로 통합 랭킹. 선택적 Reranker(cross-encoder) 추가 지원.

**결과**: 상품명 검색 정확도 향상, 모호한 질문의 의미론적 매칭 유지.

---

*포트폴리오 프로젝트 — Jiwon (onegeewon@gmail.com)*

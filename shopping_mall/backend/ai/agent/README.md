# ai/agent/

LangChain tool calling 기반 챗봇 에이전트 서브패키지.  
**SupervisorExecutor** 오케스트레이터가 CS 에이전트(`AgentExecutor`)와 OrderGraph(LangGraph)를 조율합니다.

---

## 디렉터리 구조

```
ai/agent/
├── __init__.py               공개 API (AgentExecutor, RequestContext, build_primary_llm 등)
├── executor.py               AgentExecutor — LangChain tool calling 루프
├── cs_tools.py               build_cs_tools() 팩토리 + 10개 StructuredTool + Pydantic 스키마
├── responses.py              사전 정의 응답 (Canned Responses) — LLM 없이 즉시 반환
├── llm.py                    LangChain LLM 팩토리 (ChatOpenAI / ChatAnthropic) + LangSmith 환경 주입
├── prompts.py                CS 에이전트 기본 시스템 프롬프트
├── holiday.py                공휴일 API + 캐시 (배송 예정일 보정)
│
├── tone_policy.py            응답 톤앤매너 정책 계층 (BASE / CHATBOT / FAQ)
│
├── clients/                  ⚠️ 레거시 — Python 구현 제거됨, llm.py로 대체
│   └── README.md             이전 AgentClient 패턴 참고용
│
├── supervisor/               오케스트레이터
│   ├── executor.py           SupervisorExecutor — LangChain tool calling 루프
│   └── prompts.py            SUPERVISOR_INPUT_PROMPT / SUPERVISOR_OUTPUT_PROMPT
│
├── subagents/cs/             CS 서브 에이전트
│   └── prompts.py            CS_INPUT_PROMPT / CS_OUTPUT_PROMPT
│
└── order_graph/              LangGraph 기반 취소/교환 플로우
    ├── state.py              OrderState TypedDict
    ├── nodes.py              그래프 노드 함수 + 조건부 라우팅
    ├── graph.py              build_order_graph() — StateGraph 컴파일
    └── prompts.py            ORDER_PROMPTS 딕셔너리 + 키워드 상수
```

---

## 요청 흐름

```
POST /api/chatbot/ask
  → MultiAgentChatbotService.answer()
  → SupervisorExecutor.run()
      ├── preflight refusal
      │     타인 정보 / 내부 운영 정보 / jailbreak / 범위 외 / 부적절 요청은
      │     LLM·도구 호출 전에 고정 응답으로 차단
      ├── call_cs_agent  → AgentExecutor.run() [build_cs_tools 10개]
      │                     RAG 조회 / DB 읽기 / escalate / refuse
      └── call_order_agent → OrderGraph (LangGraph StateGraph)
                              interrupt/resume 기반 멀티스텝 HitL
                              취소: 주문 선택 → 사유 → get_refund_method → 확인 → ShopTicket
                              교환: 주문 선택 → 품목 선택 → 재고 확인 → 사유 → 확인 → ShopTicket
                              변경: 주문 선택 → get_change_type → get_change_detail → 확인 → ShopTicket
  → ChatLog + ToolMetric 저장
```

Supervisor LLM이 질문을 분석해 서브 에이전트를 선택·호출합니다.  
취소/교환/변경은 OrderGraph가 전담하며 여러 턴에 걸쳐 진행됩니다.

---

## LLM 체인 (LangChain)

```python
# llm.py
primary = build_primary_llm()   # ChatOpenAI (PRIMARY_LLM_* 환경변수)
fallback = build_fallback_llm() # ChatAnthropic | None (ANTHROPIC_API_KEY)

# executor.py 내부
llm_with_tools = primary.bind_tools(tools).with_fallbacks([fallback.bind_tools(tools)])
```

Primary LLM 실패 시 LangChain의 `.with_fallbacks()` 체인이 자동 전환합니다.  
둘 다 실패하면 예외가 전파됩니다.

---

## 도구 목록 (`cs_tools.py`)

9개 도구가 `build_cs_tools(rag, db, user_id)` 팩토리에서 생성됩니다.
런타임 의존성(rag_service, db, user_id)을 클로저로 캡처하며, 요청마다 새로 생성됩니다.

| 도구 | 유형 | `TOOL_TO_INTENT` | 설명 |
|------|------|-----------------|------|
| `search_faq` | RAG | `"other"` | 통합 FAQ 지식베이스 |
| `search_policy` | RAG | `"policy"` | 6개 정책 문서 (하이브리드 검색) |
| `get_order_status` | DB | `"delivery"` | 주문·배송 현황 + 공휴일 도착일 보정 |
| `search_products` | DB | `"stock"` | 상품 검색·재고 확인 |
| `get_product_detail` | DB | `"stock"` | 상품 상세 정보 |
| `escalate_to_agent` | Action | `"escalation"` | 상담원 에스컬레이션 |
| `refuse_request` | Action | `"refusal"` | 허용되지 않는 요청 거절 |
| `cancel_order` | Action | `"cancel"` | 레거시 직접 취소 도구 (OrderGraph 우선) |
| `process_refund` | Action | `"refund"` | 레거시 환불 처리 도구 |

> 신규 고객 대화의 취소/교환/변경 접수는 `call_order_agent` → OrderGraph가 우선 처리합니다.

---

## AgentExecutor 공통 동작

`AgentExecutor`는 CS 서브 에이전트로 사용됩니다.

**일반 CS 호출**:
1. `llm_with_tools.ainvoke()` → 필요한 CS 도구 선택
2. 도구 실행: RAG는 병렬, DB/Action은 순차
3. `output_llm.ainvoke()` → 도구 결과 기반 최종 고객 답변 생성

**Supervisor `tool_hint` 호출**:
Supervisor가 `search_policy`, `get_order_status`, `search_products` 같은 read-only 도구와 인자를 확정하면
CS 에이전트는 도구 선택 LLM 호출을 생략하고 해당 도구를 직접 실행합니다.

**병렬 실행**: RAG 도구(`_RAG_TOOL_NAMES`)는 `asyncio.gather`로 동시 실행.  
DB/Action 도구는 SQLAlchemy Session 공유 문제로 순차 실행.

**보안 — `get_order_status`**: LLM이 `user_id` 파라미터를 인자로 전달하면  
타인 정보 조회 시도로 간주하고 `__REFUSED__\n사유: other_user_info`를 반환합니다.

**보안 — Supervisor preflight**: 고객 상담 채널에서 처리하면 안 되는 요청은
`SupervisorExecutor.run()` 진입 직후 `_preflight_refusal_reason()`으로 먼저 차단합니다.
LLM tool selection, CS 도구, OrderGraph 호출 전에 종료되므로 내부 정보 조회 가능성이 응답에 암시되지 않습니다.

| `reason` 코드 | 차단 예시 |
|--------------|----------|
| `other_user_info` | 타인의 주문·배송·주소·연락처 조회 |
| `internal_info` | 매출·운영 통계·관리자 대시보드·DB/SQL·시스템 프롬프트 |
| `out_of_scope` | 금융·의료·법률·정치 등 쇼핑몰 상담 범위 밖 고위험 조언 |
| `jailbreak` | 프롬프트 조작·탈옥 시도 |
| `inappropriate` | 욕설·성적 요청 등 부적절한 요청 |

**보안 — `refuse_request`**: LLM이 직접 호출하는 콘텐츠 필터 도구입니다.  
`reason` 코드에 따라 `__REFUSED__\n사유: <코드>` 마커를 반환하고,  
executor.py가 이를 감지해 `responses.refusal_response(reason)` 고정 응답을 즉시 반환합니다 (LLM 재호출 없음).

| `reason` 코드 | 거절 대상 |
|--------------|----------|
| `other_user_info` | 타인의 개인정보·주문·계정 조회 시도 |
| `internal_info` | 내부 시스템·DB·직원 정보·프롬프트 요청 |
| `out_of_scope` | 서비스 범위 외 질문 (금융·의료·법률·정치 등) |
| `jailbreak` | 프롬프트 조작·탈옥 시도 |
| `inappropriate` | 욕설·혐오 표현 등 부적절한 요청 |

---

## 사전 정의 응답 (`responses.py`)

LLM을 거치지 않고 즉시 반환되는 고정 문자열을 한 파일에서 관리합니다.  
응답 문구를 수정할 때 **이 파일 하나만 편집**합니다.

| 상수 | 사용 위치 | 설명 |
|------|----------|------|
| `LOGIN_REQUIRED` | executor.py, supervisor/executor.py, cs_tools.py | 로그인 필요 안내 |
| `REFUSED`, `REFUSED_*` | executor.py, supervisor/executor.py | `__REFUSED__` 또는 preflight refusal 감지 시 즉시 반환 |
| `ESCALATION_HIGH_URGENCY` | cs_tools.py | 긴급 상담원 연결 |
| `ESCALATION_NORMAL` | cs_tools.py | 일반 상담원 연결 |
| `MAX_ITERATIONS_EXCEEDED` | executor.py, supervisor/executor.py | 최대 반복 초과 |
| `LLM_GENERATION_FAILED` | executor.py, supervisor/executor.py | LLM 응답 생성 실패 |
| `SERVICE_TEMPORARY_ERROR` | multi_agent_chatbot.py | 서비스 전체 오류 |
| `TRUNCATION_SUFFIX` | executor.py | 답변 길이 초과 시 말미 문구 |

---

## RequestContext

`AgentExecutor.run()` 호출 시 시스템 프롬프트 끝에 자동 주입됩니다.

```
## 현재 요청 컨텍스트
- 날짜/시각: 2026-04-20 14:32
- 사용자 상태: 로그인
- 주문 조회 가능: 예
```

---

---

## 응답 톤앤매너 정책 (`tone_policy.py`)

모든 에이전트 응답에 적용되는 톤앤매너를 계층 구조로 관리합니다.

```text
BASE_TONE_POLICY          — 호칭("고객님") · 어투(반격식체 ~해요) · 이모지 금지
├── CHATBOT_TONE_POLICY   — BASE + 공감 표현 · 대화 흐름 · 자연스러운 마무리
└── FAQ_TONE_POLICY       — BASE + 자기완결성 · 명확성 우선 · 일반화된 표현
```

| 상수 | 사용 프롬프트 |
|------|-------------|
| `CHATBOT_TONE_POLICY` | `CS_INPUT_PROMPT`, `CS_OUTPUT_PROMPT`, `SUPERVISOR_OUTPUT_PROMPT` |
| `FAQ_TONE_POLICY` | `FAQ_WRITER_SYSTEM_PROMPT` |

새 에이전트/채널 추가 시 `BASE_TONE_POLICY`를 상속하는 전용 상수를 이 파일에 추가하세요.

---

## 관련 문서

| 위치 | 내용 |
|------|------|
| `supervisor/README.md` | SupervisorExecutor 오케스트레이션 로직 |
| `order_graph/README.md` | LangGraph interrupt/resume 패턴, 취소/교환 플로우 |
| `clients/README.md` | 구 LLM 클라이언트 패턴 (레거시 참고용) |

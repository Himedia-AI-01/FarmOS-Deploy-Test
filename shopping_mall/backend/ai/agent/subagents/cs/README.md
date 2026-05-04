# ai/agent/subagents/cs/

CS(Customer Service) 서브 에이전트 — 정보 조회 전담.  
`AgentExecutor`를 재사용하며, 요청마다 `build_cs_tools()` 팩토리로 도구를 생성합니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `prompts.py` | `CS_INPUT_PROMPT` (도구 선택용), `CS_OUTPUT_PROMPT` (합성용) |

> `tools.py`는 LangChain 전환(2026-04-22)으로 **삭제**되었습니다.  
> 도구 구현은 `ai/agent/cs_tools.py`의 `build_cs_tools()` 팩토리로 이동되었습니다.

---

## build_cs_tools() 팩토리

```python
# ai/agent/cs_tools.py
def build_cs_tools(rag_service, db, user_id) -> list[StructuredTool]:
    """요청마다 호출 — rag/db/user_id를 클로저로 바인딩."""
    async def search_faq(query: str, top_k: int = 3) -> str: ...
    async def get_order_status(order_id: int | None = None) -> str: ...
    # ... 10개 클로저 ...
    return [StructuredTool.from_function(coroutine=fn, name=...) for ...]
```

각 도구는 **Pydantic 스키마** (`SearchFaqInput`, `GetOrderStatusInput` 등)로 입력을 검증합니다.

---

## CS 도구 목록 (10개)

```
RAG  : search_faq, search_storage_guide, search_season_info, search_policy, search_farm_info
DB   : get_order_status, search_products, get_product_detail
Action: escalate_to_agent, refuse_request
```

**제외된 도구 없음**: 취소/교환 접수(`create_exchange_request` 등)는 처음부터 OrderGraph가 전담합니다.

---

## 두 가지 프롬프트 역할

| 프롬프트 | 전달 대상 LLM | 역할 |
|---------|-------------|------|
| `CS_INPUT_PROMPT` | `llm_with_tools` | 도구 선택 지시 |
| `CS_OUTPUT_PROMPT` | `output_llm` | 도구 결과 → 최종 답변 합성 |

두 프롬프트 모두 `ai/agent/tone_policy.py`의 `CHATBOT_TONE_POLICY`를 임포트하여 응답 톤앤매너를 적용합니다.
톤앤매너 정책 변경 시 `tone_policy.py`만 수정하면 됩니다.

---

## 인스턴스화 (`app/main.py`)

```python
cs_executor = AgentExecutor(
    primary=primary,          # ChatOpenAI
    fallback=fallback,        # ChatAnthropic | None
    rag_service=rag,
    max_iterations=settings.agent_max_iterations,
)
# 도구는 run() 호출 시마다 build_cs_tools(rag, db, user_id)로 생성됩니다.
```

`SupervisorExecutor`가 `call_cs_agent`를 실행하면:  
`cs_executor.run(user_message=query, input_system=CS_INPUT_PROMPT, output_system=CS_OUTPUT_PROMPT, history=[])` 호출.  
CS 에이전트는 히스토리 없이 Supervisor가 전달한 쿼리만 처리합니다.

---

## CS_INPUT_PROMPT 주요 지침

- 배송 현황은 로그인 사용자에게만 `get_order_status` 사용
- 비로그인 사용자의 배송 문의 → `search_policy(policy_type="delivery")`로 정책 안내
- 정책 인용 시 출처 태그 포함 (`(근거: ...)`)

### 교환·반품·불량 신고 응답 규칙

CS 에이전트는 교환/반품을 **직접 접수하지 않습니다**. 아래 형식으로만 응답합니다:

```text
[공감 한 문장]

교환과 반품·환불 중 원하시는 처리 방법을 알려주세요.

1. 교환 — 동일 상품으로 교체
2. 반품·환불 — 반품 후 환불 처리
```

고객이 방법을 선택하면 Supervisor가 자동으로 OrderGraph로 연결합니다.

---

## 내부 용어 노출 검증

```python
FORBIDDEN = [
    'call_cs_agent', 'call_order_agent',
    'search_policy', 'search_faq', 'get_order_status',
    'order_item_id', 'user_id', 'session_id',
    '다음 단계', '접수 처리 단계', '별도 절차',
]
leaks = [t for t in FORBIDDEN if t in response]
assert not leaks, f'내부 용어 노출: {leaks}'
```

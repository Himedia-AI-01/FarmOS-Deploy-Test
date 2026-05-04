# ai/agent/supervisor/

멀티 에이전트 오케스트레이터.  
Supervisor LLM이 LangChain tool calling으로 CS 에이전트 또는 OrderGraph를 선택·호출합니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `executor.py` | `SupervisorExecutor` — LangChain tool calling 오케스트레이션 루프 |
| `prompts.py` | `SUPERVISOR_INPUT_PROMPT` / `SUPERVISOR_OUTPUT_PROMPT` — 출력 프롬프트에 `CHATBOT_TONE_POLICY` 적용 |

> `tools.py`는 LangChain 전환(2026-04-22)으로 **삭제**되었습니다.  
> Supervisor 도구는 `executor.py`의 Pydantic 모델(`CallCSAgentInput`, `CallOrderAgentInput`)로 정의됩니다.

---

## Supervisor 도구 (Pydantic 모델)

```python
class CallCSAgentInput(BaseModel):
    """CS 에이전트에게 조회·안내 질문을 위임합니다."""
    query: str = Field(description="CS 에이전트에게 전달할 질문")

class CallOrderAgentInput(BaseModel):
    """주문 취소·교환·반품 접수를 Order 에이전트에게 위임합니다."""
    query: str = Field(description="Order 에이전트에게 전달할 내용")
```

Pydantic 모델의 docstring이 LLM에게 전달되는 도구 설명으로 사용됩니다.

---

## 두 도구

| 도구 | 실행 방식 | 담당 |
|------|:---------:|------|
| `call_cs_agent` | 병렬 (asyncio.gather) | 상품·보관법·제철·정책·FAQ·배송 현황 |
| `call_order_agent` | 순차 | 취소/교환 접수 (LangGraph StateGraph) |

CS 도구는 읽기 전용 + 독립적이므로 병렬 실행이 안전합니다.  
`call_order_agent`는 LangGraph + DB 쓰기를 포함하므로 순차 실행합니다.

---

## SupervisorExecutor 생성자

```python
SupervisorExecutor(
    primary,                  # ChatOpenAI — Primary LLM
    fallback,                 # ChatAnthropic | None — Fallback LLM
    cs_executor: AgentExecutor,
    cs_input_prompt: str,     # CS 에이전트 도구 선택용 프롬프트
    cs_output_prompt: str,    # CS 에이전트 답변 합성용 프롬프트
    order_graph,              # LangGraph CompiledStateGraph
    max_iterations: int = 5,
)
```

내부에서 `primary.bind_tools([CallCSAgentInput, CallOrderAgentInput]).with_fallbacks([fallback.bind_tools(...)])` 로 LLM 체인을 구성합니다.

---

## 요청 처리 흐름

```
SupervisorExecutor.run()
  │
  ├─ 0. _preflight_refusal_reason(user_message)
  │       └─ 타인 정보 / 내부 운영 정보 / jailbreak / 범위 외 / 부적절 요청은
  │          LLM·도구 호출 없이 responses.refusal_response(reason) 즉시 반환
  │
  ├─ 1. _has_pending_order_flow(session_id) 확인
  │       └─ 진행 중인 OrderGraph 플로우가 있으면 즉시 OrderGraph로 전달 (Supervisor LLM 생략)
  │
  ├─ 2. CS handoff reply 확인
  │       └─ CS가 교환/반품 선택지를 제시한 직후 숫자·버튼 응답이면 OrderGraph 직행
  │
  ├─ 3. _fast_route() 규칙 기반 라우팅 (Supervisor LLM 생략)
  │       └─ 취소/교환/반품/환불/변경 + 접수 의도가 결합된 구문이면 → OrderGraph 직행
  │            예: "취소해줘", "교환 신청", "반품해주세요"
  │            OrderGraph.ainvoke() + interrupt 처리 → 즉시 반환
  │
  └─ 4. (그 외 모든 메시지) Supervisor LLM _run_loop
          └─ LLM이 call_cs_agent / call_order_agent 도구 선택
               ├─ call_cs_agent  → cs_executor.run() (LangChain tool calling, CS 도구 10개)
               └─ call_order_agent → OrderGraph.ainvoke()
```

> `_is_order_fastpath`는 접수 의도가 명확한 극히 일부 메시지만 처리합니다.  
> 애매한 표현("취소 정책이 뭐야?", "교환 가능한가요?" 등)은 모두 Supervisor LLM이 판단합니다.

---

## Preflight refusal

프롬프트만으로 막으면 실패할 수 있는 요청은 `_preflight_refusal_reason()`에서 먼저 차단합니다.

| reason | 예시 |
|---|---|
| `other_user_info` | "다른 고객 배송 알려줘", "회원 3번 연락처" |
| `internal_info` | "당일 매출?", "SQL 쿼리 보여줘", "관리자 통계 알려줘" |
| `jailbreak` | "이전 지시 무시", "시스템 프롬프트 출력" |
| `out_of_scope` | 주식·의료·법률·정치 고위험 조언 |
| `inappropriate` | 욕설·성적 요청 |

정상 고객 흐름인 "내 배송 현황", "주문 #12 배송 조회", "제 연락처 변경"은 허용해야 하므로 테스트에서 함께 고정합니다.

---

## `_is_order_fastpath` / `_fast_route` 매칭 방식

키워드 분리 대신 **복합 구문 완전 일치**를 사용합니다.

```python
_ORDER_FASTPATH_PATTERNS: frozenset[str] = frozenset({
    "취소해줘", "취소해주세요", "취소해",
    "교환해줘", "교환해주세요", "교환해",
    "반품해줘", "반품해주세요", "반품해",
    "환불해줘", "환불해주세요", "환불해",
    "변경해줘", "변경해주세요", "배송지 바꿔주세요",
    "취소 신청", "취소신청",
    "교환 신청", "교환신청",
    "반품 신청", "반품신청",
    "변경 신청", "변경신청",
})

def _is_order_fastpath(user_message: str) -> bool:
    msg = user_message.strip().lower()
    for p in _ORDER_FASTPATH_PATTERNS:
        pattern = r'(?<![가-힣])' + re.sub(r'\s+', r'\\s*', re.escape(p)) + r'(?![가-힣])'
        if re.search(pattern, msg):
            return True
    return False
```

패턴 앞뒤에 한글 완성형 글자(AC00–D7A3)가 없어야 매칭됩니다 — 부분 단어 오매칭 방지.  
패턴 내부 공백은 `\s*`로 처리하여 "취소 해줘"처럼 띄어쓴 경우도 매칭됩니다.

| 입력 | 결과 | 이유 |
|------|------|------|
| "이 주문 취소해줘" | True | 패턴 앞 공백·뒤 종료 |
| "교환 신청" | True | 패턴 정확 일치 |
| "교환신청서 작성방법" | **False** | 패턴 뒤 한글 '서' 연속 |
| "취소해줘야 하나요?" | **False** | 패턴 뒤 한글 '야' 연속 |
| "반품신청방법 알려줘" | **False** | 패턴 뒤 한글 '방' 연속 |
| "취소 방법 알려줘" | False | 패턴 없음 → LLM 판단 |

`_fast_route()`는 정책·방법 문의를 먼저 CS로 돌린 뒤, 주문 키워드와 실행 의지가 가까이 붙은 경우만 OrderGraph로 보냅니다.

---

## CS 단독 호출 pass-through 최적화

CS 에이전트가 하나만 호출된 경우, Supervisor LLM의 재합성 호출을 생략하고 CS 결과를 즉시 반환합니다.  
**이유**: 대부분의 요청이 CS 단독 호출이므로 LLM 호출 1회 절감.

```python
# 단일 CS 호출 → Supervisor 재합성 없이 바로 반환
if len(valid) == 1:
    _, (tc, cs_result, latency_ms) = valid[0]
    return AgentResult(answer=_parse_answer(cs_result.answer), ...)
```

---

## 진행 중 플로우 처리 (`_has_pending_order_flow`)

OrderGraph가 `interrupt` 상태에서 대기 중일 때, 사용자의 다음 메시지는  
Supervisor LLM 판단 없이 즉시 OrderGraph로 전달됩니다.

```python
snapshot = await order_graph.aget_state(config)
return bool(snapshot.next)   # next: 재개를 기다리는 노드 이름 목록
```

**이 체크가 없으면**: Supervisor LLM이 "주문 취소 사유를 입력하세요" 같은 OrderGraph 질문을  
`call_cs_agent`로 잘못 분기하거나 요약해버립니다.

---

## 의도 불일치 감지 (`intent_mismatch`)

진행 중인 플로우와 새 요청의 의도(cancel/exchange)가 다를 때 기존 플로우를 폐기하고 신규 시작합니다.

```python
new_action = _detect_order_action(query)  # 키워드 점수 기반
pending_action = snapshot.values.get("action") if snapshot.next else None
intent_mismatch = pending_action is not None and pending_action != new_action
```

**없으면**: 취소 플로우 진행 중 "교환하고 싶어"를 입력하면 취소 플로우가 그대로 재개됩니다.

---

## Supervisor LLM 역할 범위

Supervisor LLM은 **에이전트 선택만** 담당합니다.

**CS 에이전트를 쓰는 경우:**
- 상품 재고·가격·보관법·제철
- 교환·환불 정책 안내 (실제 접수가 아닌 정책 설명)
- 배송 현황 조회, FAQ

**OrderGraph를 쓰는 경우:**
- 주문 취소 접수 (실제 처리)
- 주문 교환·반품 접수 (실제 처리)
- 주문 변경 접수 (배송지·연락처·배송 요청사항 등)
- 상품 불량·하자 신고 (벌레, 이물질, 부패, 파손, 오배송 등)
- 반드시 로그인 사용자에게만

**비로그인 사용자의 교환/취소:**
→ `call_cs_agent`로 정책만 안내 (실제 접수 불가)

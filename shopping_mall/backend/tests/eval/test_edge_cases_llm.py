"""
7개 경계 케이스 — 실제 Supervisor LLM 라우팅 테스트
동일한 시스템 프롬프트 + 도구 정의를 사용해 LLM이 어떤 도구를 선택하는지 확인합니다.

실행:
  cd shopping_mall/backend
  uv run python tests/eval/test_edge_cases_llm.py
"""
import json
import os
import sys
import time

import requests

# ── 환경 변수 로드 ─────────────────────────────────────────────────────────────
# .env에서 직접 읽거나 환경변수로 주입
LITELLM_URL   = os.getenv("LITELLM_URL",   "https://litellm.lilpa.moe/v1")
LITELLM_KEY   = os.getenv("LITELLM_API_KEY", "")
LITELLM_MODEL = os.getenv("LITELLM_MODEL", "gpt-5-nano")

if not LITELLM_KEY:
    print("ERROR: LITELLM_API_KEY is not set. Export it before running this eval script.", file=sys.stderr)
    sys.exit(1)

# ── Supervisor 시스템 프롬프트 (실제 prompts.py와 동일) ───────────────────────
SUPERVISOR_INPUT_PROMPT = """당신은 FarmOS 마켓 고객 지원 오케스트레이터입니다.
사용자의 문의를 분석하여 적절한 서브 에이전트를 호출하세요.
반드시 도구(서브 에이전트)를 통해 처리하고, 직접 답변을 생성하지 마세요.

## 에이전트 선택 기준

### call_cs_agent — 다음 경우에 사용하세요
- 상품 재고, 가격, 상세 정보 조회
- 농산물 보관법, 제철 정보
- 교환·환불·배송 **정책** 안내 (실제 접수가 아닌 정책 설명)
- FAQ 답변 (배송 기간, 결제 수단 등 일반 운영 질문)
- FarmOS 플랫폼·농장 정보
- 로그인 사용자의 **배송 현황 조회** ("내 배송 어디야?" 등)
- 비로그인 사용자의 교환/취소 관련 문의 → 정책만 안내
- 비로그인 사용자의 배송·주문 현황 문의 → call_cs_agent로 라우팅 (CS가 자동 안내)

### call_order_agent — 다음 경우에 사용하세요
- 주문 **취소** 접수 (실제 처리)
- 주문 **교환·반품** 접수 (실제 처리)
- **상품 불량·하자 신고** (벌레, 이물질, 부패, 파손, 오배송 등) — 로그인 사용자라면 정책 안내 없이 바로 교환·반품 접수로 연결
- 반드시 **로그인한 사용자(user_id 있음)**에게만 사용합니다.
- 비로그인 사용자의 교환/취소 요청 → call_cs_agent로 정책 안내
- 비로그인 사용자의 상품 불량 신고 → call_cs_agent로 품질 보증 정책 안내

## 복합 문의 처리
여러 도메인에 걸친 문의는 해당 에이전트를 모두 호출하세요.
결과가 독립적인 경우에만 동시에 호출하고, 이전 결과가 다음 입력으로 필요한 경우 순차 호출하세요.

예) "딸기 재고 있어? 그리고 내 배송 언제 와?"
  → call_cs_agent("딸기 재고 조회") + call_cs_agent("배송 현황 조회") 동시 호출 가능

## 교환/취소 진행 중 처리
call_order_agent가 반환한 경우, 별도 판단 없이 해당 응답을 그대로 전달하세요.

## 이전 질문에 대한 긍정 응답 처리
사용자가 "네", "응", "알려줘", "부탁해" 등으로 짧게 답할 경우,
이전 대화에서 제시했던 구체적 질문을 파악하고 그에 맞는 서브 에이전트에 구체적 요청으로 라우팅하세요.
예) "해당 상품의 상세 정보를 더 알고 싶으신가요?" → "네" → call_cs_agent("상품 상세 정보 안내")

## 현재 메시지 우선
항상 사용자의 **현재 메시지**에 응답하세요.
이전 턴에서 오류나 에스컬레이션이 있었더라도, 사용자가 현재 메시지에서 다시 언급하지 않으면 재처리하지 마세요.
"안녕", "하이", "반갑습니다" 등 인사말은 이전 맥락과 무관하게 새 대화 시작으로 처리하세요.
"""

# ── 도구 정의 (executor.py Pydantic 모델과 동일) ──────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_cs_agent",
            "description": (
                "CS 에이전트에게 조회·안내 질문을 위임합니다. "
                "상품 재고/가격, 보관법, 제철 정보, 정책 안내, FAQ, 배송 현황 조회 등에 사용합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "CS 에이전트에게 전달할 질문"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_order_agent",
            "description": (
                "주문 취소·교환·반품 접수를 Order 에이전트에게 위임합니다. "
                "반드시 로그인한 사용자에게만 사용합니다. 정책 안내가 아닌 실제 접수 처리입니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Order 에이전트에게 전달할 내용"}
                },
                "required": ["query"],
            },
        },
    },
]

# ── 7개 경계 케이스 ────────────────────────────────────────────────────────────
EDGE_CASES = [
    {"id": 44, "msg": "취소 접수 부탁드립니다",            "real_label": "order", "reason": "'부탁드립니다' 동사 미등록"},
    {"id": 45, "msg": "교환으로 처리 부탁드려요",           "real_label": "order", "reason": "'부탁드려요' 동사 미등록"},
    {"id": 46, "msg": "반품 요청합니다",                    "real_label": "order", "reason": "'요청합니다' 동사 미등록"},
    {"id": 47, "msg": "환불받고 싶습니다",                   "real_label": "order", "reason": "'받고 싶습니다' 미등록"},
    {"id": 48, "msg": "이 주문 취소 좀 해주실 수 있을까요?", "real_label": "order", "reason": "'해주실' ≠ '해주세요'"},
    {"id": 49, "msg": "반품 좀 하고 싶은데요",              "real_label": "order", "reason": "'하고 싶은데요' ≠ '하고 싶어'"},
    {"id": 50, "msg": "취소 처리 도와주세요",               "real_label": "order", "reason": "'도와주세요' 미등록"},
]


def call_supervisor_llm(user_message: str, is_logged_in: bool = False) -> dict:
    """실제 LiteLLM 프록시에 Supervisor 도구 선택 요청."""
    from datetime import datetime
    login_status = "로그인" if is_logged_in else "비로그인"
    now = datetime.now()
    context_suffix = (
        f"\n\n## 현재 요청 컨텍스트\n"
        f"- 날짜/시각: {now.strftime('%Y-%m-%d')} {now.strftime('%H:%M')}\n"
        f"- 사용자 상태: {login_status}\n"
    )
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LITELLM_MODEL,
        "messages": [
            {"role": "system", "content": SUPERVISOR_INPUT_PROMPT + context_suffix},
            {"role": "user",   "content": user_message},
        ],
        "tools": TOOLS,
        "tool_choice": "required",   # 반드시 도구를 선택하도록 강제
        "temperature": 0,
    }

    t0 = time.monotonic()
    resp = requests.post(
        f"{LITELLM_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]
    tool_calls = choice["message"].get("tool_calls", [])
    if tool_calls:
        tool_name = tool_calls[0]["function"]["name"]
        tool_args = json.loads(tool_calls[0]["function"]["arguments"])
    else:
        tool_name = "(no tool)"
        tool_args = {}

    return {
        "tool":       tool_name,
        "args":       tool_args,
        "latency_ms": latency_ms,
    }


def main():
    print("=" * 70)
    print("Supervisor LLM 경계 케이스 실제 라우팅 테스트")
    print(f"모델: {LITELLM_MODEL}  /  URL: {LITELLM_URL}")
    print("=" * 70)

    results = []
    correct = 0

    for case in EDGE_CASES:
        print(f"\n[Case {case['id']}] \"{case['msg']}\"")
        print(f"  실제 정답: {case['real_label']}  |  규칙 미매칭 이유: {case['reason']}")

        try:
            result = call_supervisor_llm(case["msg"], is_logged_in=True)
        except Exception as e:
            print(f"  ❌ API 오류: {e}")
            results.append({**case, "llm_tool": "ERROR", "latency_ms": 0, "correct": False})
            continue

        tool = result["tool"]
        expected_tool = "call_order_agent" if case["real_label"] == "order" else "call_cs_agent"
        is_correct = tool == expected_tool

        if is_correct:
            correct += 1
            mark = "✅"
        else:
            mark = "❌"

        print(f"  {mark} LLM 선택: {tool}  ({result['latency_ms']}ms)")
        if result["args"]:
            print(f"     query = \"{result['args'].get('query', '')}\"")

        results.append({
            **case,
            "llm_tool":   tool,
            "latency_ms": result["latency_ms"],
            "correct":    is_correct,
        })

    # ── 요약 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"정확도: {correct}/{len(EDGE_CASES)} ({correct/len(EDGE_CASES)*100:.1f}%)")
    print("=" * 70)
    print(f"{'ID':>4}  {'메시지':<34}  {'LLM 선택':>18}  {'정답':>5}  결과")
    print("-" * 70)
    for r in results:
        mark = "✅" if r["correct"] else "❌"
        print(f"{r['id']:>4}  {r['msg']:<34}  {r.get('llm_tool','ERROR'):>18}  {r['real_label']:>5}  {mark}")

    # ── JSON 저장 ──────────────────────────────────────────────────────────────
    output = {
        "model":    LITELLM_MODEL,
        "total":    len(EDGE_CASES),
        "correct":  correct,
        "accuracy": round(correct / len(EDGE_CASES) * 100, 1),
        "cases":    results,
    }
    out_path = "tests/eval/edge_case_llm_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()

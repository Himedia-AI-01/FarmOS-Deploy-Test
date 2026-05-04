"""
단일 에이전트 LLM vs Supervisor(다중 에이전트) 라우팅 정확도 비교
=====================================================================
동일한 50개 케이스를 두 가지 방식으로 처리해 LLM 수준에서 비교합니다.

- 단일 에이전트: CS 시스템 프롬프트 + 9개 도구 (cancel_order 포함)
  → "cs" 정답: CS 도구(search_faq/policy/products 등) 선택 여부
  → "order" 정답: cancel_order 또는 process_refund 선택 여부

- Supervisor: Supervisor 시스템 프롬프트 + 2개 도구
  → "cs" 정답: call_cs_agent
  → "order" 정답: call_order_agent

실행:
  cd shopping_mall/backend
  uv run python tests/eval/test_single_vs_multi_agent.py
"""
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── 환경 변수 ──────────────────────────────────────────────────────────────────
LITELLM_URL   = os.getenv("LITELLM_URL",   "https://litellm.lilpa.moe/v1")
LITELLM_KEY   = os.getenv("LITELLM_API_KEY", "")
LITELLM_MODEL = os.getenv("LITELLM_MODEL", "gpt-5-nano")

if not LITELLM_KEY:
    logging.error("LITELLM_API_KEY is not set. Export it before running this eval script.")
    sys.exit(1)

# ── 로그인 컨텍스트 suffix (두 에이전트 공통) ──────────────────────────────────
def _ctx_suffix() -> str:
    now = datetime.now()
    return (
        f"\n\n## 현재 요청 컨텍스트\n"
        f"- 날짜/시각: {now.strftime('%Y-%m-%d')} {now.strftime('%H:%M')}\n"
        f"- 사용자 상태: 로그인\n"
    )

# ════════════════════════════════════════════════════════════════════════════════
# 단일 에이전트 설정
# ════════════════════════════════════════════════════════════════════════════════

SINGLE_AGENT_PROMPT = """당신은 FarmOS 마켓 CS 에이전트입니다.
사용자의 문의를 분석하고 적절한 도구를 호출하세요.

## 도구 선택 기준

- **CS 안내** (상품/정책/FAQ/배송 조회): search_faq, search_policy, get_order_status, search_products, get_product_detail
- **이상·민원 에스컬레이션**: escalate_to_agent
- **거절 필요**: refuse_request
- **주문 취소 실행** (실제 처리 요청): cancel_order
- **환불 처리 실행** (취소 후 환불): process_refund

## 주의
- 정책·방법·규정 등 **안내 문의**는 search_policy 또는 search_faq를 호출하세요. cancel_order/process_refund는 실제 접수 실행에만 사용합니다.
- "취소 가능한가요?", "반품 방법이 뭐야?" 같은 가능 여부·방법 문의는 search_policy로 처리하세요.
- 반드시 로그인한 사용자에게만 cancel_order/process_refund를 사용하세요.
"""

# 단일 에이전트 9개 도구 정의
SINGLE_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_faq",
            "description": (
                "통합 FAQ 지식베이스에서 질문의 답변을 검색합니다. "
                "배송 기간, 결제·적립금, 교환·반품, 농산물 보관법, 제철 정보, "
                "FarmOS 플랫폼·농장 소개 등 고객 서비스 전반에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "subcategory": {"type": "string", "nullable": True},
                    "top_k": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": (
                "운영 정책 문서에서 관련 내용을 검색합니다. "
                "반품·교환·환불, 결제·적립금, 회원 등급, 배송 정책, "
                "상품 품질 보증, 고객 서비스 운영 규정 등에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "policy_type": {
                        "type": "string",
                        "enum": ["return", "payment", "membership", "delivery", "quality", "service"],
                    },
                },
                "required": ["query", "policy_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": (
                "현재 로그인한 사용자의 주문·배송 현황을 실시간으로 조회합니다. "
                "'내 주문 어디 있어요?', '송장번호 알려줘' 등에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "nullable": True},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "상품을 이름이나 카테고리로 검색하고 재고 상태를 확인합니다. "
                "'딸기 있어요?', '과일 뭐 있어?', '재고 있는 상품만 보여줘' 등에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "check_stock": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_detail",
            "description": "특정 상품의 상세 정보(가격, 재고, 설명, 평점 등)를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "nullable": True},
                    "product_name": {"type": "string", "nullable": True},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_agent",
            "description": "챗봇이 처리할 수 없는 케이스를 상담원에게 연결합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "urgency": {"type": "string", "default": "normal"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refuse_request",
            "description": "처리할 수 없거나 허용되지 않는 요청을 정중히 거절합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": (
                "주문을 직접 취소 처리합니다. "
                "정책 안내가 아닌 실제 취소 접수 실행에만 사용하세요. "
                "반드시 로그인한 사용자에게만 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "reason": {"type": "string", "default": "단순 변심"},
                    "refund_method": {"type": "string", "enum": ["원결제 수단", "포인트"], "default": "원결제 수단"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_refund",
            "description": (
                "취소된 주문의 환불 방법을 확정하고 환불을 처리합니다. "
                "반드시 로그인한 사용자에게만 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "refund_method": {"type": "string", "enum": ["원결제 수단", "포인트"]},
                },
                "required": ["order_id", "refund_method"],
            },
        },
    },
]

# 단일 에이전트 "order" 라벨에서 정답으로 인정하는 도구
SINGLE_ORDER_TOOLS = {"cancel_order", "process_refund"}
# 단일 에이전트 "cs" 라벨에서 정답으로 인정하는 도구 (= 나머지 전부)
SINGLE_CS_TOOLS = {
    "search_faq", "search_policy", "get_order_status",
    "search_products", "get_product_detail",
    "escalate_to_agent", "refuse_request",
}


# ════════════════════════════════════════════════════════════════════════════════
# Supervisor 설정 (기존 test_edge_cases_llm.py와 동일)
# ════════════════════════════════════════════════════════════════════════════════

SUPERVISOR_PROMPT = """당신은 FarmOS 마켓 고객 지원 오케스트레이터입니다.
사용자의 문의를 분석하여 적절한 서브 에이전트를 호출하세요.
반드시 도구(서브 에이전트)를 통해 처리하고, 직접 답변을 생성하지 마세요.

## 에이전트 선택 기준

### call_cs_agent — 다음 경우에 사용하세요
- 상품 재고, 가격, 상세 정보 조회
- 농산물 보관법, 제철 정보
- 교환·환불·배송 **정책** 안내 (실제 접수가 아닌 정책 설명)
- FAQ 답변 (배송 기간, 결제 수단 등 일반 운영 질문)
- FarmOS 플랫폼·농장 정보
- 로그인 사용자의 **배송 현황 조회**
- 비로그인 사용자의 교환/취소 관련 문의 → 정책만 안내

### call_order_agent — 다음 경우에 사용하세요
- 주문 **취소** 접수 (실제 처리)
- 주문 **교환·반품** 접수 (실제 처리)
- **상품 불량·하자 신고** — 로그인 사용자라면 바로 교환·반품 접수로 연결
- 반드시 **로그인한 사용자(user_id 있음)**에게만 사용합니다.
"""

SUPERVISOR_TOOLS = [
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
                "properties": {"query": {"type": "string"}},
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
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


# ════════════════════════════════════════════════════════════════════════════════
# LLM 호출
# ════════════════════════════════════════════════════════════════════════════════

def call_llm(system_prompt: str, user_message: str, tools: list) -> dict:
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LITELLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt + _ctx_suffix()},
            {"role": "user",   "content": user_message},
        ],
        "tools": tools,
        "tool_choice": "required",
        "temperature": 0,
    }

    t0 = time.monotonic()
    resp = requests.post(f"{LITELLM_URL}/chat/completions", headers=headers, json=payload, timeout=30)
    latency_ms = int((time.monotonic() - t0) * 1000)
    resp.raise_for_status()

    data = resp.json()
    tool_calls = data["choices"][0]["message"].get("tool_calls", [])
    tool_name = tool_calls[0]["function"]["name"] if tool_calls else "(no tool)"
    return {"tool": tool_name, "latency_ms": latency_ms}


def is_correct_single(tool_name: str, label: str) -> bool:
    if label == "order":
        return tool_name in SINGLE_ORDER_TOOLS
    else:  # cs
        return tool_name in SINGLE_CS_TOOLS


def is_correct_supervisor(tool_name: str, label: str) -> bool:
    return (label == "order" and tool_name == "call_order_agent") or \
           (label == "cs"    and tool_name == "call_cs_agent")


# ════════════════════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════════════════════

def main():
    # eval_dataset.json 로드
    dataset_path = Path(__file__).parent / "eval_dataset.json"
    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)["routing"]

    print("=" * 72)
    print("단일 에이전트 LLM vs Supervisor 라우팅 정확도 비교")
    print(f"모델: {LITELLM_MODEL}  |  케이스: {len(dataset)}개")
    print("=" * 72)

    results = []
    sa_correct = 0
    sv_correct = 0

    for case in dataset:
        msg     = case["message"]
        label   = case["label"]
        group   = case["group"]
        case_id = case["id"]

        print(f"\n[{case_id:>2}] \"{msg}\"  (label={label}, group={group})")

        # 단일 에이전트
        try:
            sa = call_llm(SINGLE_AGENT_PROMPT, msg, SINGLE_AGENT_TOOLS)
            sa_ok = is_correct_single(sa["tool"], label)
            if sa_ok:
                sa_correct += 1
            print(f"     단일 에이전트: {sa['tool']:>20} {'✅' if sa_ok else '❌'} ({sa['latency_ms']}ms)")
        except Exception as e:
            sa = {"tool": "ERROR", "latency_ms": 0}
            sa_ok = False
            print(f"     단일 에이전트: API 오류 — {e}")

        # Supervisor
        try:
            sv = call_llm(SUPERVISOR_PROMPT, msg, SUPERVISOR_TOOLS)
            sv_ok = is_correct_supervisor(sv["tool"], label)
            if sv_ok:
                sv_correct += 1
            print(f"     Supervisor   : {sv['tool']:>20} {'✅' if sv_ok else '❌'} ({sv['latency_ms']}ms)")
        except Exception as e:
            sv = {"tool": "ERROR", "latency_ms": 0}
            sv_ok = False
            print(f"     Supervisor   : API 오류 — {e}")

        results.append({
            "id":       case_id,
            "message":  msg,
            "label":    label,
            "group":    group,
            "sa_tool":  sa["tool"],
            "sa_ok":    sa_ok,
            "sv_tool":  sv["tool"],
            "sv_ok":    sv_ok,
        })

    n = len(dataset)
    print("\n" + "=" * 72)
    print(f"단일 에이전트: {sa_correct}/{n}  ({sa_correct/n*100:.1f}%)")
    print(f"Supervisor   : {sv_correct}/{n}  ({sv_correct/n*100:.1f}%)")
    print(f"차이         : {sv_correct - sa_correct:+d}건  ({(sv_correct-sa_correct)/n*100:+.1f}%p)")
    print("=" * 72)

    # 그룹별 분석
    groups = {}
    for r in results:
        g = r["group"]
        if g not in groups:
            groups[g] = {"total": 0, "sa_ok": 0, "sv_ok": 0}
        groups[g]["total"] += 1
        groups[g]["sa_ok"] += int(r["sa_ok"])
        groups[g]["sv_ok"] += int(r["sv_ok"])

    print(f"\n{'그룹':<22} {'N':>4}  {'단일 에이전트':>12}  {'Supervisor':>10}  {'차이':>6}")
    print("-" * 60)
    for g, v in groups.items():
        t = v["total"]
        sa_pct = v["sa_ok"] / t * 100
        sv_pct = v["sv_ok"] / t * 100
        diff = sv_pct - sa_pct
        print(f"{g:<22} {t:>4}  {v['sa_ok']}/{t} ({sa_pct:4.0f}%)  {v['sv_ok']}/{t} ({sv_pct:4.0f}%)  {diff:+6.0f}%p")

    # 오판 케이스 출력
    print("\n── 단일 에이전트 오판 케이스 ──────────────────────────────────────")
    for r in results:
        if not r["sa_ok"]:
            print(f"  [{r['id']:>2}] \"{r['message']}\"  → {r['sa_tool']} (정답: {r['label']})")

    print("\n── Supervisor 오판 케이스 ──────────────────────────────────────────")
    sv_errors = [r for r in results if not r["sv_ok"]]
    if sv_errors:
        for r in sv_errors:
            print(f"  [{r['id']:>2}] \"{r['message']}\"  → {r['sv_tool']} (정답: {r['label']})")
    else:
        print("  없음")

    # JSON 저장
    output = {
        "model":   LITELLM_MODEL,
        "total":   n,
        "single_agent": {"correct": sa_correct, "accuracy": round(sa_correct/n*100, 1)},
        "supervisor":   {"correct": sv_correct, "accuracy": round(sv_correct/n*100, 1)},
        "diff_pp": round((sv_correct - sa_correct) / n * 100, 1),
        "groups":  groups,
        "cases":   results,
    }
    out_path = Path(__file__).parent / "single_vs_multi_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()

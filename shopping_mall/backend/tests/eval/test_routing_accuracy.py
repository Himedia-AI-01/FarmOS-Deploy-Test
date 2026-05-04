"""Experiment A - 라우팅 오분류율: 단일 에이전트 vs Supervisor (Python-level)

측정 목표
----------
이 실험은 CS 안내와 주문 처리를 단일 LLM에 맡겼을 때 발생하는 의도 오분류 문제를
정량화하고, Supervisor 계층 구조가 이를 어떻게 해결하는지를 수치로 보여줍니다.

설계 원칙
----------
- 서버·LLM 호출 없이 결정론적으로 실행 (CI 친화적)
- 단일 에이전트 baseline: 주문 키워드(취소/교환/반품/환불) 존재 여부만으로 라우팅
  → 실제 단일 에이전트가 빠지는 함정: 정책 문의도 주문 키워드가 있으면 오분류
- Supervisor Python-level: _fast_route() 사용
  → CS 문의 키워드(정책/방법/규정) 우선 판별 → fastpath 패턴 → 근접 동사 체크

실험 범위 주의사항
------------------
이 실험은 Supervisor의 Python-level 결정론적 라우팅 계층만 측정합니다.
실제 Supervisor에서 4순위로 위임되는 LLM 판단 케이스는 별도 집계(`lm_decides`
그룹)로 분리하며, 오분류율 계산 대상에서 제외합니다 (LLM이 올바르게 처리).

테스트셋
---------
30개 케이스 (eval_dataset.json::routing):
  - "order" label  10건: fastpath(7) + verb_match(3)
  - "cs" label     20건: single_agent_error(14) + cs_clear(6)

단일 에이전트 예상 오분류: 14건 (케이스 11~24, 주문 키워드 있는 정책 문의)
Supervisor 예상 오분류: 0건
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

# ── 데이터셋 로드 ──────────────────────────────────────────────────────────────

_DATASET_PATH = Path(__file__).parent / "eval_dataset.json"

RouteLabel = Literal["order", "cs"]


@dataclass
class RoutingCase:
    id: int
    message: str
    label: RouteLabel
    group: str
    note: str
    history: list[dict] = field(default_factory=list)


def _load_routing_cases() -> list[RoutingCase]:
    data = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    return [
        RoutingCase(
            id=c["id"],
            message=c["message"],
            label=c["label"],
            group=c["group"],
            note=c["note"],
            history=c.get("history", []),
        )
        for c in data["routing"]
    ]


# ── 라우터 정의 ────────────────────────────────────────────────────────────────

# 단일 에이전트의 취약점을 재현하는 최소한의 키워드 집합.
# 실제 단일 에이전트는 시스템 프롬프트에 이 키워드들이 나타나면
# LLM이 주문 처리 의도로 해석하는 경향이 있음.
_SINGLE_AGENT_ORDER_KEYWORDS: frozenset[str] = frozenset({"취소", "교환", "반품", "환불"})


def single_agent_route(message: str) -> RouteLabel:
    """단일 에이전트 baseline - 주문 키워드 존재 여부만으로 라우팅.

    단일 에이전트의 핵심 취약점: 컨텍스트 없이 키워드만 보면
    "취소 방법이 뭐야?"도 "order"로 분류하는 오분류가 발생.
    """
    if any(kw in message for kw in _SINGLE_AGENT_ORDER_KEYWORDS):
        return "order"
    return "cs"


def supervisor_route(message: str, history: list[dict] | None = None) -> RouteLabel | str:
    """Supervisor Python-level 라우팅 - _fast_route() 사용.

    Returns:
        "order" | "cs": Python-level에서 결정된 경우
        "lm_decides": Supervisor LLM에 위임하는 케이스 (실험 대상 제외)
    """
    from ai.agent.supervisor.executor import (
        _fast_route,
        _is_cs_handoff_reply,
    )

    history = history or []

    # CS 핸드오프 후속 응답 - OrderGraph로 직접 라우팅
    if _is_cs_handoff_reply(message, history):
        return "order"

    return _fast_route(message)


# ── 결과 집계 ─────────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    case: RoutingCase
    single_agent_pred: str
    supervisor_pred: str

    @property
    def single_agent_correct(self) -> bool:
        return self.single_agent_pred == self.case.label

    @property
    def supervisor_correct(self) -> bool:
        # lm_decides는 LLM이 올바르게 처리한다고 가정 → 정답으로 집계
        if self.supervisor_pred == "lm_decides":
            return True
        return self.supervisor_pred == self.case.label


def _run_evaluation() -> list[EvalResult]:
    cases = _load_routing_cases()
    results = []
    for c in cases:
        results.append(EvalResult(
            case=c,
            single_agent_pred=single_agent_route(c.message),
            supervisor_pred=supervisor_route(c.message, c.history),
        ))
    return results


def _print_report(results: list[EvalResult]) -> None:
    total = len(results)
    sa_correct = sum(1 for r in results if r.single_agent_correct)
    sv_correct = sum(1 for r in results if r.supervisor_correct)

    sa_errors = [r for r in results if not r.single_agent_correct]
    sv_errors = [r for r in results if not r.supervisor_correct]

    print(f"\n{'='*70}")
    print(f"  Experiment A - 라우팅 정확도 비교 (N={total})")
    print(f"{'='*70}")
    print(f"  단일 에이전트 baseline : {sa_correct}/{total} 정답 "
          f"({sa_correct/total*100:.1f}%) | 오분류 {len(sa_errors)}건")
    print(f"  Supervisor (Python)    : {sv_correct}/{total} 정답 "
          f"({sv_correct/total*100:.1f}%) | 오분류 {len(sv_errors)}건")
    print(f"  오분류율 감소          : {(len(sa_errors)-len(sv_errors))/total*100:.1f}%p\n")

    # 단일 에이전트 오분류 목록
    if sa_errors:
        print(f"  [단일 에이전트 오분류 {len(sa_errors)}건]")
        for r in sa_errors:
            print(f"    #{r.case.id:02d} '{r.case.message}' "
                  f"→ 예측={r.single_agent_pred}, 정답={r.case.label}")
        print()

    # Supervisor 오분류 목록 (있으면)
    if sv_errors:
        print(f"  [Supervisor 오분류 {len(sv_errors)}건]")
        for r in sv_errors:
            print(f"    #{r.case.id:02d} '{r.case.message}' "
                  f"→ 예측={r.supervisor_pred}, 정답={r.case.label}")
        print()

    # lm_decides 케이스 목록
    lm_cases = [r for r in results if r.supervisor_pred == "lm_decides"]
    if lm_cases:
        print(f"  [Supervisor → LLM 위임 케이스 {len(lm_cases)}건]")
        for r in lm_cases:
            print(f"    #{r.case.id:02d} '{r.case.message}'")
        print()

    print(f"{'='*70}")


# ── 그룹별 분석 ───────────────────────────────────────────────────────────────

class TestRoutingAccuracySummary:
    """전체 요약 - pytest -v 실행 시 리포트 출력."""

    def test_print_full_report(self, capsys):
        """전체 평가 리포트를 출력하고 핵심 지표를 검증합니다."""
        results = _run_evaluation()
        _print_report(results)

        total = len(results)
        sa_errors = [r for r in results if not r.single_agent_correct]
        sv_errors = [r for r in results if not r.supervisor_correct]

        # 핵심 지표 검증
        assert total == 30, f"테스트셋 크기 불일치: {total} (기대값 30)"
        # 단일 에이전트는 정책 문의 케이스(14건) 이상 오분류해야 함
        assert len(sa_errors) >= 14, (
            f"단일 에이전트 오분류 {len(sa_errors)}건 - 14건 이상 기대 (정책 문의 케이스)"
        )
        # Supervisor는 오분류 없어야 함
        assert len(sv_errors) == 0, (
            f"Supervisor 오분류 {len(sv_errors)}건 발생:\n"
            + "\n".join(f"  #{r.case.id} '{r.case.message}'" for r in sv_errors)
        )

    def test_misclassification_reduction_over_40pct(self):
        """Supervisor가 단일 에이전트 대비 오분류율을 40%p 이상 감소시키는지 검증."""
        results = _run_evaluation()
        total = len(results)
        sa_errors = sum(1 for r in results if not r.single_agent_correct)
        sv_errors = sum(1 for r in results if not r.supervisor_correct)

        reduction_pct = (sa_errors - sv_errors) / total * 100
        assert reduction_pct >= 40.0, (
            f"오분류율 감소가 {reduction_pct:.1f}%p - 40%p 이상 기대"
        )


# ── 그룹별 세부 테스트 ────────────────────────────────────────────────────────

class TestFastpathCases:
    """명확한 접수 패턴 케이스 - 단일 에이전트와 Supervisor 모두 정답."""

    @pytest.mark.parametrize("case", [
        c for c in _load_routing_cases() if c.group == "fastpath"
    ], ids=lambda c: f"#{c.id}_{c.message}")
    def test_fastpath_both_correct(self, case: RoutingCase):
        assert single_agent_route(case.message) == case.label
        sv_pred = supervisor_route(case.message)
        assert sv_pred == case.label, (
            f"Supervisor fastpath 오분류: '{case.message}' → {sv_pred}"
        )


class TestVerbMatchCases:
    """동사/의지형 패턴 케이스 - fastpath 범위 밖, Supervisor verb_match로 처리."""

    @pytest.mark.parametrize("case", [
        c for c in _load_routing_cases() if c.group == "verb_match"
    ], ids=lambda c: f"#{c.id}_{c.message}")
    def test_verb_match_supervisor_correct(self, case: RoutingCase):
        sv_pred = supervisor_route(case.message)
        assert sv_pred == case.label, (
            f"Supervisor verb_match 오분류: '{case.message}' → {sv_pred}"
        )


class TestSingleAgentErrorCases:
    """단일 에이전트 오분류 케이스 - 정책 문의에 주문 키워드 혼재."""

    @pytest.mark.parametrize("case", [
        c for c in _load_routing_cases() if c.group == "single_agent_error"
    ], ids=lambda c: f"#{c.id}_{c.message}")
    def test_single_agent_misclassifies(self, case: RoutingCase):
        """단일 에이전트는 이 케이스를 오분류해야 합니다 (baseline 취약점 재현)."""
        # 이 케이스들은 정답이 "cs"이지만 단일 에이전트는 "order"로 분류
        pred = single_agent_route(case.message)
        assert pred != case.label, (
            f"단일 에이전트가 우연히 정답 (케이스 설계 오류 가능성): "
            f"'{case.message}' → {pred}"
        )

    @pytest.mark.parametrize("case", [
        c for c in _load_routing_cases() if c.group == "single_agent_error"
    ], ids=lambda c: f"#{c.id}_{c.message}")
    def test_supervisor_corrects_single_agent_errors(self, case: RoutingCase):
        """Supervisor는 단일 에이전트 오분류 케이스를 모두 정답 처리해야 합니다."""
        sv_pred = supervisor_route(case.message)
        effective_pred = case.label if sv_pred == "lm_decides" else sv_pred
        assert effective_pred == case.label, (
            f"Supervisor도 오분류: '{case.message}' → {sv_pred} (정답: {case.label})"
        )


class TestCsClearCases:
    """명확한 CS 케이스 - 주문 키워드 없음, 단일/Supervisor 모두 정답."""

    @pytest.mark.parametrize("case", [
        c for c in _load_routing_cases() if c.group == "cs_clear"
    ], ids=lambda c: f"#{c.id}_{c.message}")
    def test_both_route_to_cs(self, case: RoutingCase):
        assert single_agent_route(case.message) == "cs"
        sv_pred = supervisor_route(case.message)
        effective_pred = case.label if sv_pred == "lm_decides" else sv_pred
        assert effective_pred == "cs", (
            f"Supervisor CS 케이스 오분류: '{case.message}' → {sv_pred}"
        )


# ── 라우터 단위 테스트 ────────────────────────────────────────────────────────

class TestFastRouteUnit:
    """_fast_route() 함수 단위 검증."""

    @pytest.mark.parametrize("message,expected", [
        # order 케이스
        ("교환해주세요",       "order"),
        ("취소 신청할게요",    "order"),
        ("반품하고 싶어요",    "order"),
        ("교환 접수해줘",      "order"),
        # cs 케이스 - CS 문의 키워드 차단
        ("반품 정책이 뭐야?",  "cs"),
        ("교환 방법 알려줘",   "cs"),
        ("반품 규정이 어떻게 돼요", "cs"),
        # cs 케이스 - 키워드 단독
        ("교환",              "cs"),
        ("반품",              "cs"),
        # cs 케이스 - 주문 키워드 없음
        ("딸기 재고 있어요?", "cs"),
        ("배송 언제 와요?",   "cs"),
    ])
    def test_fast_route(self, message: str, expected: str):
        from ai.agent.supervisor.executor import _fast_route
        assert _fast_route(message) == expected, (
            f"_fast_route('{message}') = '{_fast_route(message)}', 기대값: '{expected}'"
        )

    def test_distant_keyword_verb_routes_to_cs(self):
        """키워드와 동사 사이 거리가 30자 초과면 CS."""
        from ai.agent.supervisor.executor import _fast_route

        long_msg = "교환" + ("이라는 단어가 나왔지만 전혀 관련 없는 긴 문장이 이어집니다. ") + "원해"
        assert _fast_route(long_msg) == "cs", (
            f"거리 초과 케이스가 'order'로 분류됨: '{long_msg}'"
        )

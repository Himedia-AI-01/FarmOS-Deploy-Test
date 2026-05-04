"""
CS 응답 품질 검증 — 핸드오프 형식 / 불렛 제한 / 정책 출처 인용
================================================================
챗봇 서버에 실제 요청을 보내 CS 에이전트 출력 품질을 측정합니다.

검증 항목:
  1. 반품 신청 핸드오프  — "반품 신청하고 싶어요" → 선택지 형식 반환
  2. 불렛 항목 수 제한  — 정책 응답의 불렛(- 항목) 수 ≤ 5
  3. 정책 출처 인용     — search_policy 기반 응답에 (근거: ...) 포함

실행 (서버 포트 4000이 실행 중이어야 합니다):
  cd shopping_mall/backend
  uv run python tests/eval/test_cs_response_quality.py
  uv run python tests/eval/test_cs_response_quality.py --verbose
"""
import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────────────

BASE_URL   = "http://localhost:4000"
COOKIE_PATH = Path(__file__).parents[4] / "cookie.txt"   # FarmOS/cookie.txt
TIMEOUT_SEC = 180

# ── 품질 케이스 정의 ──────────────────────────────────────────────────────────

@dataclass
class QualityCase:
    id: int
    query: str
    description: str
    checks: list[str]  # 적용할 검증 목록: "handoff" | "bullet_limit" | "citation"
    session_id: int | None = None  # None = 비로그인 게스트


QUALITY_CASES: list[QualityCase] = [
    # ── Fix 2: 반품 핸드오프 ──────────────────────────────────────────────────
    QualityCase(
        id=1,
        query="반품 신청하고 싶어요",
        description="비로그인 사용자 반품 신청 의사 → 선택지 형식 (정책 나열 금지)",
        checks=["handoff"],
    ),
    QualityCase(
        id=2,
        query="교환 신청하고 싶은데요",
        description="비로그인 사용자 교환 신청 의사 → 선택지 형식",
        checks=["handoff"],
    ),
    # ── Fix 3: 불렛 제한 ──────────────────────────────────────────────────────
    QualityCase(
        id=3,
        query="배송 기간에 관한 정책이 어떻게 되나요?",
        description="배송 정책 조회 → 불렛 ≤ 5개 + 인용 포함",
        checks=["bullet_limit", "citation"],
    ),
    QualityCase(
        id=4,
        query="반품할 때 배송비는 누가 내나요?",
        description="반품 정책 조회 → 불렛 ≤ 5개 + 인용 포함",
        checks=["bullet_limit", "citation"],
    ),
    # ── Citation: 정책 출처 인용 ──────────────────────────────────────────────
    QualityCase(
        id=5,
        query="상품 품질 보증 정책이 어떻게 되나요?",
        description="품질 보증 정책 조회 → (근거: ...) 인용",
        checks=["citation"],
    ),
    QualityCase(
        id=6,
        query="결제 정책 규정상 지원되는 결제 수단과 제한 사항이 궁금해요",
        description="결제 정책 조회 → (근거: ...) 인용",
        checks=["citation"],
    ),
]

# ── 검증 로직 ──────────────────────────────────────────────────────────────────

# 핸드오프 형식 — 어느 하나라도 있으면 OK (LLM이 어순을 바꾸는 경우 허용)
_HANDOFF_MARKER_PATTERNS: list[re.Pattern] = [
    re.compile(r"교환과\s*반품[··]?환불\s*중\s*원하시는"),
    re.compile(r"반품[··]?환불\s*중\s*원하시는\s*처리"),
    re.compile(r"반품과\s*교환\s*중\s*원하시"),
    re.compile(r"원하시는\s*처리\s*방법을?\s*(알려|선택)"),  # "원하시는 처리 방법을 알려/선택"
    re.compile(r"다음\s*중\s*원하시는"),                       # "다음 중 원하시는"
    re.compile(r"처리\s*방법을?\s*(선택|알려)"),               # "처리 방법을 선택"
]
# 선택지 번호 — 1. / 1) 둘 다 허용
_HANDOFF_OPT_1 = re.compile(r"1[.)]\s*교환")
_HANDOFF_OPT_2 = re.compile(r"2[.)]\s*반품")

# 불렛 항목 패턴 (- 로 시작하는 줄)
_BULLET_RE = re.compile(r"^-\s+", re.MULTILINE)

# 정책 인용 패턴 (근거: 로 시작하는 괄호)
_CITATION_RE = re.compile(r"\(근거\s*:", re.IGNORECASE)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_handoff(answer: str) -> CheckResult:
    """핸드오프 선택지 형식이 응답에 포함됐는지 확인.

    - 안내 문구: _HANDOFF_MARKER_PATTERNS 중 하나 이상 매칭
    - 선택지: '1. 교환' 또는 '1) 교환' + '2. 반품·환불' 또는 '2) 반품'
    LLM이 어순을 약간 바꾸거나 괄호 스타일이 달라도 통과.
    """
    has_marker = any(p.search(answer) for p in _HANDOFF_MARKER_PATTERNS)
    has_opt1   = bool(_HANDOFF_OPT_1.search(answer))
    has_opt2   = bool(_HANDOFF_OPT_2.search(answer))
    passed = has_marker and has_opt1 and has_opt2

    if passed:
        detail = "선택지 형식 확인됨"
    else:
        missing = []
        if not has_marker: missing.append("핸드오프 안내 문구")
        if not has_opt1:   missing.append("1./1) 교환 선택지")
        if not has_opt2:   missing.append("2./2) 반품 선택지")
        detail = f"누락: {', '.join(missing)}"

    return CheckResult(name="handoff", passed=passed, detail=detail)


def check_bullet_limit(answer: str, max_bullets: int = 5) -> CheckResult:
    """응답 내 불렛(- 항목) 수가 max_bullets 이하인지 확인."""
    count = len(_BULLET_RE.findall(answer))
    passed = count <= max_bullets
    detail = f"불렛 {count}개 (허용 ≤ {max_bullets})"
    return CheckResult(name="bullet_limit", passed=passed, detail=detail)


def check_citation(answer: str) -> CheckResult:
    """(근거: ...) 형식의 인용이 응답에 포함됐는지 확인."""
    match = _CITATION_RE.search(answer)
    passed = bool(match)
    detail = "(근거: ...) 인용 확인됨" if passed else "(근거: ...) 없음"
    return CheckResult(name="citation", passed=passed, detail=detail)


CHECK_FUNCTIONS = {
    "handoff":      check_handoff,
    "bullet_limit": check_bullet_limit,
    "citation":     check_citation,
}

# ── API 호출 ──────────────────────────────────────────────────────────────────

def _build_opener() -> urllib.request.OpenerDirector:
    cj = http.cookiejar.MozillaCookieJar()
    if COOKIE_PATH.exists():
        cj.load(str(COOKIE_PATH), ignore_discard=True, ignore_expires=True)
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def call_chatbot(opener: urllib.request.OpenerDirector, query: str, session_id: int | None) -> tuple[str, float]:
    """챗봇 API 호출 → (answer, elapsed_sec)."""
    payload = json.dumps({
        "question": query,
        "session_id": session_id,
        "history": [],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/chatbot/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    with opener.open(req, timeout=TIMEOUT_SEC) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    elapsed = time.monotonic() - t0
    return body.get("answer", ""), elapsed

# ── 메인 ──────────────────────────────────────────────────────────────────────

def main(verbose: bool = False) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 68)
    print("CS 응답 품질 검증  (서버: localhost:4000)")
    print(f"케이스 수: {len(QUALITY_CASES)}")
    print("=" * 68)

    opener = _build_opener()

    total_checks = 0
    passed_checks = 0
    case_results: list[dict] = []

    for case in QUALITY_CASES:
        print(f"\n[Case {case.id}] {case.description}")
        print(f"  쿼리: \"{case.query}\"")

        try:
            answer, elapsed = call_chatbot(opener, case.query, case.session_id)
        except Exception as e:
            print(f"  ERROR: {e}")
            case_results.append({"id": case.id, "error": str(e), "checks": []})
            continue

        print(f"  응답 ({elapsed:.1f}s):\n    " + answer.replace("\n", "\n    "))

        check_results: list[CheckResult] = []
        for check_name in case.checks:
            fn = CHECK_FUNCTIONS[check_name]
            result = fn(answer)
            check_results.append(result)
            total_checks += 1
            if result.passed:
                passed_checks += 1
                mark = "✅"
            else:
                mark = "❌"
            print(f"  {mark} [{result.name}] {result.detail}")

        case_results.append({
            "id":      case.id,
            "query":   case.query,
            "answer":  answer,
            "elapsed": round(elapsed, 1),
            "checks":  [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in check_results
            ],
        })

    # ── 요약 ──────────────────────────────────────────────────────────────────
    pass_rate = (passed_checks / total_checks * 100) if total_checks else 0.0
    print("\n" + "=" * 68)
    print(f"검증 결과: {passed_checks}/{total_checks} 통과  ({pass_rate:.1f}%)")
    print("=" * 68)

    failed = [
        (r["id"], r["query"], c)
        for r in case_results
        for c in r.get("checks", [])
        if not c["passed"]
    ]
    if failed:
        print("\n── 실패 항목 ────────────────────────────────────────────────────")
        for cid, query, c in failed:
            print(f"  [Case {cid}] [{c['name']}] {c['detail']}")
            print(f"    쿼리: \"{query}\"")
    else:
        print("\n모든 검증 통과")

    # JSON 결과 저장
    out_path = Path(__file__).parent / "cs_quality_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_checks":  total_checks,
                "passed_checks": passed_checks,
                "pass_rate":     round(pass_rate, 1),
                "cases":         case_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n결과 저장: {out_path}")

    if total_checks == 0:
        print("검증 가능한 응답이 없어 실패로 처리합니다.")
        sys.exit(1)

    sys.exit(0 if passed_checks == total_checks else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CS 응답 품질 검증")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    args = parser.parse_args()
    main(verbose=args.verbose)

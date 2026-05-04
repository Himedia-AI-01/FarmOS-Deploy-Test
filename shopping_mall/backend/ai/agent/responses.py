"""사전 정의 응답 (Canned Responses) — LLM을 거치지 않고 즉시 반환되는 고정 문자열.

## 목적
- **일관성**: LLM 매 호출마다 달라지는 표현을 방지하고 동일한 문구를 보장합니다.
- **관리 용이성**: 응답 문구를 수정할 때 이 파일 하나만 편집합니다.
- **성능**: LLM 재호출 없이 즉시 반환합니다.

## 사용 지점
- `executor.py`: LOGIN_REQUIRED, REFUSED, 시스템 오류 메시지
- `supervisor/executor.py`: LOGIN_REQUIRED, 시스템 오류 메시지
- `cs_tools.py`: ESCALATION_*, LOGIN_REQUIRED
"""

# ── 인증 관련 ─────────────────────────────────────────────────────────────────

LOGIN_REQUIRED = (
    "해당 기능은 로그인 후 이용 가능합니다. "
    "로그인하신 뒤 다시 시도해 주세요."
)

# ── 거절 — __REFUSED__ 마커 감지 시 LLM 재호출 없이 즉시 반환 ───────────────
#
# refuse_request 도구가 `__REFUSED__\n사유: <code>` 마커를 반환하면,
# executor.py가 이 문자열을 즉시 반환합니다 (reason 코드에 관계없이 동일).

REFUSED = "죄송합니다. 해당 요청은 처리할 수 없습니다. 다른 도움이 필요하신가요?"

REFUSED_INTERNAL_INFO = (
    "죄송합니다. 매출, 운영 통계, 내부 시스템 정보는 고객 상담 채널에서 안내해 드릴 수 없습니다. "
    "상품, 주문, 배송, 교환·반품 관련 도움이 필요하시면 말씀해 주세요."
)

REFUSED_OTHER_USER_INFO = (
    "죄송합니다. 다른 고객님의 주문, 배송, 연락처 등 개인정보는 안내해 드릴 수 없습니다. "
    "본인 주문 확인이 필요하시면 로그인 후 주문 내역이나 배송 조회를 이용해 주세요."
)

REFUSED_JAILBREAK = (
    "죄송합니다. 시스템 규칙을 우회하거나 내부 지침을 변경하는 요청은 처리할 수 없습니다. "
    "상품, 주문, 배송, 교환·반품 관련 도움이 필요하시면 말씀해 주세요."
)

REFUSED_OUT_OF_SCOPE = (
    "죄송합니다. 해당 요청은 FarmOS 마켓 고객 상담 범위에서 도와드리기 어렵습니다. "
    "상품, 주문, 배송, 교환·반품 관련 문의를 도와드릴 수 있습니다."
)

REFUSED_INAPPROPRIATE = (
    "죄송합니다. 해당 표현이나 요청은 고객 상담 채널에서 처리하기 어렵습니다. "
    "도움이 필요한 내용을 차분히 말씀해 주시면 확인해 드리겠습니다."
)


def refusal_response(reason: str | None) -> str:
    """거절 사유별 고객 안내 문구."""
    if reason == "other_user_info":
        return REFUSED_OTHER_USER_INFO
    if reason == "internal_info":
        return REFUSED_INTERNAL_INFO
    if reason == "jailbreak":
        return REFUSED_JAILBREAK
    if reason == "out_of_scope":
        return REFUSED_OUT_OF_SCOPE
    if reason == "inappropriate":
        return REFUSED_INAPPROPRIATE
    return REFUSED

# ── 에스컬레이션 (escalate_to_agent 도구) ─────────────────────────────────────

ESCALATION_HIGH_URGENCY = (
    "우선 처리 요청으로 접수되었습니다. "
    "상담원이 최대한 빠르게 연결될 예정입니다. "
    "고객센터 직통 번호: 1588-0000"
)

ESCALATION_NORMAL = (
    "상담원 연결을 요청하셨습니다. "
    "잠시만 기다려 주시면 담당 상담원이 연결됩니다. "
    "운영시간: 평일 오전 9시 ~ 오후 6시 / 고객센터: 1588-0000"
)

# ── 시스템 / 오류 ─────────────────────────────────────────────────────────────

MAX_ITERATIONS_EXCEEDED = (
    "요청을 처리하는 데 시간이 걸리고 있습니다. "
    "상담원에게 연결해 드리겠습니다."
)

LLM_GENERATION_FAILED = "죄송합니다. 답변을 생성하지 못했습니다."

SERVICE_TEMPORARY_ERROR = "현재 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."

STOCK_QUERY_NEEDS_TARGET = (
    "재고를 확인할 상품명이나 카테고리를 알려주세요.\n"
    "예: 딸기, 사과, 과일, 채소"
)

# _parse_answer에서 응답이 MAX_ANSWER_LENGTH를 초과할 때 말미에 붙는 문구
TRUNCATION_SUFFIX = "\n\n(이어지는 내용은 상담원에게 문의해 주세요.)"

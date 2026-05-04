"""FAQ 갭 분석 서비스.

사용자 질문 중 FAQ로 커버되지 않은 것(갭)을 찾아
등록 추천 후보 1·2·3위를 산출합니다.

선정 로직
----------
1. 작업 intent(cancel, greeting, refusal)는 제외
2. 인삿말·단순 반응(_TRIVIAL_RE)은 intent 분류와 무관하게 텍스트 패턴으로 추가 제외
3. FaqCitation이 없는 ChatLog → '갭 질문'으로 분류
3. 각 질문에 normalize_query 적용 후 정규화된 텍스트 기준으로 그룹핑
   (같은 질문을 다른 표현으로 물어본 경우도 동의어 치환으로 묶임)
4. 스코어 = (최근 7일 수 × 1.5 + 이전 수) × (1 + 에스컬레이션 비율)
5. 스코어 내림차순 상위 limit개 반환
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── 작업 intent — FAQ 후보에서 항상 제외 ────────────────────────────────────────
# cancel  : 주문취소·환불 처리 (작업 요청)
# greeting: 인사 (정보 질문 아님)
# refusal : 거절됨 (유효한 FAQ 주제 아님)
TASK_INTENTS: frozenset[str] = frozenset({"cancel", "greeting", "refusal"})

# ── 인삿말·단순 반응 패턴 — FAQ 후보에서 항상 제외 ──────────────────────────────
# intent 분류가 "greeting"이 아닌 경우(예: "other")에도 텍스트 자체로 걸러냅니다.
# 방어적 이중 필터: TASK_INTENTS(intent 기반) + _TRIVIAL_RE(텍스트 기반)
_TRIVIAL_RE = re.compile(
    r"^(?:"
    r"안녕(?:하세요|하십니까|하슈|요)?|"
    r"hi|hello|하이|헬로|"
    r"감사(?:합니다|해요|드려요|드립니다)?|고마(?:워요|워)?|고맙(?:습니다|다)?|"
    r"네|아니(?:오|요|에요)?|응|맞아요|그래요|알겠(?:어요|습니다)?|"
    r"잘\s*있어요?|bye|바이|잘\s*가요|"
    r"반가(?:워요|워)|반갑(?:습니다|다)?"
    r")[\s!.?~]*$",
    re.IGNORECASE,
)


def _is_trivial(text: str) -> bool:
    """FAQ 주제로 적합하지 않은 단순 인사·반응 메시지인지 확인합니다."""
    return bool(_TRIVIAL_RE.match(text.strip()))

# intent → 관리자용 한글 레이블 (라우터와 동일)
_INTENT_LABEL: dict[str, str] = {
    "delivery": "배송·조회",
    "faq": "자주 묻는 질문",
    "stock": "상품·재고",
    "cancel": "취소·환불",
    "escalation": "처리 불가",
    "policy": "정책·약관",
    "refusal": "거절됨",
    "other": "기타",
    "greeting": "인사",
}


@dataclass
class GapCluster:
    """정규화된 질문 텍스트 하나에 대응하는 갭 클러스터."""
    normalized_key: str
    representative_question: str       # 가장 최근 원문
    count: int = 0
    recent_count: int = 0              # 최근 7일 수
    escalated_count: int = 0
    top_intent: str = "other"
    _intent_counter: dict[str, int] = field(default_factory=dict, repr=False)

    def add(self, question: str, intent: str, escalated: bool, is_recent: bool) -> None:
        self.count += 1
        if is_recent:
            self.recent_count += 1
        if escalated:
            self.escalated_count += 1
        self._intent_counter[intent] = self._intent_counter.get(intent, 0) + 1
        self.top_intent = max(self._intent_counter, key=lambda k: self._intent_counter[k])

    @property
    def escalation_rate(self) -> float:
        return self.escalated_count / self.count if self.count else 0.0

    @property
    def gap_type(self) -> str:
        """에스컬레이션이 절반 이상이면 처리불가, 아니면 누락FAQ."""
        return "escalated" if self.escalation_rate >= 0.5 else "missing"

    @property
    def score(self) -> float:
        older = self.count - self.recent_count
        return (self.recent_count * 1.5 + older) * (1.0 + self.escalation_rate)


@dataclass
class RecommendationItem:
    rank: int
    representative_question: str
    normalized_key: str
    count: int
    recent_count: int
    escalated_count: int
    gap_type: str       # "missing" | "escalated"
    score: float
    top_intent: str
    top_intent_label: str


@dataclass
class GapAnalysisResult:
    period_days: int
    total_gap_questions: int
    items: list[RecommendationItem]


def analyze(
    db: Session,
    *,
    days: int = 30,
    limit: int = 3,
    min_count: int = 2,
) -> GapAnalysisResult:
    """FAQ 갭 분석을 실행하고 추천 후보를 반환합니다.

    Args:
        db: SQLAlchemy 동기 세션
        days: 집계 기간 (일)
        limit: 반환할 추천 수
        min_count: 최소 질문 수 — 이 값 미만인 클러스터는 제외
                   (1회성 질문을 추천에서 거름)
    """
    # normalize_query는 임베딩 모델을 로드하지 않으므로 임포트 안전
    from ai.rag import normalize_query
    from app.core.datetime_utils import now_kst
    from app.models.chat_log import ChatLog
    from app.models.faq_citation import FaqCitation

    now = now_kst()
    since_n_days = now - _dt.timedelta(days=days)
    since_7_days = now - _dt.timedelta(days=7)

    # ── 1. 기간 내 인용된 chat_log_id 집합 ───────────────────────────────────
    cited_log_ids: set[int] = {
        row[0]
        for row in db.query(FaqCitation.chat_log_id)
        .filter(FaqCitation.chat_log_id.isnot(None))
        .distinct()
        .all()
        if row[0] is not None
    }

    # ── 2. 갭 질문 조회: 비작업 intent + 미인용 ──────────────────────────────
    raw_logs = (
        db.query(ChatLog)
        .filter(
            ChatLog.created_at >= since_n_days,
            ChatLog.intent.notin_(list(TASK_INTENTS)),
        )
        .order_by(ChatLog.created_at.desc())
        .all()
    )
    # DB 필터 외에 Python 레벨에서도 이중 방어
    # (notin_ 캐시 or 값 불일치 등으로 slip-through 방지)
    # _is_trivial: intent 분류와 무관하게 인삿말·단순 반응을 텍스트 패턴으로 추가 제거
    gap_logs = [
        log for log in raw_logs
        if log.id not in cited_log_ids
        and log.intent not in TASK_INTENTS
        and not _is_trivial(log.question)
    ]

    if not gap_logs:
        return GapAnalysisResult(period_days=days, total_gap_questions=0, items=[])

    # ── 3. normalize_query 기반 텍스트 클러스터링 ────────────────────────────
    # 정규화된 텍스트가 같으면 같은 질문으로 간주
    clusters: dict[str, GapCluster] = {}

    for log in gap_logs:
        try:
            key = normalize_query(log.question)
        except Exception:
            logger.warning("[faq_gap_analyzer] normalize 실패, 원문 사용 (chat_log_id=%s)", log.id)
            key = log.question.strip()

        if key not in clusters:
            clusters[key] = GapCluster(
                normalized_key=key,
                representative_question=log.question,  # desc 정렬이므로 처음 만나는 게 최신
            )

        clusters[key].add(
            question=log.question,
            intent=log.intent,
            escalated=bool(log.escalated),
            is_recent=log.created_at >= since_7_days,
        )

    # ── 4. 스코어 정렬 + 최소 빈도 필터 ─────────────────────────────────────
    ranked = sorted(
        (c for c in clusters.values() if c.count >= min_count),
        key=lambda c: c.score,
        reverse=True,
    )[:limit]

    items = [
        RecommendationItem(
            rank=i + 1,
            representative_question=c.representative_question,
            normalized_key=c.normalized_key,
            count=c.count,
            recent_count=c.recent_count,
            escalated_count=c.escalated_count,
            gap_type=c.gap_type,
            score=round(c.score, 2),
            top_intent=c.top_intent,
            top_intent_label=_INTENT_LABEL.get(c.top_intent, c.top_intent),
        )
        for i, c in enumerate(ranked)
    ]

    return GapAnalysisResult(
        period_days=days,
        total_gap_questions=len(gap_logs),
        items=items,
    )

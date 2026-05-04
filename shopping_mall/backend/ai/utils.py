"""AI 레이어 공통 유틸리티."""
import logging
import re

logger = logging.getLogger(__name__)

# ── Kiwi 형태소 분석기 (지연 로딩) ───────────────────────────────────────────────
# kiwipiepy 미설치 시 기존 정규식 토크나이저로 graceful degradation.
_kiwi = None
_kiwi_available: bool | None = None   # None=미확인, True=사용가능, False=불가


def _get_kiwi():
    """Kiwi 인스턴스를 최초 호출 시 1회 로딩 후 캐싱."""
    global _kiwi, _kiwi_available
    if _kiwi_available is not None:
        return _kiwi
    try:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
        _kiwi_available = True
        logger.info("Kiwi 형태소 분석기 로드 완료 — BM25 토크나이저로 사용")
    except ImportError:
        _kiwi_available = False
        logger.warning(
            "kiwipiepy 미설치 — 정규식 토크나이저로 fallback. "
            "설치: pip install kiwipiepy"
        )
    return _kiwi


# ── 한국어 불용어 (Stopwords) ─────────────────────────────────────────────────
# 단독으로 의미를 갖지 못하는 형식어. BM25 IDF를 왜곡하는 고빈도 기능어를 제거.
_KO_STOPWORDS: frozenset[str] = frozenset({
    # 격조사·보조사 단독형 (접미사 규칙으로 미처리되는 단음절 잔여어)
    "이", "가", "을", "를", "은", "는", "에", "로", "도", "만",
    "와", "과", "랑", "의", "서",
    # 부정·양태 부사
    "안", "못", "더",
    # 의존명사 (단독 의미 없음)
    "수", "것", "등", "및",
    # 구어체 응답 어미 잔여
    "요", "죠", "네", "지", "야", "여", "며",
    # 빈도 높은 동사 어간 잔여 (어미 제거 후 남는 1~2자 노이즈)
    "와요", "가요", "해요", "돼요",
})

# ── 동사/형용사 접미사 (longest match first) ───────────────────────────────────
# 하다 계열 복합어미, 피동·사동, 일반 활용형.
# "교환하고" → "교환", "반품할" → "반품", "됩니다" → (제거 후 최소길이 미달 → 무시)
_KO_VERB_SUFFIXES: tuple[str, ...] = (
    # ── 하다 계열 (긴 것부터) ─────────────────────────────
    "하고싶어요", "하고싶어", "하고싶다",
    "해주세요", "해줘요", "해줘",
    "하겠습니다", "했습니다", "합니다",
    "하였습니다", "하였어요",
    "할게요", "할래요", "할까요",
    "할게", "할래", "할까",
    "했어요", "했어", "했다",
    "하면서", "하더라도", "하지만",
    "하면", "하고", "하여", "하며", "하지", "하는", "하다",
    "해서", "해도", "해야", "해요",
    "한다", "한데", "한지",
    # ── 되다 계열 ─────────────────────────────────────────
    "됩니다", "됐어요", "되어요",
    "되면", "되고", "되어", "됐다",
    # ── 짧은 하다 활용형 (반드시 긴 것 뒤에 위치) ──────────
    "했", "할", "한", "된", "될",
    # ── 하세요/하십시오 계열 ───────────────────────────────
    "하십시오", "하세요", "하셔요",
    # ── 일반 어미 ─────────────────────────────────────────
    "이에요", "이었어요", "예요",
    "았어요", "었어요", "았어", "었어",
    "아요", "어요",
    "아서", "어서",
    "아도", "어도",
    "나요", "네요",
)

# ── 조사 (Josa) — 체언 뒤에 붙는 격조사·접속조사 ──────────────────────────────
_KO_JOSA: tuple[str, ...] = (
    # 복합 조사 (긴 것부터)
    "에서는", "에서도", "에서의",
    "으로는", "으로도", "으로의",
    "에서", "으로",
    "까지", "부터", "이랑", "이라", "이며", "이고", "이나",
    "이는", "이도", "이만",
    "는데", "은데",
    "와도", "과도", "와는", "과는",
    # 단순 조사
    "을", "를", "이", "가", "은", "는", "도", "만", "와", "과", "랑", "에", "로",
)

_MIN_TOKEN_LEN = 2  # 의미 단위 최소 음절 수


def _strip_ko(token: str) -> str:
    """동사 어미·조사를 제거해 어근을 반환합니다.

    1순위: 동사 활용형 접미사 (하다/되다 계열)
    2순위: 격조사·접속조사

    어근이 _MIN_TOKEN_LEN 미만이 되는 경우는 원형을 유지합니다.
    """
    for suf in _KO_VERB_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= _MIN_TOKEN_LEN:
            return token[: len(token) - len(suf)]

    for josa in _KO_JOSA:
        if token.endswith(josa) and len(token) - len(josa) >= _MIN_TOKEN_LEN:
            return token[: len(token) - len(josa)]

    return token


def _tokenize_ko_regex(text: str) -> list[str]:
    """정규식 기반 토크나이저 (Kiwi 미설치 시 fallback, 기존 v2 로직)."""
    raw_tokens = re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
    result: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        root = _strip_ko(tok)
        if root in _KO_STOPWORDS or len(root) < _MIN_TOKEN_LEN:
            continue
        if root not in seen:
            result.append(root)
            seen.add(root)
    return result if result else [text.lower()]


def _tokenize_ko_kiwi(text: str, kiwi) -> list[str]:
    """Kiwi 형태소 분석기 기반 토크나이저 (v3).

    추출 대상 품사:
      NN*  — 일반/고유/의존 명사  (반품, 신청, 기간, 상품)
      VV   — 동사 어간            (취소, 교환)
      VA   — 형용사 어간          (신선, 불량)
      XR   — 어근                 (복합어 내 의미 단위)
      SL   — 외래어/영문          (GAP, READY_TO_SHIP)

    복합명사 분리 예시:
      "반품신청기간" → ["반품", "신청", "기간"]
      "출고전취소" → ["출고", "전", "취소"]  (전: NNB 의존명사 → 필터)
    """
    _KIWI_KEEP_TAGS = frozenset({"NNG", "NNP", "NNB", "NR", "VV", "VA", "XR", "SL"})
    result: list[str] = []
    seen: set[str] = set()
    try:
        for token in kiwi.tokenize(text):
            if token.tag not in _KIWI_KEEP_TAGS:
                continue
            form = token.form.lower()
            if form in _KO_STOPWORDS or len(form) < _MIN_TOKEN_LEN:
                continue
            if form not in seen:
                result.append(form)
                seen.add(form)
        # 영문/숫자 토큰은 Kiwi가 SL/SW로 처리하지만 누락 가능 → 보완
        for tok in re.findall(r"[a-zA-Z0-9_]+", text):
            tok = tok.lower()
            if len(tok) >= 2 and tok not in seen:
                result.append(tok)
                seen.add(tok)
    except Exception as e:
        logger.warning("Kiwi 토크나이징 실패 (%s) — 정규식 fallback", e)
        return _tokenize_ko_regex(text)
    return result if result else _tokenize_ko_regex(text)


def tokenize_ko(text: str) -> list[str]:
    """한국어 BM25 토크나이저.

    Kiwi(kiwipiepy) 설치 시 형태소 분석 기반(v3)으로 동작:
      - 복합명사 분리: "반품신청기간" → ["반품", "신청", "기간"]
      - 품사 기반 필터: 명사·동사 어간·어근·외래어만 추출
    미설치 시 정규식 기반(v2)으로 graceful degradation.

    BM25 인덱스 빌드(seed_rag.py)와 쿼리 검색(hybrid_retrieve) 양쪽에서
    동일하게 호출되므로, 토크나이저 변경 후 반드시 BM25 인덱스를 재빌드해야 합니다.
    """
    kiwi = _get_kiwi()
    if kiwi is not None:
        return _tokenize_ko_kiwi(text, kiwi)
    return _tokenize_ko_regex(text)

"""AI 레이어 공통 유틸리티."""
import re


def tokenize_ko(text: str) -> list[str]:
    """한국어 정규식 토크나이저 — 한글/영문/숫자 단위로 분리.

    BM25 인덱스 빌드와 쿼리 양쪽에서 동일한 토크나이저를 사용해야
    검색 품질이 일관됩니다.
    """
    tokens = re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
    return tokens if tokens else [text.lower()]

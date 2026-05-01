"""날짜/시간 유틸리티 — 프로젝트 전체 KST 통일.

이 프로젝트는 한국 단일 시간대(KST, UTC+9)를 사용합니다.
DB 컬럼은 TIMESTAMP WITHOUT TIME ZONE이며, 저장되는 모든 값은 naive KST입니다.
"""
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 KST 시각을 naive datetime으로 반환합니다.

    DB(TIMESTAMP WITHOUT TIME ZONE)에 바로 저장할 수 있는 형태입니다.
    tzinfo가 없지만 값은 항상 KST 기준입니다.
    """
    return datetime.now(tz=_KST).replace(tzinfo=None)

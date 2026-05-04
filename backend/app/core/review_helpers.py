"""리뷰 분석 라우터/MCP tool 공유 헬퍼.

Design Ref: §11.1 — File Structure (review_helpers.py)
Plan SC: SC-06 (코어 단일 소스, 코드 중복 0)

기존 `api/review_analysis.py` 안에 있던 두 헬퍼를 분리해
FastAPI 라우터와 MCP tool 양쪽에서 동일하게 사용한다.
동작 변화 없음 — 함수 시그니처/로직은 라우터 원본과 동일.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession


async def get_seller_product_ids(
    db: AsyncSession,
    seller_id: str | None = None,
) -> list[int] | None:
    """판매자의 상품 ID 목록 조회 (멀티테넌트).

    현재 shop_stores 에 owner_id 컬럼이 없으므로 항상 None(전체 접근)을 반환한다.
    향후 owner_id 가 추가되면 아래 주석을 해제해 필터링을 활성화한다.

    Args:
        db: AsyncSession
        seller_id: 판매자 ID (None 이면 전체 접근)

    Returns:
        상품 ID 리스트 (None 이면 전체 접근)
    """
    if seller_id is None:
        return None

    # TODO: shop_stores 에 owner_id 추가 후 아래 코드 활성화
    # from sqlalchemy import text as sa_text
    # result = await db.execute(
    #     sa_text("""
    #         SELECT p.id FROM shop_products p
    #         JOIN shop_stores s ON p.store_id = s.id
    #         WHERE s.owner_id = :seller_id
    #     """),
    #     {"seller_id": seller_id},
    # )
    # product_ids = [row[0] for row in result.fetchall()]
    # return product_ids if product_ids else None

    return None  # 현재는 전체 접근


def stratified_sample(reviews: list[dict], sample_size: int) -> list[dict]:
    """rating 별 비례 층화 샘플링으로 대표성 있는 부분집합을 추출한다.

    전체 N 건 중 rating 분포를 유지하면서 sample_size 건만 추출한다.
    예: 전체에서 5점이 60%, 1점이 5% 면 샘플에서도 동일 비율.

    Args:
        reviews: ReviewRAG.get_all_reviews() 가 반환하는 dict 리스트
        sample_size: 샘플 목표 수

    Returns:
        sample_size 이하의 review dict 리스트 (원본 < sample_size 이면 원본 그대로)
    """
    if len(reviews) <= sample_size:
        return reviews

    # rating 별 그룹핑
    by_rating: dict[int, list[dict]] = {}
    for r in reviews:
        key = int(r.get("metadata", {}).get("rating", r.get("rating", 0)))
        by_rating.setdefault(key, []).append(r)

    sampled: list[dict] = []
    total = len(reviews)
    for group in by_rating.values():
        # 비례 배분 (최소 1건)
        n = max(1, round(len(group) / total * sample_size))
        sampled.extend(random.sample(group, min(n, len(group))))

    # 목표 수에 맞추기 (반올림 오차 보정)
    if len(sampled) > sample_size:
        sampled = random.sample(sampled, sample_size)
    elif len(sampled) < sample_size:
        # ID 기반 set lookup 으로 O(n) 보장 (dict equality 비교 회피)
        sampled_ids = {r["id"] for r in sampled}
        remaining = [r for r in reviews if r["id"] not in sampled_ids]
        extra = min(sample_size - len(sampled), len(remaining))
        sampled.extend(random.sample(remaining, extra))

    return sampled

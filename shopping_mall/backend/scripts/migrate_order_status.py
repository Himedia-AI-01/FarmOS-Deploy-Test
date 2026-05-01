"""Order.status 마이그레이션 스크립트.

구 status 값을 새 설계로 변환합니다.

변환 규칙:
  registered  + Shipment 없음  → preparing
  registered  + Shipment 있음  → shipped
  picked_up                    → shipped
  in_transit                   → shipped
  shipping                     → shipped
  paid                         → pending  (비표준값 정리)
  pending / delivered / cancelled → 변경 없음

사용법:
  # 변환 대상 미리 확인 (변경 없음)
  uv run python scripts/migrate_order_status.py --dry-run

  # 실제 마이그레이션 실행
  uv run python scripts/migrate_order_status.py
"""
import argparse
import logging
import sys
import os

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    engine = create_engine(settings.database_url)
    mode = "[DRY-RUN]" if dry_run else "[EXECUTE]"

    with engine.connect() as conn:
        # ── 현황 조회 ──────────────────────────────────────────────────────────
        result = conn.execute(text("""
            SELECT status, COUNT(*) AS cnt
            FROM shop_orders
            GROUP BY status
            ORDER BY cnt DESC
        """))
        rows = result.fetchall()
        logger.info("%s 현재 shop_orders status 분포:", mode)
        for status, cnt in rows:
            logger.info("  %-20s %d건", status, cnt)

        # ── 변환 규칙 적용 ─────────────────────────────────────────────────────

        migrations: list[tuple[str, str, str]] = []  # (설명, SQL, 파라미터용 SQL)

        # 1. registered + Shipment 없음 → preparing
        result = conn.execute(text("""
            SELECT COUNT(*) FROM shop_orders o
            WHERE o.status = 'registered'
              AND NOT EXISTS (
                  SELECT 1 FROM shop_shipments s WHERE s.order_id = o.id
              )
        """))
        cnt = result.scalar()
        logger.info("%s registered (no shipment) → preparing: %d건", mode, cnt)
        if cnt > 0:
            migrations.append((
                "registered (no shipment) → preparing",
                """UPDATE shop_orders SET status = 'preparing'
                   WHERE status = 'registered'
                     AND NOT EXISTS (
                         SELECT 1 FROM shop_shipments s WHERE s.order_id = shop_orders.id
                     )""",
            ))

        # 2. registered + Shipment 있음 → shipped
        result = conn.execute(text("""
            SELECT COUNT(*) FROM shop_orders o
            WHERE o.status = 'registered'
              AND EXISTS (
                  SELECT 1 FROM shop_shipments s WHERE s.order_id = o.id
              )
        """))
        cnt = result.scalar()
        logger.info("%s registered (has shipment) → shipped: %d건", mode, cnt)
        if cnt > 0:
            migrations.append((
                "registered (has shipment) → shipped",
                """UPDATE shop_orders SET status = 'shipped'
                   WHERE status = 'registered'
                     AND EXISTS (
                         SELECT 1 FROM shop_shipments s WHERE s.order_id = shop_orders.id
                     )""",
            ))

        # 3. picked_up / in_transit / shipping → shipped
        for old_status in ("picked_up", "in_transit", "shipping"):
            result = conn.execute(
                text("SELECT COUNT(*) FROM shop_orders WHERE status = :s"),
                {"s": old_status},
            )
            cnt = result.scalar()
            logger.info("%s %s → shipped: %d건", mode, old_status, cnt)
            if cnt > 0:
                migrations.append((
                    f"{old_status} → shipped",
                    f"UPDATE shop_orders SET status = 'shipped' WHERE status = '{old_status}'",
                ))

        # 4. paid → pending (비표준값 정리)
        result = conn.execute(text("SELECT COUNT(*) FROM shop_orders WHERE status = 'paid'"))
        cnt = result.scalar()
        logger.info("%s paid → pending: %d건", mode, cnt)
        if cnt > 0:
            migrations.append((
                "paid → pending",
                "UPDATE shop_orders SET status = 'pending' WHERE status = 'paid'",
            ))

        # ── 실행 ──────────────────────────────────────────────────────────────
        if not migrations:
            logger.info("%s 변환 대상 없음. 완료.", mode)
            return

        if dry_run:
            logger.info("[DRY-RUN] 위 변환을 실제로 적용하려면 --dry-run 없이 실행하세요.")
            return

        for description, sql in migrations:
            result = conn.execute(text(sql))
            logger.info("[EXECUTE] %s — %d행 업데이트", description, result.rowcount)

        conn.commit()
        logger.info("[EXECUTE] 마이그레이션 완료.")

        # 변환 후 분포 확인
        result = conn.execute(text("""
            SELECT status, COUNT(*) AS cnt
            FROM shop_orders
            GROUP BY status
            ORDER BY cnt DESC
        """))
        logger.info("[EXECUTE] 변환 후 status 분포:")
        for status, cnt in result.fetchall():
            logger.info("  %-20s %d건", status, cnt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Order.status 마이그레이션")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="변환 대상만 출력하고 실제 변경은 수행하지 않습니다.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)

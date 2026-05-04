#!/usr/bin/env bash
# Design Ref: §16.10 — farmos-ec2-deploy Module 4-B (v0.4)
# ApplicationStart 훅: docker-compose 기동.
set -euo pipefail
IFS=$'\n\t'

LOG() { echo "[application-start] $(date -Iseconds) $*"; }

cd /opt/farmos

# .env 에 IMAGE_TAG, GHCR_OWNER 포함 (Parameter Store 출처)
LOG "Loading .env"
set -a
# shellcheck disable=SC1091
source ./.env
set +a

# GHCR public 레포면 익명 풀이 가능. private 레포면 docker login ghcr.io 별도 필요 (M5 부트스트랩에서 처리).
LOG "Pulling images (IMAGE_TAG=${IMAGE_TAG:-latest})"
docker compose pull

# ────────────────────────────────────────────────
# Phase 1 — postgres 먼저 띄움 (api 컨테이너는 아직 안 시작)
#   이유: api lifespan 의 init_db() 와 seed 복원이 race condition 일으킴
#   → 빈 DB 면 seed 복원을 먼저 끝내고, 그 다음에 api 시작
# ────────────────────────────────────────────────
LOG "Phase 1: Starting postgres only"
docker compose up -d postgres

# ────────────────────────────────────────────────
# Seed gate — 첫 배포 또는 새 EC2 인스턴스일 때만 S3 dump 복원
#
# 동작:
#   1. postgres healthy 대기
#   2. rag_pesticide_products / shop_products 행 수 확인
#   3. 둘 다 임계값 이상이면 skip (이미 시드됨 — 매 배포마다 재시드 안 함)
#   4. 둘 중 하나라도 미만이면 SEED_DUMP_S3_URL 에서 dump fetch + pg_restore
#
# 환경변수 (SSM /farmos/prod/seed/dump_url 매핑):
#   SEED_DUMP_S3_URL — s3://bucket/key.dump 형식. 미설정 시 시드 스킵.
# ────────────────────────────────────────────────
LOG "Checking DB seed status..."

# postgres 컨테이너 healthy 대기 (최대 60초)
for i in $(seq 1 12); do
  if docker inspect -f '{{.State.Health.Status}}' farmos-postgres 2>/dev/null | grep -q healthy; then
    break
  fi
  sleep 5
done

PG_USER="${POSTGRES_USER:-farmos}"
PG_DB="${POSTGRES_DB:-farmos}"

count_table() {
  docker exec farmos-postgres psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT COUNT(*) FROM $1" 2>/dev/null | tr -d '[:space:]' || echo "0"
}

PESTICIDE_ROWS=$(count_table "rag_pesticide_products")
SHOP_PRODUCTS_ROWS=$(count_table "shop_products")

LOG "Seed status: rag_pesticide_products=${PESTICIDE_ROWS:-0}, shop_products=${SHOP_PRODUCTS_ROWS:-0}"

# 임계값: 농약 1000 이상 + 상품 10 이상이면 시드 완료로 간주
if [ "${PESTICIDE_ROWS:-0}" -ge 1000 ] && [ "${SHOP_PRODUCTS_ROWS:-0}" -ge 10 ]; then
  LOG "✓ DB already seeded — skipping restore"
elif [ -z "${SEED_DUMP_S3_URL:-}" ]; then
  LOG "⚠ DB needs seed but SEED_DUMP_S3_URL not set — manual seeding required"
  LOG "  Set SSM /farmos/prod/seed/dump_url then redeploy, or seed manually."
else
  LOG "→ DB needs initial seed — restoring from $SEED_DUMP_S3_URL"

  REGION="${AWS_DEFAULT_REGION:-$(curl -s -H "X-aws-ec2-metadata-token: $(curl -s -X PUT 'http://169.254.169.254/latest/api/token' -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" http://169.254.169.254/latest/meta-data/placement/region)}"

  TMP_DUMP=$(mktemp --suffix=.dump)
  if ! aws s3 cp "$SEED_DUMP_S3_URL" "$TMP_DUMP" --region "$REGION"; then
    LOG "ERROR: failed to download seed dump from $SEED_DUMP_S3_URL"
    rm -f "$TMP_DUMP"
    exit 1
  fi

  DUMP_SIZE=$(du -h "$TMP_DUMP" | cut -f1)
  LOG "Downloaded dump (~$DUMP_SIZE) — restoring to postgres container"

  docker cp "$TMP_DUMP" farmos-postgres:/tmp/seed.dump
  rm -f "$TMP_DUMP"

  # --clean --if-exists: 기존 테이블 drop 후 재생성 (빈 DB 가정이지만 안전)
  # --no-owner --no-privileges: 로컬 dump 의 user/role (postgres) 정보 무시 → EC2 의 farmos 사용자가 owner
  if docker exec farmos-postgres pg_restore \
       -U "$PG_USER" -d "$PG_DB" \
       --clean --if-exists --no-owner --no-privileges \
       /tmp/seed.dump 2>&1 | tail -20; then
    LOG "✓ Seed restore complete"
  else
    LOG "⚠ pg_restore returned non-zero — check logs above"
  fi

  docker exec farmos-postgres rm -f /tmp/seed.dump

  # 재확인
  PESTICIDE_ROWS=$(count_table "rag_pesticide_products")
  SHOP_PRODUCTS_ROWS=$(count_table "shop_products")
  LOG "Post-restore: rag_pesticide_products=${PESTICIDE_ROWS:-0}, shop_products=${SHOP_PRODUCTS_ROWS:-0}"
fi

# ────────────────────────────────────────────────
# Phase 2 — 나머지 서비스 시작 (api / shop-api / nginx)
#   이 시점에 DB 는 이미 시드 완료된 상태 → api 의 init_db() 는 이미 존재하는 테이블 확인 후 skip
# ────────────────────────────────────────────────
LOG "Phase 2: Starting remaining services (api, shop-api, nginx)"
docker compose up -d --remove-orphans

LOG "Pruning dangling images (older than 72h)"
docker image prune -f --filter "until=72h" || true

exit 0

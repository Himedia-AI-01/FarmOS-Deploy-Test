#!/usr/bin/env bash
# Design Ref: §16.8 — farmos-ec2-deploy Module 4-B (v0.4)
# BeforeInstall 훅: 디렉토리 준비 + 직전 IMAGE_TAG 백업 (롤백 보조용).
# 본격 자동 롤백은 CodeDeploy DeploymentGroup 의 Auto-Rollback 이 처리.
set -euo pipefail
IFS=$'\n\t'

LOG() { echo "[before-install] $(date -Iseconds) $*"; }

LOG "Ensuring /opt/farmos directories"
sudo mkdir -p /opt/farmos/data/postgres /opt/farmos/data/chroma /opt/farmos/data/hf-cache-shop \
              /opt/farmos/data/shop-logs \
              /opt/farmos/dist /opt/farmos/shop-dist /opt/farmos/release
sudo chown -R ubuntu:ubuntu /opt/farmos

# Postgres:18-alpine 은 uid 70 (postgres user) 으로 실행되므로
# /var/lib/postgresql 마운트 루트가 uid 70 소유여야 mkdir/init 가능.
# 위 recursive chown 으로 ubuntu:ubuntu 가 됐으니 명시적으로 되돌림.
sudo chown -R 70:70 /opt/farmos/data/postgres
LOG "  /opt/farmos/data/postgres chowned to 70:70 (postgres alpine uid)"

# 직전에 실행 중이던 farmos-api 이미지 태그를 .prev-tag 에 백업
PREV_IMAGE=$(docker inspect farmos-api --format '{{.Config.Image}}' 2>/dev/null || echo "")
if [ -n "$PREV_IMAGE" ]; then
  PREV_TAG="${PREV_IMAGE##*:}"
  echo "$PREV_TAG" > /opt/farmos/.prev-tag
  LOG "Saved previous IMAGE_TAG: $PREV_TAG"
else
  LOG "No previous farmos-api container (first deploy)"
fi

# ────────────────────────────────────────────────────────────────
# Pre-deploy disk cleanup (재발 방지)
# - 보존: 최신 sha-* 태그 2개 (현재 + 직전 → 롤백 안전망)
# - 청소: 그 외 sha-*, dangling, 빌드 캐시, 컨테이너 로그, journald
# - /opt/farmos/data/* 는 bind mount → prune 영향 없음
# - PREV_TAG 백업(위)이 끝난 뒤에 실행되어야 .prev-tag 안전 보장
# ────────────────────────────────────────────────────────────────
LOG "═══ Pre-deploy disk cleanup ═══"
LOG "Disk before: $(df -h / | awk 'NR==2{print $3" used / "$2" ("$5")"}')"

# 1) 정지된 컨테이너 + 사용 안 하는 네트워크
sudo docker container prune -f >/dev/null 2>&1 || true
sudo docker network prune -f   >/dev/null 2>&1 || true

# 2) repo 별 sha-* 태그를 최신순 정렬, 상위 2개만 보존하고 삭제
prune_old_tags() {
  local pattern="$1"
  sudo docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedAt}}' 2>/dev/null \
    | awk -v p="$pattern" '$1 ~ p && $1 ~ /:sha-/' \
    | sort -k3,4 -r \
    | tail -n +3 \
    | awk '{print $2}' \
    | xargs -r sudo docker rmi -f >/dev/null 2>&1 || true
}
prune_old_tags 'farmos-api'
prune_old_tags 'shopping-mall-api'

# 3) dangling 이미지 + 24시간 이전 빌드 캐시 (가장 큰 회수량)
sudo docker image prune -f >/dev/null 2>&1 || true
sudo docker builder prune -af --filter "until=24h" >/dev/null 2>&1 || true

# 4) 컨테이너 로그 (100MB 초과분만 truncate — 진행 중 로그는 안전)
sudo find /var/lib/docker/containers -name "*-json.log" -size +100M \
  -exec truncate -s 0 {} \; 2>/dev/null || true

# 5) journald 로그 (3일치만 보존)
sudo journalctl --vacuum-time=3d --quiet >/dev/null 2>&1 || true

# 6) APT 캐시 + 옛날 CodeDeploy archive (7일 이전)
sudo apt-get clean -y >/dev/null 2>&1 || true
sudo find /opt/codedeploy-agent/deployment-root -maxdepth 4 -type d \
  -name "deployment-archive" -mtime +7 -exec rm -rf {} + 2>/dev/null || true

LOG "Disk after:  $(df -h / | awk 'NR==2{print $3" used / "$2" ("$5")"}')"

# ─── 안전 가드: 청소 후에도 5GB 미만이면 배포 중단 → CodeDeploy 자동 롤백 ───
AVAIL_GB=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
if [ "${AVAIL_GB:-0}" -lt 5 ]; then
  LOG "❌ Insufficient disk after cleanup: ${AVAIL_GB}GB available — aborting"
  exit 1
fi
LOG "✓ Disk OK: ${AVAIL_GB}GB available"

exit 0

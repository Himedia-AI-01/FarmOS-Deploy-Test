#!/usr/bin/env bash
# Design Ref: §16.8 — farmos-ec2-deploy Module 4-B (v0.4)
# BeforeInstall 훅: 디렉토리 준비 + 직전 IMAGE_TAG 백업 (롤백 보조용).
# 본격 자동 롤백은 CodeDeploy DeploymentGroup 의 Auto-Rollback 이 처리.
set -euo pipefail
IFS=$'\n\t'

LOG() { echo "[before-install] $(date -Iseconds) $*"; }

LOG "Ensuring /opt/farmos directories"
sudo mkdir -p /opt/farmos/data/postgres /opt/farmos/data/chroma /opt/farmos/data/hf-cache-shop \
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

exit 0

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

LOG "Starting stack"
docker compose up -d --remove-orphans

LOG "Pruning dangling images (older than 72h)"
docker image prune -f --filter "until=72h" || true

exit 0

#!/usr/bin/env bash
# Design Ref: §16.7 — farmos-ec2-deploy Module 4-B (v0.4)
# ApplicationStop 훅: 직전 컨테이너 정지.
# 첫 배포 시 docker-compose.yml 미존재 → || true 로 무시.
set -uo pipefail
IFS=$'\n\t'

LOG() { echo "[application-stop] $(date -Iseconds) $*"; }

if [ -f /opt/farmos/docker-compose.yml ]; then
  LOG "Stopping existing stack"
  cd /opt/farmos
  docker compose stop || true
else
  LOG "First deploy — no docker-compose.yml yet, skipping stop"
fi

exit 0

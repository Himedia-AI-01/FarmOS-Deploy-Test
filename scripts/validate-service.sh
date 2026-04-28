#!/usr/bin/env bash
# Design Ref: §16.11 — farmos-ec2-deploy Module 4-B (v0.4)
# Plan SC: SC-2 (/health 200), 자동 롤백 ≤ 1분
#
# ValidateService 훅: 30회 × 2초 = 최대 60초 동안 /health 폴링.
# 실패 시 exit 1 → CodeDeploy DeploymentGroup 의 Auto-Rollback 트리거.
# 훅 timeout 90초 < 한도 3600 (R11 완화).
set -uo pipefail
IFS=$'\n\t'

LOG() { echo "[validate-service] $(date -Iseconds) $*"; }

for i in $(seq 1 30); do
  if curl -fs -o /dev/null http://localhost/health; then
    LOG "Healthy on attempt $i"
    exit 0
  fi
  sleep 2
done

LOG "ERROR: /health failed after 30 attempts (60 seconds)"
LOG "Last status:"
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost/health || true
docker ps --format 'table {{.Names}}\t{{.Status}}' || true
exit 1

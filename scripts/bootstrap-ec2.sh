#!/usr/bin/env bash
# Design Ref: §16.14 — farmos-ec2-deploy Module 5 (v0.4)
# Plan SC: SC-7 (IAM 최소권한), SC-9 (SSM→.env)
#
# 1회 실행: EC2 신규 인스턴스 부팅 후 다음 절차로 실행한다.
#   scp -i farmos-prod-1.pem scripts/bootstrap-ec2.sh ubuntu@<EIP>:~/
#   ssh -i farmos-prod-1.pem ubuntu@<EIP>
#   sudo bash ~/bootstrap-ec2.sh 2>&1 | tee bootstrap.log
#
# 본 스크립트는 idempotent (재실행해도 안전)하지만, 정상 동작 시 1회면 충분.
# 완료 후:
#   - newgrp docker (또는 ssh 재접속) 으로 docker 그룹 적용
#   - GH Actions 첫 배포 트리거 (git push origin dev)
#
# 검증 종료 코드:
#   0 = 모든 단계 성공
#   1 = 한 단계라도 실패 (set -e + 명시적 exit)
set -euo pipefail
IFS=$'\n\t'

LOG()   { echo "[bootstrap-ec2] $(date -Iseconds) $*"; }
ERROR() { echo "[bootstrap-ec2] $(date -Iseconds) ERROR: $*" >&2; }

REGION="${REGION:-ap-northeast-2}"
LOG "Region: $REGION"
LOG "User: $(whoami)"

# ────────────────────────────────────────────────
# 0) sudo 권한 확인 (root 이거나 sudo 가능 사용자)
# ────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
  ERROR "Need root (sudo bash bootstrap-ec2.sh) or passwordless sudo."
  exit 1
fi

# ────────────────────────────────────────────────
# 1) 시스템 업데이트
# ────────────────────────────────────────────────
LOG "1/9 apt-get update + upgrade"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" upgrade

# ────────────────────────────────────────────────
# 2) Docker 24+ 설치 (Ubuntu 24.04 공식 절차)
# ────────────────────────────────────────────────
LOG "2/9 Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get install -y ca-certificates curl gnupg lsb-release
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  LOG "  docker already present: $(docker --version)"
fi
sudo usermod -aG docker ubuntu
sudo systemctl enable --now docker

# ────────────────────────────────────────────────
# 3) AWS CodeDeploy agent 설치
# ────────────────────────────────────────────────
LOG "3/9 CodeDeploy agent"
if ! systemctl list-unit-files 2>/dev/null | grep -q '^codedeploy-agent\.service'; then
  sudo apt-get install -y ruby-full wget
  cd /home/ubuntu
  wget -q "https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install" -O /tmp/cda-install
  sudo chmod +x /tmp/cda-install
  sudo /tmp/cda-install auto
else
  LOG "  codedeploy-agent already installed"
fi
sudo systemctl enable --now codedeploy-agent

# ────────────────────────────────────────────────
# 4) AWS CLI v2 설치
# ────────────────────────────────────────────────
LOG "4/9 AWS CLI v2"
if ! command -v aws >/dev/null 2>&1 || ! aws --version 2>&1 | grep -q '^aws-cli/2'; then
  sudo apt-get install -y unzip
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
  unzip -q /tmp/awscliv2.zip -d /tmp
  sudo /tmp/aws/install --update
else
  LOG "  aws-cli v2 already present: $(aws --version)"
fi

# ────────────────────────────────────────────────
# 5) jq + rsync (after-install.sh 에서 사용)
# ────────────────────────────────────────────────
LOG "5/9 jq + rsync"
sudo apt-get install -y jq rsync

# ────────────────────────────────────────────────
# 6) UFW 방화벽 (22 + 80, 443 미사용 — CF Flexible)
# ────────────────────────────────────────────────
LOG "6/9 UFW firewall"
sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
yes | sudo ufw --force enable

# ────────────────────────────────────────────────
# 7) 2GB swap (sentence-transformers 모델 로드 안전 — t3.medium 4GB RAM)
# ────────────────────────────────────────────────
LOG "7/9 Swap (2GB)"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
else
  LOG "  /swapfile already present"
fi

# ────────────────────────────────────────────────
# 8) /opt/farmos working directory + journal log rotation + Docker log limit
# ────────────────────────────────────────────────
LOG "8/9 /opt/farmos + log rotation"
sudo mkdir -p /opt/farmos/data/postgres /opt/farmos/data/chroma /opt/farmos/dist /opt/farmos/release
sudo chown -R ubuntu:ubuntu /opt/farmos

sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/farmos.conf > /dev/null <<'EOF'
[Journal]
SystemMaxUse=200M
SystemKeepFree=500M
EOF
sudo systemctl restart systemd-journald

# Docker daemon log driver — 컨테이너 stdout JSON 로그가 무한 증가하지 않도록 회전
if [ ! -f /etc/docker/daemon.json ]; then
  sudo mkdir -p /etc/docker
  sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
EOF
  sudo systemctl restart docker
fi

# ────────────────────────────────────────────────
# 9) 검증 — Docker / CodeDeploy / AWS CLI / IAM Profile / SSM 28키
# ────────────────────────────────────────────────
LOG "9/9 Verification"

LOG "  docker --version:       $(docker --version)"
LOG "  docker compose version: $(docker compose version | head -n1)"
LOG "  codedeploy-agent:       $(sudo systemctl is-active codedeploy-agent)"
LOG "  aws --version:          $(aws --version)"

LOG "  --- IAM Instance Profile ---"
if aws sts get-caller-identity --region "$REGION" --output text --query 'Arn' 2>/dev/null; then
  LOG "  ✓ IAM Instance Profile attached"
else
  ERROR "  ✗ IAM Instance Profile not attached or AWS CLI cannot authenticate"
  ERROR "    EC2 콘솔 → Instance → Actions → Security → Modify IAM role → farmos-ec2-instance"
fi

LOG "  --- SSM Parameter Store /farmos/prod ---"
COUNT=$(aws ssm get-parameters-by-path \
  --path /farmos/prod --recursive \
  --region "$REGION" \
  --query "length(Parameters)" --output text 2>/dev/null || echo "0")
if [ "$COUNT" -ge 28 ]; then
  LOG "  ✓ SSM keys: $COUNT (>= 28)"
else
  ERROR "  ✗ SSM keys: $COUNT (expected 28)"
  ERROR "    M4-A Step 14 또는 IAM Inline Policy(ssm:GetParametersByPath / kms:Decrypt) 확인"
fi

LOG ""
LOG "============================================================"
LOG "Bootstrap complete. Instance ready for first CodeDeploy run."
LOG ""
LOG "Next steps:"
LOG "  1) exit  &&  ssh back in   (docker 그룹 적용)"
LOG "  2) From your laptop:  git push origin dev"
LOG "  3) Watch:  GH Actions → CodeDeploy console → /opt/farmos"
LOG "============================================================"

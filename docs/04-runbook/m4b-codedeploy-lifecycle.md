# M4-B Runbook — CodeDeploy Lifecycle 검증

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` §16.6 ~ §16.11 (v0.4)
> **Plan SC**: SC-2 (`/health` 200), SC-9 (SSM → .env), R10/R11/R13 완화
> **선행**: M4-A 완료 (캐치 테이블 #4 `arn:aws:iam::242201280878:role/farmos-codedeploy-svc` 필요 — DG 생성에 사용됨, 본 모듈은 코드 검증만 수행)
> **후행**: M4-C (GH Secrets + 첫 푸시)
> **분량**: 본 모듈은 콘솔 작업이 없는 **로컬 코드 검증** 위주. 사용자는 5~10분.

---

## §0. 사전 점검 (5분)

### 0.1 본 런북의 산출물

본 모듈은 다음 6개 파일이 이미 작성되어 있다고 가정합니다 (M4-B `/pdca do farmos-ec2-deploy --scope module-4b` 실행 결과).

| 파일 | 절대 경로 | 역할 | EOL |
|---|---|---|---|
| `appspec.yml` | `E:/new_my_study/FarmOS-Deploy-Test/appspec.yml` | CodeDeploy lifecycle 매니페스트 (v0.0) | LF |
| `application-stop.sh` | `E:/.../scripts/application-stop.sh` | ApplicationStop 훅 | LF |
| `before-install.sh` | `E:/.../scripts/before-install.sh` | BeforeInstall 훅 | LF |
| `after-install.sh` | `E:/.../scripts/after-install.sh` | AfterInstall 훅 (SSM → .env) | LF |
| `application-start.sh` | `E:/.../scripts/application-start.sh` | ApplicationStart 훅 | LF |
| `validate-service.sh` | `E:/.../scripts/validate-service.sh` | ValidateService 훅 (`/health` 폴링) | LF |

### 0.2 파일 존재 여부 확인 (한 줄)

```bash
# Bash (Git Bash / WSL / macOS / Linux)
cd E:/new_my_study/FarmOS-Deploy-Test
ls -la appspec.yml scripts/{application-stop,before-install,after-install,application-start,validate-service}.sh
```

**기대 출력**: 6 행 모두 `-rwxr-xr-x` (또는 git index 상 +x). 1행이라도 빠지면 M4-B 산출물 미완료.

### 0.3 EOL = LF 정책 (필수)

`appspec.yml` 과 `*.sh` 는 EC2 (Linux) 에서 실행되므로 **반드시 LF**. CRLF 면 첫 줄 `#!/usr/bin/env bash\r` 의 `\r` 때문에 `bad interpreter: No such file or directory` 에러가 발생합니다.

```bash
# .gitattributes 확인 (이미 작성됨)
cat .gitattributes
```

기대 라인:
```
*.sh         text eol=lf
appspec.yml  text eol=lf
```

EOL 검증:
```bash
# LF = 0a 만 보이면 OK. 0d 0a 가 보이면 CRLF (잘못됨).
file appspec.yml scripts/*.sh
# → 모두 "ASCII text" (LF) 이어야 함. "ASCII text, with CRLF line terminators" 이면 잘못됨.
```

### 0.4 +x 권한 비트 (Linux 실행 권한)

CodeDeploy agent 가 EC2 에서 lifecycle 훅을 실행하려면 `+x` 비트가 필요합니다. Windows checkout 시 chmod 가 무의미하지만, **git index 자체에 +x 비트가 박혀 있어야** 리눅스 체크아웃 시 자동으로 +x 가 됩니다.

```bash
# 5개 .sh 의 git index mode 확인
git ls-files --stage scripts/*.sh
```

**기대 출력 (5행 모두 100755)**:
```
100755 abc123... 0    scripts/after-install.sh
100755 def456... 0    scripts/application-start.sh
100755 789abc... 0    scripts/application-stop.sh
100755 def012... 0    scripts/before-install.sh
100755 345678... 0    scripts/validate-service.sh
```

만약 `100644` (실행 비트 없음) 이면 즉시 수정:
```bash
git update-index --chmod=+x scripts/application-stop.sh \
                            scripts/before-install.sh \
                            scripts/after-install.sh \
                            scripts/application-start.sh \
                            scripts/validate-service.sh
git status   # 변경 없으면 OK, "mode change 100644 → 100755" 표시되면 commit 필요
```

---

## §1. CodeDeploy Lifecycle 시퀀스 (전체 흐름)

### 1.1 시퀀스 다이어그램

```
GH Actions: deploy.yml
   │
   │ aws deploy create-deployment
   │   --revision s3://farmos-codedeploy-242201280878-ap-northeast-2/bundles/sha-xxx.zip
   ▼
┌────────────────────────────────────────────────────────────────────┐
│  CodeDeploy Service (us-east-1 backbone)                           │
│   - DeploymentGroup farmos-prod 가 가진 EC2 태그 셋 으로 인스턴스   │
│     찾기 (App=farmos AND Environment=prod)                         │
│   - 매치된 EC2 의 codedeploy-agent 데몬에게 작업 디스패치           │
└────────────────────────────────────────────────────────────────────┘
   │
   ▼
┌────────────────────────────────────────────────────────────────────┐
│  EC2: codedeploy-agent (systemd)                                   │
│  /opt/codedeploy-agent/deployment-root/<DG-id>/<Deploy-id>/        │
│   ├─ deployment-archive/   ← S3 zip 압축 해제                       │
│   ├─ logs/                                                         │
│   └─ deployment-archive/scripts/*.sh                               │
└────────────────────────────────────────────────────────────────────┘
   │
   │   (각 훅 timeout 초 내 exit 0 → 다음, 0 외 → 즉시 실패 → 자동 롤백)
   ▼

  Phase 1  ApplicationStop   ┌────────────┐  120s  application-stop.sh
                             │ 직전 컨테이너  │
                             │ docker stop  │
                             └──────┬─────┘
                                    ▼
  Phase 2  DownloadBundle    ┌────────────┐   ―   (CodeDeploy 내부, 사용자 코드 0)
                             │ S3 zip     │       deploy-bundle.zip → archive/
                             │ Download   │
                             └──────┬─────┘
                                    ▼
  Phase 3  BeforeInstall     ┌────────────┐  60s   before-install.sh
                             │ /opt/farmos │       mkdir + .prev-tag 백업
                             │ 디렉토리      │
                             └──────┬─────┘
                                    ▼
  Phase 4  Install           ┌────────────┐   ―    (CodeDeploy 내부)
                             │ source / → │        deployment-archive/* →
                             │ destination│        /opt/farmos/release/*
                             │  /opt/.../release│  (appspec 의 files: 블록)
                             └──────┬─────┘
                                    ▼
  Phase 5  AfterInstall      ┌────────────┐  120s  after-install.sh
                             │ release →   │       (1) rsync release → opt
                             │ /opt/farmos │       (2) IMDSv2 region 자동감지
                             │ + .env 생성  │       (3) SSM 28키 → .env
                             │ + 검증       │       (4) chmod 600
                             └──────┬─────┘       (5) 28키+6핵심변수 검증
                                    ▼
  Phase 6  ApplicationStart  ┌────────────┐  180s  application-start.sh
                             │ docker      │       compose pull + up -d
                             │ compose up  │       prune 72h+
                             └──────┬─────┘
                                    ▼
  Phase 7  ValidateService   ┌────────────┐  90s   validate-service.sh
                             │ /health 폴링 │       30회 × 2초 = 60초
                             │ (60초 한도)  │       실패 → exit 1 → 자동롤백
                             └──────┬─────┘
                                    ▼
                           Deployment Succeeded
```

### 1.2 훅 별 timeout 표 (`appspec.yml` v0.0)

| Hook | Script | Timeout | runas | 실패 시 |
|---|---|---|---|---|
| ApplicationStop | `scripts/application-stop.sh` | 120s | ubuntu | 자동 롤백 트리거 (단, 첫 배포 시 docker-compose.yml 미존재로 `\|\| true` 흡수) |
| BeforeInstall | `scripts/before-install.sh` | 60s | ubuntu | 자동 롤백 트리거 |
| AfterInstall | `scripts/after-install.sh` | 120s | ubuntu | 자동 롤백 트리거 (R10 — SSM 0건이면 명시적 exit 1) |
| ApplicationStart | `scripts/application-start.sh` | 180s | ubuntu | 자동 롤백 트리거 (이미지 풀 + 컨테이너 부팅) |
| ValidateService | `scripts/validate-service.sh` | 90s | ubuntu | 자동 롤백 트리거 (SC-2 — 60초 폴링 후 exit 1) |

### 1.3 자동 롤백 (DeploymentGroup 측 설정 — M4-A Step 13)

DeploymentGroup `farmos-prod` 의 `autoRollbackConfiguration.enabled = true` + `events = [DEPLOYMENT_FAILURE]` 가 있으면, 위 훅 중 하나라도 exit 1 시 CodeDeploy 가 직전 성공 revision 으로 자동 재배포합니다. 본 프로젝트 R12 (자동 롤백 ≤ 60초) 는 이 메커니즘에 의존.

---

## §2. 각 .sh 파일 분석

각 훅 파일에 대해 (1) 입력 환경변수, (2) 핵심 동작, (3) 실패 시 결과, (4) 5블록 함정 — 4 항목 패턴으로 정리.

### Step 2.1 `application-stop.sh` (ApplicationStop, 120s)

#### 블록 A — 진입점

```
CodeDeploy agent (EC2) 가 deployment-archive/scripts/application-stop.sh 를 자동 실행
   ↓ runas: ubuntu
   ↓ 환경변수: APPLICATION_NAME, DEPLOYMENT_ID, DEPLOYMENT_GROUP_ID,
              DEPLOYMENT_GROUP_NAME, LIFECYCLE_EVENT
   ↓ stdout/stderr → /var/log/aws/codedeploy-agent/codedeploy-agent.log
                    + /opt/codedeploy-agent/deployment-root/<dg>/<deploy>/logs/scripts.log
```

#### 블록 B — 스크립트 핵심 로직 ASCII

```
┌────────────────────────────────────────────────────────────┐
│ application-stop.sh                                        │
├────────────────────────────────────────────────────────────┤
│  if [ -f /opt/farmos/docker-compose.yml ]; then            │
│      cd /opt/farmos                                        │
│      docker compose stop || true   ← 실패해도 무시          │
│  else                                                      │
│      LOG "First deploy — skipping"                         │
│  fi                                                        │
│  exit 0   ← 항상 성공 처리 (idempotent)                    │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입출력 표

| 항목 | 값 |
|---|---|
| 입력 (CodeDeploy env) | `LIFECYCLE_EVENT=ApplicationStop` |
| 입력 (파일) | `/opt/farmos/docker-compose.yml` (존재 여부만 체크) |
| 출력 (파일) | 없음 — 단지 컨테이너 stop |
| 출력 (exit) | **0 (항상)** — 첫 배포 + 재배포 모두 안전 |
| 첫 배포 동작 | docker-compose.yml 미존재 → "skipping" 로그만 남기고 종료 |

#### 블록 D — 검증 명령

로컬에서:
```bash
# bash 신택스 검증
bash -n scripts/application-stop.sh && echo "✓ syntax OK"
# → ✓ syntax OK
```

EC2 에서 (배포 후):
```bash
# CodeDeploy 가 실행한 마지막 application-stop 로그
sudo tail -200 /opt/codedeploy-agent/deployment-root/*/d-*/logs/scripts.log \
  | grep application-stop
# → "[application-stop] ... Stopping existing stack" 또는 "First deploy — skipping"
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| 첫 배포에서 ApplicationStop 실패 | `set -e` 가 `docker compose stop` 의 0 외 exit 를 차단 | 본 스크립트는 `set -uo pipefail` (`-e` 없음) + `\|\| true` 사용 — 변경 금지 |
| ApplicationStop 가 60초 이상 | `docker compose stop` 이 컨테이너 SIGTERM → SIGKILL 의 grace period 대기 | docker-compose.yml 의 `stop_grace_period: 10s` 단축 (이미 설정됨) |
| `cd /opt/farmos` 가 No such file | bootstrap-ec2.sh 에서 디렉토리 생성 실패 | M5 §3 bootstrap 9/9 단계에서 `/opt/farmos` 검증 |
| Permission denied | runas 사용자(ubuntu) 가 /opt/farmos 소유자 아님 | bootstrap-ec2.sh 의 `chown -R ubuntu:ubuntu /opt/farmos` 재실행 |
| `docker: command not found` | bootstrap-ec2.sh 미실행 또는 docker 그룹 미적용 | `sudo bash bootstrap-ec2.sh` + `newgrp docker` |

---

### Step 2.2 `before-install.sh` (BeforeInstall, 60s)

#### 블록 A — 진입점

```
CodeDeploy agent (EC2) 자동 실행
   ↓ runas: ubuntu
   ↓ LIFECYCLE_EVENT=BeforeInstall
   ↓ 시점: ApplicationStop 직후, Install (release/ 동기화) 직전
```

#### 블록 B — 스크립트 핵심 로직 ASCII

```
┌────────────────────────────────────────────────────────────┐
│ before-install.sh                                          │
├────────────────────────────────────────────────────────────┤
│ set -euo pipefail                                          │
│                                                            │
│ # 1) 디렉토리 보장                                          │
│ sudo mkdir -p /opt/farmos/{data/postgres, data/chroma,     │
│                            dist, release}                  │
│ sudo chown -R ubuntu:ubuntu /opt/farmos                    │
│                                                            │
│ # 2) 직전 IMAGE_TAG 백업 (롤백 보조용)                      │
│ PREV_IMAGE=$(docker inspect farmos-api                     │
│                --format '{{.Config.Image}}' 2>/dev/null    │
│                || echo "")                                 │
│ if [ -n "$PREV_IMAGE" ]; then                              │
│     PREV_TAG="${PREV_IMAGE##*:}"                           │
│     echo "$PREV_TAG" > /opt/farmos/.prev-tag               │
│ fi                                                         │
│                                                            │
│ exit 0                                                     │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입출력 표

| 항목 | 값 |
|---|---|
| 입력 (CodeDeploy env) | `LIFECYCLE_EVENT=BeforeInstall` |
| 입력 (Docker) | 직전 `farmos-api` 컨테이너의 `.Config.Image` |
| 출력 (디렉토리) | `/opt/farmos/{data/postgres, data/chroma, dist, release}` |
| 출력 (파일) | `/opt/farmos/.prev-tag` (re-deploy 시. 첫 배포 시 미생성) |
| 출력 (exit) | 0 (정상) / 1 (`set -e` — mkdir 실패 등) |

#### 블록 D — 검증 명령

```bash
# 로컬 syntax
bash -n scripts/before-install.sh && echo "✓ syntax OK"

# EC2 에서 (배포 후)
ls -la /opt/farmos
# → drwxr-xr-x ... ubuntu ubuntu ... data dist release
cat /opt/farmos/.prev-tag 2>/dev/null
# → re-deploy 시: sha-<12자> 출력 / 첫 배포: 파일 없음 (정상)
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| BeforeInstall 60s 타임아웃 | `sudo` 비번 입력 대기 (NOPASSWD 미설정) | EC2 ubuntu 사용자는 기본 NOPASSWD — `/etc/sudoers.d/90-cloud-init-users` 손대지 말 것 |
| `mkdir: cannot create directory '/opt/farmos': Permission denied` | 첫 배포 직전, `/opt/farmos` 가 root 소유 | bootstrap-ec2.sh 의 `chown -R ubuntu:ubuntu /opt/farmos` 가 미실행 |
| `.prev-tag` 가 안 생김 (첫 배포 후) | 직전 컨테이너 없음 — **정상 동작** | 두 번째 배포 후 다시 확인 |
| docker inspect 가 stderr 노출 | 첫 배포 시 farmos-api 컨테이너 없음 — **정상** | `2>/dev/null \|\| echo ""` 로 흡수됨 |
| `chown` 권한 거부 | runas user 가 root 가 아님 | sudo + ubuntu NOPASSWD 조합 — 변경 금지 |

---

### Step 2.3 `after-install.sh` (AfterInstall, 120s) — 핵심 훅

#### 블록 A — 진입점

```
CodeDeploy agent 자동 실행
   ↓ LIFECYCLE_EVENT=AfterInstall
   ↓ 시점: Install (release/ 동기화) 직후, ApplicationStart 직전
   ↓ Plan SC-9 핵심: SSM Parameter Store → /opt/farmos/.env (chmod 600)
```

#### 블록 B — 스크립트 7단계 로직 ASCII

```
┌──────────────────────────────────────────────────────────────┐
│ after-install.sh (가장 큰 훅 — 7단계)                        │
├──────────────────────────────────────────────────────────────┤
│ 1) /opt/farmos/release/* → /opt/farmos/                       │
│      docker-compose.yml, nginx.conf, dist/ 동기화              │
│                                                              │
│ 2) IMDSv2 로 region 자동 감지                                 │
│      TOKEN= http://169.254.169.254/latest/api/token (PUT)    │
│      REGION= placement/region                                │
│                                                              │
│ 3) SSM /farmos/prod/* 28키 조회 (--with-decryption)            │
│      jq 매핑 함수로 SSM 키 → backend/.env.example 변수명 변환  │
│      예: /farmos/prod/db/url → DATABASE_URL=<value>           │
│                                                              │
│ 4) 비-비밀 default 26개 추가                                  │
│      PROJECT_NAME, API_V1_PREFIX, EMBED_DIM=1024 등         │
│                                                              │
│ 5) chmod 600 + chown ubuntu:ubuntu                            │
│                                                              │
│ 6) 28키 검증 (PARAM_COUNT >= 28 필수)                          │
│    + 6핵심 변수 grep                                          │
│      DATABASE_URL JWT_SECRET_KEY LITELLM_URL LITELLM_API_KEY  │
│      UPSTAGE_API_KEY POSTGRES_PASSWORD                       │
│                                                              │
│ 7) bash source .env 신택스 검증 (R13 추가)                    │
└──────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입출력 표

| 단계 | 입력 | 출력 | 실패 시 |
|---|---|---|---|
| 1 | `/opt/farmos/release/*` | `/opt/farmos/{docker-compose.yml, nginx.conf, dist/}` | `set -e` → exit 1 |
| 2 | IMDSv2 (`http://169.254.169.254/latest/meta-data/`) | `$REGION` 변수 | REGION 빈 문자열 시 `exit 1` |
| 3 | SSM 28키 + KMS Decrypt | `.env` 28 라인 (SSM 매핑) | `length(.Parameters) == 0` 시 `exit 1` |
| 4 | (heredoc DEFAULTS) | `.env` +26 라인 | — |
| 5 | `.env` | `chmod 600` + `chown ubuntu:ubuntu` | `set -e` |
| 6 | `.env` | LOG `SSM keys: 28, .env lines: 54` | `PARAM_COUNT < 28` 또는 `KEY 누락` 시 `exit 1` |
| 7 | `.env` | `set -a; source .env; set +a` | `source` 실패 시 `exit 1` |

#### 블록 D — 검증 명령

```bash
# 로컬 syntax
bash -n scripts/after-install.sh && echo "✓ syntax OK"

# 정적 검증 (jq 매핑 함수 — 28개 키 모두 매핑되는지)
grep -oE '"/farmos/prod/[a-z_/]+"' scripts/after-install.sh | wc -l
# → 28 이어야 함 (jq if-elif 분기 28개)

# EC2 에서 배포 후
ls -la /opt/farmos/.env
# → -rw------- 1 ubuntu ubuntu  ~3000 .env  ← chmod 600 + ubuntu 소유
sudo wc -l /opt/farmos/.env
# → ~54 (28 SSM + 26 default)

sudo grep -E "^(DATABASE_URL|JWT_SECRET_KEY|LITELLM_URL|LITELLM_API_KEY|UPSTAGE_API_KEY|POSTGRES_PASSWORD)=" /opt/farmos/.env | wc -l
# → 6
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `Parameter Store returned 0 keys` 후 exit 1 | IAM Instance Profile 의 ssm:GetParametersByPath 권한 없음 또는 KMS Decrypt 거부 | M4-A §3 Step 7 inline policy 의 `KMSDecryptForSSM` Sid 확인 + M5 Step 4 IAM Profile attach 확인 |
| `Failed to detect EC2 region via IMDSv2` | IMDSv2 가 disabled (HttpTokens=optional 인데 IMDS hop limit=1) | EC2 → Instance → Actions → Instance settings → Modify instance metadata options → IMDSv2 = Required (또는 Optional) |
| `Critical variable JWT_SECRET_KEY missing` | M4-A Step 14 SSM 시드에서 `/farmos/prod/jwt/secret_key` 미시드 | `aws ssm put-parameter --name /farmos/prod/jwt/secret_key --type SecureString --value "$(openssl rand -base64 48)"` |
| `.env syntax check failed` | SSM 값 안에 escape 안 된 따옴표/`$` | 해당 키 `aws ssm put-parameter --overwrite --value "..."` 로 재시드 |
| `length(.Parameters) = 27` (잡학적) | SSM 시드 1개 누락 | M4-A §14.3 카테고리별 카운트 명령으로 누락 키 식별 |

---

### Step 2.4 `application-start.sh` (ApplicationStart, 180s)

#### 블록 A — 진입점

```
CodeDeploy agent 자동 실행
   ↓ LIFECYCLE_EVENT=ApplicationStart
   ↓ 시점: AfterInstall 완료 (.env 생성됨) 직후
```

#### 블록 B — 스크립트 핵심 로직 ASCII

```
┌────────────────────────────────────────────────────────────┐
│ application-start.sh                                       │
├────────────────────────────────────────────────────────────┤
│ set -euo pipefail                                          │
│ cd /opt/farmos                                             │
│                                                            │
│ # 1) .env 로드 (IMAGE_TAG, GHCR_OWNER 포함)                │
│ set -a                                                     │
│ source ./.env                                              │
│ set +a                                                     │
│                                                            │
│ # 2) GHCR 이미지 풀                                        │
│ docker compose pull   ← public 레포: 익명, private: login  │
│                                                            │
│ # 3) 스택 기동                                             │
│ docker compose up -d --remove-orphans                      │
│                                                            │
│ # 4) 72시간 이상 dangling 이미지 정리                       │
│ docker image prune -f --filter "until=72h" || true         │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입출력 표

| 항목 | 값 |
|---|---|
| 입력 (env) | `IMAGE_TAG`, `GHCR_OWNER`, 그리고 .env 의 28 SSM 변수 |
| 출력 (컨테이너) | `farmos-postgres`, `farmos-api`, `farmos-nginx` (docker-compose.yml 정의) |
| 타임아웃 한계 | 180초 — 이미지 풀(2GB+) + 부팅 |
| 실패 시 | `docker compose pull/up` 실패 시 `set -e` → exit 1 → 자동 롤백 |

#### 블록 D — 검증 명령

```bash
bash -n scripts/application-start.sh && echo "✓ syntax OK"

# EC2 에서 배포 후
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
# → farmos-postgres   Up   5432/tcp
# → farmos-api        Up   8000/tcp
# → farmos-nginx      Up   0.0.0.0:80->80/tcp

# IMAGE_TAG 가 .env 의 값과 일치하는지
docker inspect farmos-api --format '{{.Config.Image}}'
# → ghcr.io/himedia-ai-01/farmos-api:sha-<12자>
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| 180s 타임아웃 (이미지 풀이 느림) | t3.medium의 첫 풀 + 이미지 2GB+ | 첫 배포만 한 번 timeout. 두 번째 배포부턴 layer 캐시 → 30초 미만 |
| `unauthorized: authentication required` (GHCR) | 레포가 private + EC2 에서 `docker login ghcr.io` 미수행 | 레포 Settings → Packages → farmos-api → 가시성 변경 = Public, **또는** bootstrap 후 `docker login ghcr.io -u <PAT 사용자>` |
| `Cannot connect to the Docker daemon` | docker 서비스 미실행 또는 ubuntu 가 docker 그룹 미가입 | `sudo systemctl start docker` + `newgrp docker` (M5 bootstrap 8/9 검증) |
| `manifest unknown` | IMAGE_TAG (sha-xxx) 가 GHCR 에 push 되지 않음 | GH Actions `build-and-push` 잡 로그에서 `Successfully pushed` 확인 |
| `port is already allocated` | 직전 nginx 컨테이너 비정상 종료 후 잔존 | `docker rm -f farmos-nginx` 후 재배포 트리거 |

---

### Step 2.5 `validate-service.sh` (ValidateService, 90s) — 자동 롤백 트리거

#### 블록 A — 진입점

```
CodeDeploy agent 자동 실행
   ↓ LIFECYCLE_EVENT=ValidateService
   ↓ 시점: ApplicationStart 직후 (마지막 훅)
   ↓ Plan SC-2 (/health 200), R12 자동 롤백 ≤ 60초 핵심
```

#### 블록 B — 스크립트 핵심 로직 ASCII

```
┌────────────────────────────────────────────────────────────┐
│ validate-service.sh                                        │
├────────────────────────────────────────────────────────────┤
│ for i in $(seq 1 30); do                                   │
│   if curl -fs -o /dev/null http://localhost/health; then  │
│     LOG "Healthy on attempt $i"                            │
│     exit 0   ← Deployment Succeeded                       │
│   fi                                                       │
│   sleep 2                                                  │
│ done                                                       │
│                                                            │
│ # 60초 (30 × 2) 동안 건강하지 않음 → 실패                  │
│ LOG "ERROR: /health failed after 30 attempts"              │
│ docker ps                                                  │
│ exit 1   ← Auto-Rollback 트리거 (DG 설정 의존)            │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입출력 표

| 항목 | 값 |
|---|---|
| 입력 | `http://localhost/health` (nginx → return 200 "ok") |
| 폴링 빈도 | 2초 간격, 30회 (= 최대 60초) |
| Hook timeout | 90초 (= 60초 폴링 + 30초 마진) |
| 출력 (성공) | `exit 0` → Deployment Succeeded |
| 출력 (실패) | `exit 1` → CodeDeploy Auto-Rollback (직전 revision 재배포) |

#### 블록 D — 검증 명령

```bash
bash -n scripts/validate-service.sh && echo "✓ syntax OK"

# EC2 에서 배포 후 (수동)
curl -i http://localhost/health
# HTTP/1.1 200 OK
# Content-Type: text/plain
# ok
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| ValidateService 가 60초 만에 실패 | 컨테이너가 부팅 중 (FastAPI uvicorn 모델 로딩 시 30~50초) | timeout 90초 충분하지만, 첫 배포에서 sentence-transformers 다운로드 시 추가 시간 — bootstrap에서 swap 2GB 확보 + Dockerfile 에서 모델 사전 다운로드 |
| `curl: connect: Connection refused` | nginx 컨테이너 미부팅 | `docker logs farmos-nginx` |
| 200 대신 502 Bad Gateway | nginx upstream(api:8000) 부팅 실패 | `docker logs farmos-api` (DB 연결 / SSM .env 변수 확인) |
| 200 대신 301 무한 리다이렉트 | nginx.conf 에 `return 301 https` 가 추가됨 — Plan R12 위반 | `cat /opt/farmos/nginx.conf \| grep -n "return 301"` → 줄 발견 시 즉시 제거 |
| Auto-rollback 가 발동 안 함 | DG 설정에서 `Roll back when a deployment fails` 미체크 | M4-A Step 13 Advanced > Rollbacks 체크 |

---

## §3. 로컬 검증 (5분, 첫 푸시 직전 권장)

### 3.1 신택스 검증 (모든 .sh)

```bash
cd E:/new_my_study/FarmOS-Deploy-Test
for f in scripts/{application-stop,before-install,after-install,application-start,validate-service}.sh; do
  echo "→ $f"
  bash -n "$f" && echo "  ✓ syntax OK" || echo "  ✗ syntax FAIL"
done
```

기대 출력 (5행 모두 ✓):
```
→ scripts/application-stop.sh
  ✓ syntax OK
→ scripts/before-install.sh
  ✓ syntax OK
→ scripts/after-install.sh
  ✓ syntax OK
→ scripts/application-start.sh
  ✓ syntax OK
→ scripts/validate-service.sh
  ✓ syntax OK
```

### 3.2 ShellCheck (권장 — 정적 분석)

ShellCheck 는 따옴표 누락, undefined variable, deprecated 사용 등을 발견합니다. 설치 후:

```bash
# Windows: choco install shellcheck   |  WSL: apt-get install shellcheck
shellcheck -x scripts/*.sh
```

기대: 경고 0 (또는 SC2086 같은 사소한 정보 수준만). after-install.sh 에서 `# shellcheck disable=SC1091` 같은 의도적 무시는 OK.

### 3.3 appspec.yml 형식 검증

```bash
# YAML 신택스
python -c "import yaml; yaml.safe_load(open('appspec.yml'))" && echo "✓ YAML OK"

# 필수 키 존재
grep -E "^(version|os|files|hooks)" appspec.yml
# → version: 0.0
# → os: linux
# → files:
# → hooks:

# hook 5개 모두 정의됨
grep -E "^  (ApplicationStop|BeforeInstall|AfterInstall|ApplicationStart|ValidateService):" appspec.yml | wc -l
# → 5
```

### 3.4 git +x 비트 일괄 점검

```bash
git ls-files --stage scripts/*.sh appspec.yml
# scripts/*.sh 5개: 100755
# appspec.yml: 100644 (실행 비트 불필요)
```

---

## §4. EC2 디버깅 (배포 실패 시)

### 4.1 CodeDeploy agent 로그 위치 (3개)

```
/var/log/aws/codedeploy-agent/codedeploy-agent.log
   ↑ agent 데몬 자체 로그 (시작/정지/연결 오류)

/opt/codedeploy-agent/deployment-root/
└── <DG-id>/                    ← farmos-prod DG 의 ID (e.g., abcd-1234)
    └── <Deployment-id>/        ← d-XXXXXXX
        ├── deployment-archive/   ← S3 zip 압축 해제 (실제 파일들)
        ├── logs/
        │   └── scripts.log      ★ 가장 중요 — 5개 훅 stdout/stderr 통합
        └── ...
```

### 4.2 마지막 실패 배포 로그 추출 (한 줄)

```bash
# EC2 SSH 접속 후
sudo find /opt/codedeploy-agent/deployment-root -name 'scripts.log' -printf '%T@ %p\n' \
  | sort -nr | head -1 | awk '{print $2}' | xargs sudo tail -300
```

### 4.3 훅별 분리 추출

```bash
LATEST_LOG=$(sudo find /opt/codedeploy-agent/deployment-root -name 'scripts.log' -printf '%T@ %p\n' | sort -nr | head -1 | awk '{print $2}')

# 어느 훅에서 실패했나?
sudo grep -E '\[(application-stop|before-install|after-install|application-start|validate-service)\]' "$LATEST_LOG" \
  | tail -50

# AfterInstall SSM 28키 카운트만 보고 싶다
sudo grep -E 'SSM keys|.env lines' "$LATEST_LOG"
# → [after-install] ... SSM keys: 28 (expected 28), .env lines: 54
```

### 4.4 자주 쓰는 디버깅 명령

```bash
# CodeDeploy agent 상태
sudo systemctl status codedeploy-agent --no-pager

# agent 재시작 (배포 중에는 절대 X)
sudo systemctl restart codedeploy-agent

# 현재 EC2 가 attach 된 IAM Profile
aws sts get-caller-identity --region ap-northeast-2
# → Arn: arn:aws:sts::242201280878:assumed-role/farmos-ec2-instance/i-xxxxx

# SSM 28키 직접 조회 (after-install.sh 가 하는 것과 동일)
aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --with-decryption --region ap-northeast-2 \
  --query "length(Parameters)" --output text
# → 28

# /opt/farmos 의 .env 5줄 미리보기
sudo head -5 /opt/farmos/.env
sudo wc -l /opt/farmos/.env
```

### 4.5 컨테이너 로그

```bash
# 부팅 실패 시
docker logs --tail 200 farmos-api
docker logs --tail 200 farmos-postgres
docker logs --tail 100 farmos-nginx

# 실시간 추적
docker compose -f /opt/farmos/docker-compose.yml logs -f api
```

---

## §5. M4-B 완료 검증 (4건 통과)

```bash
cd E:/new_my_study/FarmOS-Deploy-Test

echo "=== 1. 6개 파일 존재 ==="
for f in appspec.yml scripts/{application-stop,before-install,after-install,application-start,validate-service}.sh; do
  [ -f "$f" ] && echo "✓ $f" || echo "✗ $f MISSING"
done

echo "=== 2. .sh syntax OK (5건) ==="
for f in scripts/*.sh; do
  bash -n "$f" 2>/dev/null && echo "✓ $f" || echo "✗ $f"
done

echo "=== 3. git +x 비트 (5건 모두 100755) ==="
git ls-files --stage scripts/*.sh | awk '{print $1, $4}'

echo "=== 4. appspec.yml 5 hooks ==="
grep -cE "^  (ApplicationStop|BeforeInstall|AfterInstall|ApplicationStart|ValidateService):" appspec.yml
# → 5
```

위 4건 모두 통과 → M4-C 진행 가능.

---

## §6. 다음 단계

| Module | 다음 행위 |
|---|---|
| **M4-C** | [`m4c-gh-secrets-deploy.md`](./m4c-gh-secrets-deploy.md) — GH Secrets 6개 등록 + 첫 dev push |
| (병행) **M5** | [`m5-ec2-bootstrap.md`](./m5-ec2-bootstrap.md) — EC2 시작 + bootstrap. **첫 배포 직전 EC2 가 ready 상태이어야 함** |

---

## §7. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | 2026-04-28 | M4-B 코드 검증 절차 + 라이프사이클 시퀀스 + 5훅 5블록 함정 + EC2 디버깅 |

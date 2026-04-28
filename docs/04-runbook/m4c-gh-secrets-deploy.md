# M4-C Runbook — GH Secrets 등록 + 첫 배포

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` §13, §16.13 (v0.4)
> **Plan SC**: SC-1 (dev push → 5분 내 prod), SC-4 (자동 롤백 ≤ 60초), SC-8 (OIDC AssumeRole)
> **선행**: M4-A (캐치 테이블 #2/#5/#6/#7), M4-B (`appspec.yml` + 5 lifecycle .sh), M5 (EC2 ready)
> **후행**: M6 (CloudFlare DNS — 도메인 연결)
> **소요 시간**: 30~60분 (Secrets 등록 5분 + 첫 배포 디버깅 25~55분)

---

## §0. 사전 점검 (10분)

### 0.1 M4-A 캐치 테이블 8건 모두 채워졌는가

본 모듈은 M4-A 의 다음 캐치 값들을 GH Secrets 로 옮기는 작업입니다. M4-A `m4a-aws-setup.md §1` 의 캐치 테이블을 다시 확인하세요.

| # | 캐치 값 | 본 모듈 사용 |
|---|---|---|
| 1 | `arn:aws:iam::242201280878:oidc-provider/...` | (간접) Trust Policy 안에 박혀 있음 |
| 2 | **`arn:aws:iam::242201280878:role/farmos-gh-actions-deploy`** | ★ `AWS_ROLE_ARN` Secret |
| 3 | `arn:aws:iam::242201280878:instance-profile/farmos-ec2-instance` | (M5 에서 사용) |
| 4 | `arn:aws:iam::242201280878:role/farmos-codedeploy-svc` | (M4-A Step 13 에서 사용 완료) |
| 5 | **`farmos-codedeploy-242201280878-ap-northeast-2`** | ★ `S3_DEPLOY_BUCKET` Secret |
| 6 | **`farmos`** | ★ `CODEDEPLOY_APP` Secret |
| 7 | **`farmos-prod`** | ★ `CODEDEPLOY_GROUP` Secret |
| 8 | `28` (SSM 키 카운트) | (간접 — after-install.sh 가 검증) |

검증:
```bash
ACCT=242201280878
aws iam get-role --role-name farmos-gh-actions-deploy --query 'Role.Arn' --output text
aws s3api head-bucket --bucket farmos-codedeploy-${ACCT}-ap-northeast-2
aws deploy get-application --application-name farmos --region ap-northeast-2 \
  --query 'application.applicationName' --output text
aws deploy get-deployment-group --application-name farmos --deployment-group-name farmos-prod \
  --region ap-northeast-2 --query 'deploymentGroupInfo.deploymentGroupName' --output text
```

위 4 명령이 모두 정상 응답 → 진행 가능.

### 0.2 M4-B 산출물 6개 존재 + LF + +x

```bash
cd E:/new_my_study/FarmOS-Deploy-Test
ls -la appspec.yml scripts/*.sh
git ls-files --stage scripts/*.sh   # 5행 모두 100755
```

### 0.3 GitHub CLI (gh) 또는 콘솔 접근

본 런북은 양쪽 모두 제공합니다.

| 옵션 | 사전 준비 |
|---|---|
| **gh CLI (권장 — 빠름)** | `gh auth login` (Web 브라우저) → `gh auth status` 로 확인 |
| GitHub Console | 브라우저 로그인 (uio400@naver.com 계정으로 `Himedia-AI-01/FarmOS-Deploy-Test` 레포 Admin 권한) |

```bash
# gh CLI 사전 검증
gh --version            # 2.40.0+ 권장
gh auth status          # ✓ Logged in to github.com as <user>
gh repo view Himedia-AI-01/FarmOS-Deploy-Test --json name,visibility
# → {"name":"FarmOS-Deploy-Test","visibility":"public"}  (또는 private)
```

### 0.4 deploy.yml / ci.yml 존재 검증

```bash
ls -la .github/workflows/
# → ci.yml      (M3 산출물, branches-ignore: [dev])
# → deploy.yml  (M4-C 산출물 — 이미 작성됨)

# YAML 신택스
python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "✓ deploy.yml OK"
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "✓ ci.yml OK"

# .gitignore 의 EOL = LF (Windows 에서 commit 시 CRLF 변환 방지)
file .github/workflows/deploy.yml
# → ASCII text  (CRLF 라면 .gitattributes 의 .github/workflows/*.yml text eol=lf 적용 안 됨)
```

---

## §1. GH Secrets 등록 (6개)

### 1.0 Secrets 매핑 표

| Secret 이름 | 값 (M4-A 캐치 출처) | 형식 |
|---|---|---|
| `AWS_REGION` | `ap-northeast-2` | 평문 |
| `AWS_ROLE_ARN` | `arn:aws:iam::242201280878:role/farmos-gh-actions-deploy` (#2) | 평문 ARN |
| `S3_DEPLOY_BUCKET` | `farmos-codedeploy-242201280878-ap-northeast-2` (#5) | 평문 |
| `CODEDEPLOY_APP` | `farmos` (#6) | 평문 |
| `CODEDEPLOY_GROUP` | `farmos-prod` (#7) | 평문 |
| `GHCR_OWNER` | `Himedia-AI-01` (대문자 그대로 — deploy.yml 안에서 lowercase 변환됨) | 평문 |

> ⚠️ `GHCR_OWNER` 는 **대문자 보존** (`Himedia-AI-01`). deploy.yml 의 `Compute image metadata` 단계에서 `tr '[:upper:]' '[:lower:]'` 로 자동 변환되어 `himedia-ai-01/farmos-api` 가 됩니다. 사용자 직접 lowercase 입력 금지 — git remote 와 시각적 일치 깨짐.

---

### Step 1.1 콘솔로 등록 (6개 일괄)

#### 블록 A — 화면 진입 경로

```
GitHub.com 로그인 (uio400@naver.com)
   ↓ 우상단 검색바 또는 직접 URL: https://github.com/Himedia-AI-01/FarmOS-Deploy-Test
레포 페이지
   ↓ 상단 탭 행 우측 ⭐ "Settings" 클릭
       (탭 순서: <> Code | Issues | Pull requests | Actions | Projects | Wiki | Security | Insights | ⭐ Settings)
Settings 페이지
   ↓ 좌측 메뉴 "Security" 그룹 → ⭐ "Secrets and variables" 클릭 (드롭다운)
   ↓ 펼쳐진 하위 메뉴에서 ⭐ "Actions" 클릭
Actions secrets and variables 페이지
   ↓ 우측 상단 [New repository secret] 녹색 버튼 클릭 (6번 반복)
```

#### 블록 B — 화면 레이아웃 (New repository secret 모달)

```
┌─────────────────────────────────────────────────────────────┐
│  Actions secrets and variables                              │
│  ────────────────────────────────────────                   │
│  Tabs: [Secrets] [Variables]   ★ Secrets 탭 활성             │
│                                                             │
│  Repository secrets        [New repository secret] ★        │
│  ────────────────────────────────────────                   │
│   (등록된 Secrets 목록 — 처음엔 빈 상태)                     │
└─────────────────────────────────────────────────────────────┘

   ↓ [New repository secret] 클릭

┌─────────────────────────────────────────────────────────────┐
│  New secret                                                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Name *                                                     │
│   ┌───────────────────────────────────────────────────┐     │
│   │ AWS_REGION                                        │     │
│   └───────────────────────────────────────────────────┘     │
│   ※ Secret 이름은 정확히 표 그대로 (대소문자 + 언더스코어)   │
│                                                             │
│  Secret *                                                   │
│   ┌───────────────────────────────────────────────────┐     │
│   │ ap-northeast-2                                    │     │
│   └───────────────────────────────────────────────────┘     │
│   ※ 앞뒤 공백/줄바꿈 0개. 콘솔 마우스 드래그 후 \n 주의       │
│                                                             │
│  [Cancel]                            [Add secret] ★ 클릭    │
└─────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표 (6번 반복)

| 번호 | Name 필드 | Secret 필드 |
|---|---|---|
| 1 | `AWS_REGION` | `ap-northeast-2` |
| 2 | `AWS_ROLE_ARN` | `arn:aws:iam::242201280878:role/farmos-gh-actions-deploy` |
| 3 | `S3_DEPLOY_BUCKET` | `farmos-codedeploy-242201280878-ap-northeast-2` |
| 4 | `CODEDEPLOY_APP` | `farmos` |
| 5 | `CODEDEPLOY_GROUP` | `farmos-prod` |
| 6 | `GHCR_OWNER` | `Himedia-AI-01` |

#### 블록 D — 검증 명령

```bash
gh secret list --repo Himedia-AI-01/FarmOS-Deploy-Test
```

**기대 출력 (6행, 순서 무관)**:
```
AWS_REGION         Updated 2026-04-28
AWS_ROLE_ARN       Updated 2026-04-28
CODEDEPLOY_APP     Updated 2026-04-28
CODEDEPLOY_GROUP   Updated 2026-04-28
GHCR_OWNER         Updated 2026-04-28
S3_DEPLOY_BUCKET   Updated 2026-04-28
```

> ⚠️ Secret **값**은 `gh secret list` 가 보여주지 않습니다 (의도). 값 검증은 `gh workflow run` 후 실제 동작으로 확인.

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Settings 메뉴가 안 보임 | 레포에 Admin/Maintain 권한 없음 | 레포 소유자가 Settings → Collaborators 에 추가 |
| New secret 모달의 Add secret 비활성 | Name 또는 Secret 미입력 | 두 필드 모두 비어있지 않은지 확인 |
| Secret 값 끝에 `\n` 들어감 | 마우스 드래그 후 빈 줄 포함 복사 | 텍스트박스 내 우클릭 → Ctrl+A → Delete → 다시 붙여넣기 |
| Secret 이름 오타 (`AWS-REGION` 같은 하이픈) | GitHub 은 영문 대문자 + `_` 만 허용 | 정확히 `AWS_REGION` (언더스코어) — deploy.yml 의 `${{ secrets.AWS_REGION }}` 와 1:1 매칭 |
| 같은 이름 Secret 다시 등록 시도 | 이미 등록됨 | 목록에서 해당 행 우측 [Update] 버튼 사용 |

---

### Step 1.2 gh CLI 로 일괄 등록 (대안 — 1분)

#### 블록 A — 진입점

```
로컬 터미널 (Bash / Git Bash / WSL / PowerShell)
   ↓ gh auth login 완료된 상태
   ↓ FarmOS-Deploy-Test 디렉토리 또는 --repo 인자 지정
```

#### 블록 B — 명령 시퀀스

```bash
REPO=Himedia-AI-01/FarmOS-Deploy-Test

gh secret set AWS_REGION         --repo "$REPO" --body "ap-northeast-2"
gh secret set AWS_ROLE_ARN       --repo "$REPO" --body "arn:aws:iam::242201280878:role/farmos-gh-actions-deploy"
gh secret set S3_DEPLOY_BUCKET   --repo "$REPO" --body "farmos-codedeploy-242201280878-ap-northeast-2"
gh secret set CODEDEPLOY_APP     --repo "$REPO" --body "farmos"
gh secret set CODEDEPLOY_GROUP   --repo "$REPO" --body "farmos-prod"
gh secret set GHCR_OWNER         --repo "$REPO" --body "Himedia-AI-01"
```

각 명령은 `✓ Set Actions secret AWS_REGION for Himedia-AI-01/FarmOS-Deploy-Test` 같은 1행 출력.

#### 블록 C — 입력값 표

| Secret | --body 값 |
|---|---|
| `AWS_REGION` | `ap-northeast-2` |
| `AWS_ROLE_ARN` | `arn:aws:iam::242201280878:role/farmos-gh-actions-deploy` |
| `S3_DEPLOY_BUCKET` | `farmos-codedeploy-242201280878-ap-northeast-2` |
| `CODEDEPLOY_APP` | `farmos` |
| `CODEDEPLOY_GROUP` | `farmos-prod` |
| `GHCR_OWNER` | `Himedia-AI-01` |

#### 블록 D — 검증

```bash
gh secret list --repo "$REPO"
# → 6행
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `gh: error: command 'secret' not found` | gh 버전 < 2.0 | `gh --version` 확인 후 업데이트 |
| `HTTP 403: Resource not accessible by integration` | gh 가 다른 계정으로 로그인됨 | `gh auth switch` 또는 `gh auth login` 재로그인 |
| `--body` 내용에 공백 포함 시 잘림 | 셸 quoting 미흡 | 큰따옴표로 감쌈 (위 예시 그대로) |
| Windows PowerShell 에서 `\n` 이상 동작 | PS 의 quoting 차이 | Git Bash / WSL 사용 권장. PS 에서는 `gh secret set NAME --body 'value'` 단일 따옴표 |
| Secret 등록은 성공했는데 Run 에서 빈 값 | branch protection 의 environment-scoped secret 사용 중 | repository secret 이 아닌 environment secret 인지 확인 (본 프로젝트는 repo secret 사용) |

---

## §2. 첫 dev push 트리거

### 2.0 사전 점검 (한 번 더)

| 항목 | 명령 | 기대 |
|---|---|---|
| 6 Secrets 등록 | `gh secret list --repo Himedia-AI-01/FarmOS-Deploy-Test \| wc -l` | `6` |
| EC2 ready (M5) | `aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" "Name=instance-state-name,Values=running" --query "Reservations[].Instances[].State.Name" --output text` | `running` |
| EC2 태그 (Tag group AND) | `aws ec2 describe-instances --filters Name=tag:App,Values=farmos Name=tag:Environment,Values=prod --query "Reservations[].Instances[].InstanceId" --output text` | `i-xxx` (1개) |
| SSM 28키 | `aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 --query "length(Parameters)" --output text` | `28` |
| codedeploy-agent 동작 | EC2 ssh 후 `sudo systemctl is-active codedeploy-agent` | `active` |

### Step 2.1 트리거 (`git push origin dev`)

#### 블록 A — 진입점

```
로컬 (FarmOS-Deploy-Test 클론한 곳)
   ↓ 현재 브랜치 dev 인지 확인
   ↓ 빈 commit 또는 의미 있는 commit 으로 push
```

#### 블록 B — 명령 시퀀스 ASCII

```
┌────────────────────────────────────────────────────────────┐
│ git status                                                 │
│ # On branch dev                                            │
│ # Your branch is up to date with 'origin/dev'.             │
│ # nothing to commit                                        │
│                                                            │
│ git checkout dev                                           │
│ git pull origin dev                                        │
│                                                            │
│ # 빈 커밋으로 트리거 (실제 코드 변경 없음)                 │
│ git commit --allow-empty -m "ci: trigger first deploy"     │
│ git push origin dev                                        │
│                                                            │
│ # 직후 브라우저:                                           │
│ #   https://github.com/Himedia-AI-01/FarmOS-Deploy-Test/   │
│ #     actions/workflows/deploy.yml                         │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 항목 | 값 |
|---|---|
| 브랜치 | `dev` (D10 — trunk) |
| 커밋 메시지 (예) | `ci: trigger first deploy` |
| 워크플로우 트리거 | `on.push.branches: [dev]` (deploy.yml) |
| 동시성 그룹 | `concurrency.group: deploy-prod` (직전 배포 완료까지 대기) |

#### 블록 D — 검증 (5초 내 시작 확인)

```bash
gh run list --workflow=deploy.yml --branch=dev --limit 1
```

기대 출력 (push 5초 내):
```
STATUS  CONCLUSION  WORKFLOW       BRANCH  EVENT  ID         ELAPSED  AGE
queued  -           Deploy (prod)  dev     push   12345678   --       3s
```

또는 브라우저에서 Actions 탭 → 가장 위 row 가 노란색(in progress).

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| push 성공했는데 Run 안 생김 | `on:` 브랜치 필터 미스 (`branches: [main]` 으로 잘못 설정) | `.github/workflows/deploy.yml` 의 `branches: [ dev ]` 확인 |
| Run 시작했지만 즉시 cancelled | `concurrency.cancel-in-progress: true` 로 직전 Run 취소됨 | 본 deploy.yml 은 `cancel-in-progress: false` — 직전 Run 완료까지 대기 |
| Run 이 `Skipped` 로 끝남 | `paths-ignore` 필터 또는 `if:` 조건 미충족 | 본 deploy.yml 은 그런 필터 없음 — 다시 push |
| ci.yml 도 같이 트리거됨 | ci.yml `branches-ignore: [dev]` 누락 | M3 ci.yml 수정 — D10 정정 항목 (Design v0.4 Revision 참조) |
| dev 브랜치 자체가 없음 | M0 부터 main 만 만들고 dev 안 만듦 | `git checkout -b dev && git push -u origin dev` + 레포 Settings → Default branch = dev |

---

## §3. GitHub Actions 모니터링

### Step 3.1 Actions 탭 → 잡별 로그 펼치기

#### 블록 A — 화면 진입 경로

```
GitHub Repo (FarmOS-Deploy-Test)
   ↓ 상단 탭 "⚙ Actions" 클릭 (Code 탭 옆)
Actions 페이지
   ↓ 좌측 사이드바 "Workflows" 목록
   ↓   - All workflows
   ↓   - CI            ← M3 워크플로우
   ↓   ★ Deploy (prod) ← 본 모듈, 클릭
Deploy (prod) 워크플로우 페이지
   ↓ Runs 목록 첫 번째 행 (방금 push) 클릭
Run 상세 페이지
   ↓ 좌측 잡 목록:
   ↓   - Build & Push to GHCR   ← Job 1 (병렬)
   ↓   - Build Frontend         ← Job 2 (병렬)
   ↓   ★ Deploy via CodeDeploy   ← Job 3 (Job 1 + 2 완료 후)
   ↓ 잡 클릭 → 우측 영역에 step 별 로그 펼침
```

#### 블록 B — 화면 레이아웃 (Run 상세)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Deploy (prod) #1                                                     │
│   on: push  dev   ← 이벤트 + 브랜치                                  │
│   started 4 minutes ago by clover0309                                │
├──────────────────────────────────────────────────────────────────────┤
│  Summary                                                             │
│  ┌─ Jobs ─────────────────┐  ┌─ Right pane: Selected job ────────┐  │
│  │ ⏳ Build & Push to GHCR│  │ ⏳ Deploy via CodeDeploy           │  │
│  │     2m 13s             │  │   ─ Set up job                  ✓ │  │
│  │ ✓ Build Frontend       │  │   ─ Checkout                    ✓ │  │
│  │     1m 02s             │  │   ─ Download frontend dist      ✓ │  │
│  │ ⏳ Deploy via CodeDeploy│  │   ─ Configure AWS credentials   ✓ │  │
│  │     in progress...     │  │   ─ Verify OIDC AssumeRole      ✓ │  │
│  └────────────────────────┘  │   ─ Pre-flight check            ⏳│  │
│                              │   ─ Update IMAGE_TAG in SSM      ─│  │
│                              │   ─ Build CodeDeploy bundle      ─│  │
│                              │   ─ Upload bundle to S3          ─│  │
│                              │   ─ Trigger CodeDeploy           ─│  │
│                              │   ─ Wait for deployment ...      ─│  │
│                              │   ─ Show deployment summary      ─│  │
│                              └────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 잡별 핵심 step 표

| Job | Step (펼치기 우선순위) | 기대 마지막 라인 |
|---|---|---|
| **Build & Push to GHCR** | "Build & Push (backend)" | `pushing manifest for ghcr.io/himedia-ai-01/farmos-api:sha-...` |
| **Build Frontend** | "Build (tsc -b && vite build)" | `✓ built in xxxs` |
| **Deploy via CodeDeploy** | "Verify OIDC AssumeRole" | `Arn: arn:aws:sts::242201280878:assumed-role/farmos-gh-actions-deploy/gh-actions-...` |
| | "Pre-flight check" | `✓ S3 bucket accessible` + `✓ CodeDeploy application accessible` + `✓ CodeDeploy deployment group accessible` |
| | "Update IMAGE_TAG in Parameter Store" | `✓ /farmos/prod/image/tag = sha-<12자>` |
| | "Trigger CodeDeploy" | `Deployment ID: d-XXXXXXX` + `Console URL: https://...` |
| | "Wait for deployment" | `✓ Deployment Succeeded` |

#### 블록 D — 검증 명령 (CLI)

```bash
# 전체 Run 상태
gh run watch                # 자동 polling (Ctrl+C 종료)

# 또는 단일 Run id 로 폴링
gh run view <run-id> --log   # 전체 로그 ▼ 펼침

# 최근 1개 Run 실패 step 만
gh run view --log-failed     # 실패한 step 의 로그만 출력
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `Configure AWS credentials` 에서 `Could not assume role` | `AWS_ROLE_ARN` 오타 또는 Trust Policy `sub` 미스매치 | M4-A §2 Step 3 Trust Policy 의 `sub: repo:Himedia-AI-01/FarmOS-Deploy-Test:ref:refs/heads/dev` 확인 |
| `Pre-flight check` S3 head-bucket 403 | GH Actions Role 의 Permissions Policy Resource ARN 오타 | M4-A §2 Step 4 Inline Policy 의 `arn:aws:s3:::farmos-codedeploy-242201280878-ap-northeast-2*` 확인 |
| `Update IMAGE_TAG` 가 `AccessDenied: ssm:PutParameter` | Permissions Policy 에 `SSMParameterUpdate` Sid 없음 | M4-A §2 Step 4 정책 JSON 의 `ssm:PutParameter` 권한이 `/farmos/prod/image/tag` 1개로 제한되어 있는지 확인 |
| `Build & Push` 가 `unauthorized` | GH Actions 의 `permissions: packages: write` 없음 | deploy.yml 의 `permissions:` 블록에 `packages: write` 있는지 확인 (이미 있음) |
| `Wait for deployment` 가 무한 대기 | CodeDeploy 가 EC2 못 찾음 ("No instances") | M4-A Step 13 Tag group 1개 안에 2태그 (AND) + EC2 태그 일치 확인 |

---

## §4. CodeDeploy 콘솔 추적

### Step 4.1 Deployments 탭 진입

#### 블록 A — 화면 진입 경로

```
방법 1: 배포 시작 직후 GH Actions 의 "Trigger CodeDeploy" step 로그에 표시된
        Console URL 직접 클릭
        → https://ap-northeast-2.console.aws.amazon.com/codedeploy/home?region=ap-northeast-2#/deployments/d-XXXXXXX

방법 2: 콘솔에서 따라가기
   AWS Console → 검색바 "CodeDeploy"
   CodeDeploy 페이지
      ↓ 좌측 메뉴 "Deploy" 그룹 → "Applications" 클릭
   Applications 목록
      ↓ ⭐ "farmos" 클릭
   Application 상세
      ↓ "Deployments" 탭 (두 번째 탭) ★ 클릭
   Deployments 목록
      ↓ 가장 위 row (방금 GH Actions 가 트리거함) 클릭
   Deployment d-XXXXXXX 상세 페이지
```

#### 블록 B — 화면 레이아웃 (Deployment 상세)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Deployment d-XXXXXXX                                       [Stop]    │
│ farmos / farmos-prod                                                 │
├──────────────────────────────────────────────────────────────────────┤
│ Status: ⏳ In progress                  Start: 2026-04-28T13:24:05Z  │
│                                                                      │
│  Deployment lifecycle events  (instance: i-XXXXXXX)                  │
│  ┌──────────────────────────┬──────────┬──────────────┐              │
│  │ Lifecycle event          │ Duration │ Status       │              │
│  ├──────────────────────────┼──────────┼──────────────┤              │
│  │ ApplicationStop          │     1s   │ ✓ Succeeded  │              │
│  │ DownloadBundle           │     8s   │ ✓ Succeeded  │              │
│  │ BeforeInstall            │     2s   │ ✓ Succeeded  │              │
│  │ Install                  │    14s   │ ✓ Succeeded  │              │
│  │ AfterInstall             │    11s   │ ✓ Succeeded  │              │
│  │ ApplicationStart         │ 2m 30s   │ ⏳ In progress│              │
│  │ ValidateService          │      —   │ Pending      │              │
│  └──────────────────────────┴──────────┴──────────────┘              │
│                                                                      │
│  [View events] ← 이벤트별 ▼ "View events" 클릭 시 stdout/stderr 노출  │
└──────────────────────────────────────────────────────────────────────┘
```

#### 블록 C — Lifecycle 진행 상태 의미

| 상태 | 의미 |
|---|---|
| `Pending` | 아직 시작 안 함 |
| `In progress` | 훅 스크립트 실행 중 |
| `Succeeded` | exit 0 |
| `Failed` | exit non-zero (즉시 다음 훅 차단 + 자동 롤백) |
| `Skipped` | 이전 훅 실패로 건너뜀 |

전체 deployment 상태:
- `Created` → `Queued` → `In progress` → `Succeeded` (정상)
- `In progress` → `Failed` (어느 훅에서든 exit 1) → 자동 롤백 시작 시 → `Failed (Rolled back)` 또는 다음 deployment 가 직전 revision 으로 자동 트리거

#### 블록 D — Hook stdout/stderr 보기

```
Deployment 상세 → 각 lifecycle event 행 우측 [View events] 링크 클릭
   ↓
View events 페이지
   ↓ 화면 상단 "Logs" 토글 / 코드뷰
   ↓
   2026-04-28 13:24:05 [stdout] LifecycleEvent - AfterInstall
   2026-04-28 13:24:06 [stdout] Script - scripts/after-install.sh
   2026-04-28 13:24:06 [stdout] [stdout]Syncing release files to /opt/farmos
   2026-04-28 13:24:06 [stdout] [stdout]Detected region: ap-northeast-2
   2026-04-28 13:24:08 [stdout] [stdout]Fetched 28 parameters
   2026-04-28 13:24:08 [stdout] [stdout]SSM keys: 28 (expected 28), .env lines: 54
   2026-04-28 13:24:08 [stdout] [stdout].env generated and validated
```

> 콘솔 [View events] 가 보여주는 로그는 **CodeDeploy agent 가 EC2 stdout 을 수집해 CloudWatch 가 아닌 자체 store 에 저장한 것**. EC2 의 `/opt/codedeploy-agent/deployment-root/.../logs/scripts.log` 와 동일한 내용.

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Deployment 상세 페이지에 "0 instances" | DG 의 Tag group OR/AND 미스매치 | M4-A Step 13 Tag group 1개 + EC2 태그 일치 확인 |
| AfterInstall Failed (exit 1) | SSM 권한/시드/매핑 중 하나 실패 | View events → `[stderr]` 행 확인. M4-B §4 디버깅 흐름 |
| ApplicationStart Timeout (180s 초과) | 첫 배포 + 이미지 풀 + sentence-transformers 모델 로딩 | timeout 늘리지 말 것 — `swap 2GB` + bootstrap 의 모델 사전 다운로드 확인 |
| ValidateService Failed | nginx 502 (api 부팅 실패) | EC2 ssh 후 `docker logs farmos-api` |
| Status `Failed` 인데 자동 롤백 안 됨 | DG 의 Auto-Rollback 미설정 | M4-A Step 13 Advanced > Rollbacks 의 ☑ "Roll back when a deployment fails" 체크 |

---

## §5. 의도적 실패 시나리오 (자동 롤백 검증, Plan SC-4)

> 본 단계는 **첫 배포가 성공한 후** 진행. SC-4 (자동 롤백 ≤ 60초) 검증.

### Step 5.1 broken 이미지 임시 푸시

#### 블록 A — 진입점

```
로컬 (FarmOS-Deploy-Test)
   ↓ backend/app/main.py 의 health 라우트를 일부러 500 으로 변경 (또는 프로세스 즉시 exit)
   ↓ feature 브랜치 만들지 말고 dev 에 직접 push (트렁크 모델, D10)
```

#### 블록 B — 깨뜨릴 코드 ASCII

```python
# backend/app/main.py 의 어느 위치 (router include 직전)
@app.get("/health")
async def health():
    raise RuntimeError("intentional break for SC-4 rollback test")
```

> ⚠️ 본 시나리오는 **반드시 직후 정상 commit 으로 복원** 필요.

```
git commit -am "test(SC-4): break /health to verify auto-rollback"
git push origin dev
```

#### 블록 C — 기대 흐름 표

| 시각 (상대) | 이벤트 | 상태 |
|---|---|---|
| t=0s | dev push | GH Actions Run 시작 |
| t=~3분 | Build & Push 완료 (broken sha-XXX) | |
| t=~3분 | Deploy 잡 → Trigger CodeDeploy | Deployment In progress |
| t=~5분 | EC2 에서 ApplicationStart 성공 (컨테이너는 일단 부팅됨) | |
| t=~5분 | ValidateService → curl /health → 500 → 30회 fail | Failed (60초 후) |
| t=~6분 | CodeDeploy: Auto-Rollback 트리거 (직전 sha-YYY revision 재배포) | New deployment d-XXX-rollback |
| t=~7분 | 롤백 ValidateService 성공 | Deployment Succeeded (Rollback) |

#### 블록 D — 검증 명령

```bash
# 마지막 2개 deployment
aws deploy list-deployments --application-name farmos \
  --deployment-group-name farmos-prod --region ap-northeast-2 \
  --query 'deployments[0:2]' --output table

# 각각 상태 + rollbackInfo
for D in $(aws deploy list-deployments --application-name farmos \
            --deployment-group-name farmos-prod --region ap-northeast-2 \
            --query 'deployments[0:2]' --output text); do
  echo "── $D ──"
  aws deploy get-deployment --deployment-id "$D" --region ap-northeast-2 \
    --query 'deploymentInfo.{Status:status, Rollback:rollbackInfo, Error:errorInformation}'
done
```

기대:
- 첫 deployment: `status=Failed`, `errorInformation.code=ValidationFailedAtServer`, `rollbackInfo.rollbackTriggeringDeploymentId=<자기 자신>`
- 두 번째 deployment: `status=Succeeded`, `creator=autoRollback`

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| 의도적 push 가 ValidateService 직전에 ApplicationStart 에서 실패 | RuntimeError 가 컨테이너 부팅 자체를 막음 | OK — 어느 훅이든 실패하면 자동 롤백 트리거됨 |
| 자동 롤백이 60초 초과 (~120초) | DG 의 Auto-Rollback events 가 `[DEPLOYMENT_FAILURE]` 만 등록 | `[DEPLOYMENT_FAILURE, DEPLOYMENT_STOP_ON_ALARM]` 추가 시 더 빠르지 않음. 60~120초가 일반적 — SC-4는 "자동 롤백 동작" 자체 검증 |
| 롤백된 직전 revision 도 이미 broken | M4-B 산출물 자체에 버그가 있었던 경우 | SC-4 시나리오 진입 전 정상 배포 1회 확인 필수 |
| 검증 후 broken 코드 그대로 dev 에 남음 | 복원 푸시 누락 | `git revert HEAD && git push origin dev` 로 즉시 복원 |
| GH Actions Run 만 Failed 라고 나오고 CodeDeploy console 은 Succeeded | `aws deploy wait deployment-successful` 가 롤백된 직전 revision 의 succeeded 까지 잡음 (드물게) | 일반적으로 GH Actions Run 의 Failed 가 정확. CodeDeploy console 의 첫 deployment 가 Failed 이면 SC-4 검증 OK |

---

## §6. M4-C 완료 검증 (한 줄)

```bash
echo "=== Secrets ==="
gh secret list --repo Himedia-AI-01/FarmOS-Deploy-Test | wc -l
# → 6

echo "=== 마지막 Run 결과 ==="
gh run list --workflow=deploy.yml --branch=dev --limit 1 \
  --json status,conclusion,databaseId,headSha
# → "conclusion": "success"

echo "=== 마지막 Deployment 결과 ==="
aws deploy list-deployments --application-name farmos \
  --deployment-group-name farmos-prod --region ap-northeast-2 \
  --query 'deployments[0]' --output text \
  | xargs -I{} aws deploy get-deployment --deployment-id {} --region ap-northeast-2 \
    --query 'deploymentInfo.{Status:status, Complete:completeTime}'
# → Status: Succeeded
```

위 3건 모두 통과 → M4-C 완료, M6 (CloudFlare) 진행 가능.

---

## §7. 공통 함정 (전체 모듈 묶음)

### 함정 7.1 — `Could not assume role`

**증상**: GH Actions `Configure AWS credentials` step 에서 `User: ... is not authorized to perform: sts:AssumeRoleWithWebIdentity` 또는 `Could not assume role`.

**5블록 함정**:

| 가능한 원인 | 점검 방법 | 즉시 해결 |
|---|---|---|
| Trust Policy `sub` 가 `repo:OWNER/REPO:*` 가 아닌 좁은 ref | M4-A §2 Step 3 화면 확인 | 일단 `repo:Himedia-AI-01/FarmOS-Deploy-Test:*` 와일드카드로 두고 동작 확인 후 좁히기 |
| `audience` 가 `sts.amazonaws.com` 이 아님 | OIDC Provider Audience 확인 | M4-A Step 1 그대로 |
| `AWS_ROLE_ARN` Secret 의 ARN 오타 | `gh secret get AWS_ROLE_ARN` (값 표시 안됨) — 다시 set | M4-A 캐치 #2 ARN 그대로 |
| GitHub Run 의 `permissions: id-token: write` 누락 | deploy.yml 확인 | 이미 있음 — 변경 X |
| OIDC Provider 가 다른 리전/계정에 등록됨 | `aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[].Arn"` | M4-A Step 1 재실행 |

### 함정 7.2 — CodeDeploy 가 No instances

| 가능한 원인 | 점검 방법 | 즉시 해결 |
|---|---|---|
| EC2 태그 미부착 | `aws ec2 describe-tags --filters Name=resource-id,Values=i-xxx` | EC2 → Instance → Tags → Manage tags → `App=farmos`, `Environment=prod` |
| EC2 태그는 있는데 DG Tag group 분리 (OR) | M4-A Step 13 검증 명령 | DG 편집 → Tag group 1개에 합치기 |
| EC2 stopped/terminated | `aws ec2 describe-instances --instance-ids i-xxx` | start instance |
| codedeploy-agent 미동작 | EC2 에서 `sudo systemctl is-active codedeploy-agent` | `sudo systemctl restart codedeploy-agent` |
| EC2 IAM Profile 에 `AmazonEC2RoleforAWSCodeDeploy` 없음 | `aws iam list-attached-role-policies --role-name farmos-ec2-instance` | M4-A Step 6 정책 첨부 |

### 함정 7.3 — first-deploy specific (첫 배포 한정 함정)

| 증상 | 원인 | 해결 |
|---|---|---|
| ApplicationStart 180s 초과 | 첫 이미지 풀 (2GB+) | 한 번만 — 두 번째 배포부터 캐시 |
| AfterInstall 시 SSM 28키 0 | bootstrap 실행 직후, IAM 검증 없이 첫 push | M5 §4 검증 (특히 SSM 28키) 통과 후 push |
| GHCR 이미지 `unauthorized` | private 레포 + EC2 docker login 미수행 | 레포 visibility = Public 으로 변경 권장 |
| ValidateService 60초 + 컨테이너는 Up 인데 502 | api ↔ db 연결 실패 (POSTGRES_PASSWORD .env 차이) | `docker exec farmos-postgres pg_isready` + `docker logs farmos-api` |
| dev 브랜치 default 가 main 으로 잘못됨 | 레포 Settings → Branches → Default branch | `dev` 로 변경 |

### 함정 7.4 — Rollback 의도 외 동작

| 증상 | 원인 | 해결 |
|---|---|---|
| 자동 롤백이 끝없는 루프 | 롤백된 revision 도 broken | 수동 stop + 새 정상 commit 푸시 |
| 자동 롤백 후 `/farmos/prod/image/tag` 가 새 broken sha 그대로 | deploy.yml 의 IMAGE_TAG put-parameter 가 GH Actions step 인데 EC2 측에서 별도로 안 되돌림 | after-install.sh 의 `.prev-tag` 가 직전 sha 보존하므로 EC2 컨테이너는 정상. SSM 의 image/tag 값은 다음 정상 push 시 자동 갱신 |
| `aws deploy wait` 가 timeout (25분 초과) | DG ApplicationStart 가 무한 대기 (이미지 풀 실패) | timeout 25분 늘리지 말 것 — Trigger 후 console 에서 직접 확인 |
| dev 가 아닌 다른 브랜치로 push 했는데 deploy.yml 트리거됨 | branch 필터 미스 | deploy.yml `on.push.branches: [ dev ]` 확인 |
| `concurrency` 충돌 — 직전 Run 이 hang 됨 | `cancel-in-progress: false` 정책 | GH Actions UI 에서 hang 된 Run 강제 cancel ([Cancel run] 버튼) |

---

## §8. 다음 단계

| Module | 다음 행위 |
|---|---|
| M5 (이미 완료된 경우) | M6 진행 |
| **M6** | [`m6-cloudflare-dns.md`](./m6-cloudflare-dns.md) — 도메인 + CloudFlare DNS A + Flexible SSL |

---

## §9. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | 2026-04-28 | GH Secrets 6개 등록 (콘솔 + gh CLI) + 첫 push 트리거 + Actions/CodeDeploy 모니터링 + SC-4 자동 롤백 검증 |

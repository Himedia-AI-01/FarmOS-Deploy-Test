# M4-A Runbook v2 — AWS 사전 준비 (Phase 1 + Phase 2)

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` §12.6~§12.12 + §18 (v0.4)
> **Plan SC**: SC-7 (IAM 최소권한), SC-8 (OIDC AssumeRole), SC-9 (EC2 SSM 조회)
> **Decisions Applied**: D5(단일 EC2) + D7(S3 deploy) + D8(CodeDeploy+SSM) + D9(28키) + D10(dev=trunk)
> **v2 변경점**: 14 단계 모두 5 블록(진입경로/ASCII/입력값/검증/함정) 균일화 + 콘솔 클릭 단위 디테일 + 화면 전환 명시 + §-1 사전점검 + §A/§B 부록

---

## §-1. 사전 점검 (작업 시작 전 5분)

### -1.1 본 런북에서 사용하는 두 가지 ACCOUNT_ID 형식

> ⚠️ **반드시 읽고 시작**
>
> AWS Console 우상단에는 계정 ID가 `2422-0128-0878` 처럼 **하이픈 포함 12자리**로 표시되지만,
> ARN/Policy/CLI에는 반드시 **하이픈 없는 12자리** 숫자만 들어가야 합니다.
>
> | 위치 | 형식 | 예 |
> |---|---|---|
> | AWS Console 우상단 (가독성) | `XXXX-XXXX-XXXX` | `2422-0128-0878` |
> | ARN / Policy JSON / CLI / 본 런북 | `XXXXXXXXXXXX` | `242201280878` |
>
> **본 파일 전체에서 등장하는 `242201280878` = 사용자 계정 (하이픈 제거 형식)**.
> v1 → v2 전환 시 사용자가 §0에 입력한 `2422-0128-0878` 표시 형식을 ARN 호환 `242201280878`로 일괄 치환했습니다.

### -1.2 AWS CLI 또는 CloudShell 둘 중 하나 준비

| 옵션 | 장점 | 단점 |
|---|---|---|
| **AWS CloudShell (권장)** | 설치 불필요, 콘솔 우상단 `[>_]` 아이콘 클릭 1회로 시작, 자격증명 자동 주입 | 세션 30분 idle 시 종료, 1GB 홈 디렉터리 |
| 로컬 AWS CLI | 빠른 반복 | `aws configure --profile farmos-admin` + Admin IAM 사용자 액세스 키 발급 필요 (M4-A 완료 후 폐기 권장) |

**CloudShell 시작 경로**:
```
AWS Console (어떤 페이지든 OK)
  ↓ 우상단 헤더 우측에서 [>_] 아이콘 (Account dropdown 왼쪽 약 3개) 클릭
  ↓ "AWS CloudShell" 새 탭/하단 패널 자동 열림 (10~20초 소요)
  ↓ 프롬프트 [cloudshell-user@ip-xxx ~]$ 표시되면 준비 완료
```

**리전 확인 (모든 작업의 첫 단계)**:
```bash
# CloudShell or local
aws sts get-caller-identity --query Account --output text
# → 242201280878 가 출력되어야 함

aws configure list | grep region
# → ap-northeast-2 가 출력되어야 함 (다르면 export AWS_DEFAULT_REGION=ap-northeast-2)

export AWS_DEFAULT_REGION=ap-northeast-2
export AWS_PAGER=""   # less 페이저 비활성화 (긴 출력에서 멈추지 않음)
```

### -1.3 ACCOUNT_ID 직접 확인 방법 (콘솔)

```
AWS Console (어떤 페이지든)
  ↓ 우상단 헤더에서 사용자 이름/이메일 옆 ▼ 클릭
  ↓ 드롭다운에서 "Account ID:" 라벨 옆 12자리 숫자 (또는 하이픈 형식) 표시
  ↓ 옆의 [📋 copy] 아이콘 클릭 → 클립보드에 무하이픈 형식으로 복사됨
```

> 본 런북은 사용자 계정 = `242201280878` 가정. 다른 계정이면 VS Code에서 Ctrl+H로 일괄 치환.

### -1.4 권장 IAM 사용자 (Admin 권한, 일회용)

OIDC 셋업 자체에는 IAM/S3/CodeDeploy/SSM 생성 권한이 필요합니다. 콘솔에 로그인한 IAM User가 다음 중 하나면 OK:

- AWS Account Root (가능하지만 비권장 — Step 1 끝나면 즉시 로그아웃)
- Admin Group의 IAM User (`AdministratorAccess` Managed Policy)
- `IAMFullAccess` + `AmazonS3FullAccess` + `AWSCodeDeployFullAccess` + `AmazonSSMFullAccess` 조합

> M4-A 완료 후 OIDC가 모든 배포 권한을 대체하므로, 일회용 Admin 사용자의 액세스 키는 **폐기 권장**.

---

## §0. 사전 입력값 (사용자 직접 채움)

| 변수 | 값 | 비고 |
|---|---|---|
| **`{REGION}`** | `ap-northeast-2` | 서울 — 확정 |
| **`{ACCOUNT_ID}`** | `242201280878` | AWS 콘솔 우상단 12자리 (하이픈 제거 형식). 본 파일 일괄 치환 완료. |
| **`{OWNER}`** | `Himedia-AI-01` | git remote 자동 추출 |
| **`{REPO}`** | `FarmOS-Deploy-Test` | git remote 자동 추출 |
| **S3 버킷명 (자동)** | `farmos-codedeploy-242201280878-ap-northeast-2` | 컨벤션 따름 |

> **시작 전 단계**:
> 1. 콘솔 우상단 ID가 `2422-0128-0878` 표시면 본 파일 사용 그대로 진행 (이미 무하이픈 변환됨).
> 2. AWS CLI 사용 시 `aws configure --profile farmos-admin` (Admin 권한 IAM 사용자 키). M4-A 셋업 후 폐기 권장 — OIDC 가 대체.
> 3. 모든 명령에 `--region ap-northeast-2` 또는 `export AWS_DEFAULT_REGION=ap-northeast-2`.

---

## §1. 출력 캐치 테이블 (진행하며 ARN/이름 채우기)

| # | 리소스 | 값 (사용자 기록) | 다음 모듈에서 사용 |
|---|---|---|---|
| 1 | OIDC Provider ARN | `arn:aws:iam::242201280878:oidc-provider/token.actions.githubusercontent.com` | M4-C deploy.yml |
| 2 | GH Actions Role ARN | `arn:aws:iam::242201280878:role/farmos-gh-actions-deploy` | M4-C `AWS_ROLE_ARN` Secret |
| 3 | EC2 Instance Profile ARN | `arn:aws:iam::242201280878:instance-profile/farmos-ec2-instance` | M5 EC2 Launch |
| 4 | CodeDeploy Service Role ARN | `arn:aws:iam::242201280878:role/farmos-codedeploy-svc` | Step 13 |
| 5 | S3 Deploy Bucket Name | `farmos-codedeploy-242201280878-ap-northeast-2` | M4-C `S3_DEPLOY_BUCKET` Secret |
| 6 | CodeDeploy App Name | `farmos` | M4-C `CODEDEPLOY_APP` Secret |
| 7 | DeploymentGroup Name | `farmos-prod` | M4-C `CODEDEPLOY_GROUP` Secret |
| 8 | SSM 키 시드 개수 | `__/28__` | 검증 명령으로 확인 |

---

# §2. Phase 1 — IAM 사전 준비 (8단계)

## Step 1. OIDC Provider 등록 (GitHub Actions ↔ AWS 신뢰)

### 블록 A — 화면 진입 경로

```
AWS Console (https://console.aws.amazon.com)
  ↓ 우상단 리전 표시 [Asia Pacific (Seoul) ap-northeast-2] 확인
  ↓ ⚠️ 다른 리전이면 클릭 → ap-northeast-2 선택 (IAM은 글로벌이지만 습관화)
  ↓ 상단 검색바에 "IAM" 입력 → IAM (Manage access to AWS resources) 클릭
IAM 서비스 페이지 (https://console.aws.amazon.com/iam/)
  ↓ 좌측 메뉴 "Access management" 그룹 (이미 펼쳐져 있음)
  ↓ "Access management" 그룹의 5번째 항목 ⭐ "Identity providers" 클릭
Identity providers 페이지
  ↓ 우측 상단 [Add provider] 버튼 클릭
Add an Identity provider 페이지로 전환
```

### 블록 B — 화면 레이아웃 (Add provider 페이지)

```
┌──────────────────────────────────────────────────────────────┐
│ Add an Identity provider                                     │
├──────────────────────────────────────────────────────────────┤
│ Configure provider                                           │
│                                                              │
│ Provider type *                                              │
│  ◯ SAML                                                      │
│  ⦿ OpenID Connect    ← ★ 이 라디오 선택                      │
│                                                              │
│ Provider URL *                                               │
│  ┌────────────────────────────────────────────────────┐ ┌──┐ │
│  │ https://token.actions.githubusercontent.com        │ │GT│ │
│  └────────────────────────────────────────────────────┘ └──┘ │
│  └ 입력 후 우측 [Get thumbprint] 버튼 자동 활성화 → 클릭     │
│                                                              │
│  Thumbprint: 6938fd4d98bab03faadb97b34396831e3780aea1        │
│  (자동 채워짐. 수동 변경 비권장)                             │
│                                                              │
│ Audience *                                                   │
│  ┌────────────────────────────────────────────────────┐      │
│  │ sts.amazonaws.com                                  │      │
│  └────────────────────────────────────────────────────┘      │
│  [+ Add another audience]   ← 클릭하지 말 것 (1개로 충분)   │
│                                                              │
│ Tags - optional   (건너뛰기)                                 │
│                                                              │
│ [Cancel]                              [Add provider] ★ 클릭 │
└──────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드명 (콘솔 표시 그대로) | 입력값 | 비고 |
|---|---|---|
| Provider type | `OpenID Connect` (라디오) | SAML 아님 |
| Provider URL | `https://token.actions.githubusercontent.com` | 끝에 슬래시 없음 |
| Get thumbprint 버튼 | 클릭 | 자동 채워짐 — 수동 입력 비권장 |
| Audience | `sts.amazonaws.com` | "Add another audience" 누르지 말 것 |
| Tags | (빈칸 유지) | 선택 사항 |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam list-open-id-connect-providers
```
**기대 출력**:
```json
{ "OpenIDConnectProviderList": [ { "Arn": "arn:aws:iam::242201280878:oidc-provider/token.actions.githubusercontent.com" } ] }
```

화면 검증: IAM → Identity providers 목록에서 `token.actions.githubusercontent.com` 가 1행으로 표시되어야 합니다.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| "Provider URL is invalid" | URL 끝에 `/` 또는 path가 붙음 | `https://token.actions.githubusercontent.com` 정확히 입력 |
| Get thumbprint 버튼 비활성화 | Provider URL 미입력 또는 https:// 누락 | URL 다시 입력 후 박스 밖 클릭 → 버튼 활성화 |
| "Provider with this URL already exists" | 이미 한 번 등록함 | Step 1 건너뛰고 Step 2로 |
| Add provider 클릭 후 무반응 | Audience 미입력 | sts.amazonaws.com 입력 확인 |
| 서울 리전에서 안 보임 | IAM은 Global 서비스 — 어느 리전이든 한 번 등록하면 끝 | 리전 무관 — Step 2로 |

---

## Step 2. GH Actions Role 생성 — Trust entity (Web identity 선택)

### 블록 A — 화면 진입 경로

```
IAM 서비스 페이지
  ↓ 좌측 메뉴 "Access management" 그룹
  ↓ ⭐ "Roles" 클릭 (Identity providers 위쪽 항목)
Roles 페이지
  ↓ 우측 상단 [Create role] 주황 버튼 클릭
Step 1 of 3 — Select trusted entity 페이지로 전환
```

### 블록 B — 화면 레이아웃 (Step 1 of 3 — Select trusted entity)

```
┌────────────────────────────────────────────────────────────────┐
│ Step 1: Select trusted entity                                  │
├────────────────────────────────────────────────────────────────┤
│ Trusted entity type                                            │
│  ┌─────────────────────────────┐ ┌──────────────────────────┐  │
│  │ ◯ AWS service               │ │ ◯ AWS account            │  │
│  │   Allow EC2/Lambda/...      │ │   Another AWS account    │  │
│  └─────────────────────────────┘ └──────────────────────────┘  │
│  ┌─────────────────────────────┐ ┌──────────────────────────┐  │
│  │ ⦿ Web identity   ★ 선택!    │ │ ◯ SAML 2.0 federation    │  │
│  │   Allow OIDC IdP (GitHub)   │ │   Corporate SAML         │  │
│  └─────────────────────────────┘ └──────────────────────────┘  │
│  ┌─────────────────────────────┐                               │
│  │ ◯ Custom trust policy       │                               │
│  │   Manually edit JSON        │                               │
│  └─────────────────────────────┘                               │
│                                                                │
│ ── Web identity 선택 시 아래 폼이 펼쳐짐 ──                    │
│                                                                │
│ Identity provider *                                            │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ token.actions.githubusercontent.com              ▼     │    │
│  └────────────────────────────────────────────────────────┘    │
│  ※ 비어있으면 Step 1 OIDC 등록 미완료 — 새 탭에서 Step 1 확인 │
│                                                                │
│ Audience *                                                     │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ sts.amazonaws.com                                ▼     │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│ GitHub organization - optional                                 │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                                                        │    │ ★ 비워둠
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│ GitHub repository - optional                                   │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                                                        │    │ ★ 비워둠
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│ GitHub branch - optional                                       │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                                                        │    │ ★ 비워둠
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│ [Cancel]                                              [Next ▶] │
└────────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드명 (콘솔 표시 그대로) | 입력값 | 비고 |
|---|---|---|
| Trusted entity type | `Web identity` (3번째 라디오) | 5개 옵션 중 위에서 3번째 |
| Identity provider | `token.actions.githubusercontent.com` 드롭다운에서 선택 | Step 1 미완료 시 비어 있음 |
| Audience | `sts.amazonaws.com` 드롭다운에서 선택 | Step 1에서 등록한 값 |
| GitHub organization | (비워둠) | ★ Optional. Trust Policy JSON에서 sub 조건으로 직접 정밀 설정하므로 여기선 비움 |
| GitHub repository | (비워둠) | ★ 동일 이유 |
| GitHub branch | (비워둠) | ★ 동일 이유 |

> **왜 Optional 3 필드를 비우나?** 콘솔이 자동 생성하는 Trust Policy는 보안 조건이 느슨합니다 (`StringLike` 만 사용). Step 3 에서 `StringEquals`(audience 정확 일치) + `StringLike`(repo+branch 패턴) 조합으로 직접 작성합니다.

### 블록 D — 검증 명령 + 기대 출력

이 단계는 화면 진행만 — Next 클릭 후 Step 3(권한)으로 자동 전환되어야 합니다. 진행이 안 되면 블록 E 참고.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Web identity 라디오가 안 보임 | 콘솔이 좁아서 그리드가 깨짐 | 브라우저 창 가로 1200px 이상 |
| Identity provider 드롭다운이 비어있음 | Step 1 OIDC 등록이 같은 계정/리전에서 끝나지 않음 | 새 탭에서 IAM → Identity providers 확인. 없으면 Step 1 다시 |
| Audience 드롭다운에 sts.amazonaws.com 없음 | Step 1 에서 Audience 입력 누락 | Step 1으로 돌아가 Audience 추가 |
| Optional 3 필드를 채웠더니 Next 가 비활성 | 셋 중 일부만 채움 (3개 모두 채우거나 모두 비워야 함) | 3개 모두 비우기 |
| Next 클릭 후 페이지 이동 없음 | 브라우저 캐시 충돌 | F5 새로고침 후 처음부터 |

---

## Step 3. GH Actions Role — Permissions 단계 건너뛰기 + Trust Policy 직접 작성

### 블록 A — 화면 진입 경로

```
Step 2 의 [Next ▶] 클릭 직후
Step 2 of 3 — Add permissions 페이지로 전환
  ↓ 검색창/체크박스가 보이지만 ★ 아무것도 선택하지 않음
  ↓ 하단 [Next ▶] 클릭 (정책 0개 상태로)
Step 3 of 3 — Name, review, and create 페이지로 전환
  ↓ Role name 입력
  ↓ 하단 [Create role] 클릭
Roles 목록 페이지로 자동 복귀 + 상단에 녹색 성공 배너
  ↓ 방금 만든 farmos-gh-actions-deploy 행 클릭
Role 상세 페이지 (탭: Permissions / Trust relationships / Tags / Access Advisor / Revoke sessions)
  ↓ ⭐ "Trust relationships" 탭 클릭 (Permissions 옆)
Trust relationships 탭
  ↓ 우측 [Edit trust policy] 버튼 클릭
Edit trust policy JSON 편집 페이지
```

### 블록 B — 화면 레이아웃 (Edit trust policy)

```
┌────────────────────────────────────────────────────────────┐
│ Edit trust policy   farmos-gh-actions-deploy               │
├────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────┐  │
│  │ {                                                    │  │
│  │   "Version": "2012-10-17",                           │  │
│  │   ... 마법사가 자동 생성한 JSON ...                  │  │  ← ★ 전체 선택 후
│  │ }                                                    │  │     Ctrl+A → Delete
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  [Cancel]   [Update policy]                                │
└────────────────────────────────────────────────────────────┘
```

자동 생성 JSON을 **통째로 삭제** 후 아래 Trust Policy를 붙여넣기.

### 블록 C — 붙여넣을 Trust Policy (정확히 이대로)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::242201280878:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:Himedia-AI-01/FarmOS-Deploy-Test:ref:refs/heads/dev"
        }
      }
    }
  ]
}
```

붙여넣기 후 우측 하단 **[Update policy]** 클릭 → Trust relationships 탭으로 자동 복귀.

> **R9 완화 팁**: 최초 1회는 `"repo:Himedia-AI-01/FarmOS-Deploy-Test:*"` 와일드카드로 검증 후 `:ref:refs/heads/dev` 로 좁히기.

| Step 3 입력값 | 값 |
|---|---|
| Step 2-of-3 Permissions | (아무것도 체크하지 않음 → Next) |
| Step 3-of-3 Role name | `farmos-gh-actions-deploy` |
| Step 3-of-3 Description | `GitHub Actions OIDC role for farmos deploy (M4-A)` (권장) |
| Trust policy 자동 생성분 | 전체 삭제 후 위 JSON 붙여넣기 |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam get-role --role-name farmos-gh-actions-deploy \
  --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition' \
  --output json
```
**기대 출력**: `StringEquals` 와 `StringLike` 두 키 존재.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Permissions 단계에서 Next 버튼 비활성 | 콘솔 버그 — 검색창 한 번 클릭하면 풀림 | 검색창 클릭 → ESC → Next |
| "Role with name farmos-gh-actions-deploy already exists" | 이전 시도에서 만들어둠 | IAM → Roles → 기존 Role 삭제 후 재시도, 또는 그대로 사용하고 Trust policy 만 갱신 |
| Trust relationships 탭에 [Edit trust policy] 가 없음 | 다른 탭(Permissions) 보고 있음 | 탭 라벨 다시 확인 — 두 번째 탭 |
| JSON 붙여넣기 후 "Invalid policy syntax" | 이전 JSON 일부 남음 (Ctrl+A 미적용) | 편집기 안 클릭 → Ctrl+A → Delete → 다시 붙여넣기 |
| Update policy 후 sub 조건이 비어 보임 | StringLike 키 오타 (예: `:sub:` ←콜론 두 번) | 위 JSON 정확히 복붙 (탭/공백 변형 OK) |

---

## Step 4. GH Actions Role — Inline Policy 추가 (`farmos-deploy-permissions`)

### 블록 A — 화면 진입 경로

```
Roles 목록 → farmos-gh-actions-deploy 클릭 (Step 3 끝나고 자동 복귀했다면 그 페이지)
Role 상세 페이지
  ↓ ⭐ "Permissions" 탭 (가장 왼쪽 탭, 기본 활성)
Permissions 탭 화면
  ↓ 우측 상단 [Add permissions ▼] 드롭다운 버튼 클릭
드롭다운 메뉴 펼쳐짐 (3개 항목):
  • Attach policies
  • ⭐ Create inline policy   ← 클릭
  • (가끔 추가 항목)
Create policy (정책 편집기) 페이지로 전환
  ↓ 상단 탭 두 개: "Visual" / "JSON"
  ↓ ⭐ "JSON" 탭 클릭
```

### 블록 B — 화면 레이아웃 (Create policy → JSON 탭)

```
┌──────────────────────────────────────────────────────────────┐
│ Create policy                                                │
│  Specify permissions    Step 1 of 2                          │
├──────────────────────────────────────────────────────────────┤
│  Policy editor:    [ Visual ]  [JSON]    ← ★ JSON 클릭       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ {                                                      │  │
│  │   "Version": "2012-10-17",                             │  │
│  │   "Statement": []   ← 자동 더미. 전체 삭제 후 붙여넣기 │  │
│  │ }                                                      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  [Cancel]                                            [Next ▶]│
└──────────────────────────────────────────────────────────────┘
```

### 블록 C — 붙여넣을 Permissions Policy + Step 2 입력

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3DeployBucket",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::farmos-codedeploy-242201280878-ap-northeast-2",
        "arn:aws:s3:::farmos-codedeploy-242201280878-ap-northeast-2/*"
      ]
    },
    {
      "Sid": "CodeDeployTrigger",
      "Effect": "Allow",
      "Action": [
        "codedeploy:CreateDeployment",
        "codedeploy:GetDeployment",
        "codedeploy:GetDeploymentConfig",
        "codedeploy:RegisterApplicationRevision",
        "codedeploy:GetApplicationRevision"
      ],
      "Resource": [
        "arn:aws:codedeploy:ap-northeast-2:242201280878:application:farmos",
        "arn:aws:codedeploy:ap-northeast-2:242201280878:deploymentgroup:farmos/farmos-prod",
        "arn:aws:codedeploy:ap-northeast-2:242201280878:deploymentconfig:*"
      ]
    },
    {
      "Sid": "SSMParameterUpdate",
      "Effect": "Allow",
      "Action": ["ssm:PutParameter"],
      "Resource": "arn:aws:ssm:ap-northeast-2:242201280878:parameter/farmos/prod/image/tag"
    },
    {
      "Sid": "KMSDecryptForSSM",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:ap-northeast-2:242201280878:alias/aws/ssm"
    }
  ]
}
```

붙여넣기 후 [Next ▶] → Step 2 of 2:

| 필드 | 값 |
|---|---|
| Policy name | `farmos-deploy-permissions` |
| Description | `M4-A inline: S3+CodeDeploy+SSM put for GH Actions` |

[Create policy] 클릭 → Role 상세 Permissions 탭으로 자동 복귀.

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam list-role-policies --role-name farmos-gh-actions-deploy
```
**기대 출력**: `{ "PolicyNames": ["farmos-deploy-permissions"] }`

화면 검증: Permissions 탭의 Permissions policies 표에 `farmos-deploy-permissions` (Type: `Inline policy`) 1행.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Add permissions 드롭다운에 "Create inline policy" 없음 | 다른 탭(Trust relationships) 봤음 | 탭 다시 Permissions 확인 |
| JSON 탭 클릭했는데 Visual editor 로 자동 회귀 | 콘솔 버그 (저장된 JSON이 비어있을 때 발생) | JSON 영역 클릭 후 한 글자 입력 → Visual로 안 바뀜 → 그 후 Ctrl+A 후 정식 JSON 붙여넣기 |
| "MalformedPolicyDocument: Has prohibited field Resource" | Action 만 있고 Resource 누락된 Statement | 위 JSON 그대로 복붙 (4개 Sid 모두 Resource 있음) |
| Next 버튼 클릭 후 "policy has 0 statements" | Statement 배열이 비어 있음 | JSON 다시 붙여넣기 |
| Policy name 입력 후 Create 클릭 시 "must contain only alphanumeric and +=,.@-_" | 공백/특수문자 | `farmos-deploy-permissions` 정확히 |

---

## Step 5. EC2 Instance Profile — Trust entity (AWS service / EC2)

### 블록 A — 화면 진입 경로

```
IAM → Roles → [Create role]
Step 1 of 3 — Select trusted entity
  ↓ Trusted entity type 라디오 5개 중 ⭐ "AWS service" (1번째) 선택
  ↓ Web identity 폼 사라지고 "Use case" 박스가 새로 나타남
Use case 박스
  ↓ "Service or use case" 드롭다운 클릭 → "EC2" 검색해서 선택
  ↓ 그 아래 "Use case" 라디오 그룹이 나타남:
     ⦿ EC2                                    ← ★ 이거
     ◯ EC2 - Spot Instances
     ◯ EC2 - Scheduled Instances
     ◯ EC2 Role for AWS Systems Manager
     ◯ EC2 Role for AWS CodeDeploy
  ↓ [Next ▶]
```

> **주의**: "EC2 Role for AWS Systems Manager" 같은 사전 통합 옵션을 고르면 자동 첨부 정책이 달라집니다. 우리는 일반 **EC2** 를 선택하고 다음 단계에서 정책을 직접 첨부합니다.

### 블록 B — 화면 레이아웃 (Step 1 — AWS service 선택 후)

```
┌────────────────────────────────────────────────────────────┐
│ Step 1: Select trusted entity                              │
├────────────────────────────────────────────────────────────┤
│ Trusted entity type                                        │
│  ⦿ AWS service   ◯ AWS account   ◯ Web identity            │
│  ◯ SAML 2.0      ◯ Custom trust policy                     │
│                                                            │
│ ─ Use case ─                                               │
│ Service or use case *                                      │
│  ┌────────────────────────────────────────────────┐        │
│  │ EC2                                          ▼ │        │
│  └────────────────────────────────────────────────┘        │
│                                                            │
│ Use case *                                                 │
│  ⦿ EC2                                                     │ ★
│    Allows EC2 instances to call AWS services on your behalf│
│  ◯ EC2 - Spot Instances                                    │
│  ◯ EC2 - Scheduled Instances                               │
│  ◯ EC2 Role for AWS Systems Manager                        │
│  ◯ EC2 Role for AWS CodeDeploy                             │
│                                                            │
│ [Cancel]                                          [Next ▶] │
└────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드명 | 입력값 | 비고 |
|---|---|---|
| Trusted entity type | `AWS service` (1번째 라디오) | |
| Service or use case | `EC2` (드롭다운 검색) | |
| Use case | `EC2` (라디오 그룹의 1번째) | "EC2 Role for AWS Systems Manager" 같은 통합 옵션 비선택 |

### 블록 D — 검증 명령 + 기대 출력

이 단계는 화면 진행만 — [Next ▶] 클릭 시 Step 2 of 3 (Add permissions) 로 전환되어야 합니다.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| "Use case" 라디오가 안 보임 | "Service or use case" 드롭다운 미선택 | EC2 입력 후 엔터 또는 클릭으로 선택 |
| 자동 첨부 정책이 다름 | "EC2 Role for AWS Systems Manager" 같은 통합 옵션 선택 | 뒤로가기 → 일반 "EC2" 선택 |
| Service 드롭다운에 EC2 없음 | 검색어 오타 ("ec 2" 공백) | "EC2" 또는 "ec2" |

---

## Step 6. EC2 Instance Profile — Managed Policy 2개 첨부

### 블록 A — 화면 진입 경로

```
Step 5 의 [Next ▶] 클릭 직후
Step 2 of 3 — Add permissions 페이지로 자동 전환
  ↓ 검색바 ("Filter policies") 가 상단 중앙
```

### 블록 B — 화면 레이아웃 (Step 2 — Add permissions)

```
┌────────────────────────────────────────────────────────────┐
│ Step 2: Add permissions                                    │
├────────────────────────────────────────────────────────────┤
│ Permissions policies (Selected 0/1100+)                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Q  AmazonSSMManagedInstanceCore                      │  │ ← 검색
│  └──────────────────────────────────────────────────────┘  │
│   Filter by Type [All types ▼]                             │
│                                                            │
│  ☐ Policy name                       Type      Used as     │
│  ☑ AmazonSSMManagedInstanceCore     AWS managed Permissions│ ★체크
│  ☐ AmazonSSMServiceRolePolicy        AWS managed Service   │
│                                                            │
│  ↑↑ 체크 후 검색바 비우고 다시 검색:                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Q  AmazonEC2RoleforAWSCodeDeploy                     │  │
│  └──────────────────────────────────────────────────────┘  │
│  ☑ AmazonEC2RoleforAWSCodeDeploy    AWS managed Permissions│ ★체크
│                                                            │
│  ▼ Set permissions boundary - optional   (펼치지 않음)    │
│                                                            │
│ [Cancel]   [Previous ◀]                          [Next ▶] │
└────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 검색어 (정확히) | 체크할 정책 | 비고 |
|---|---|---|
| `AmazonSSMManagedInstanceCore` | ☑ AmazonSSMManagedInstanceCore | Session Manager + SSM agent |
| `AmazonEC2RoleforAWSCodeDeploy` | ☑ AmazonEC2RoleforAWSCodeDeploy | CodeDeploy agent S3 read |

> 검색바 우측 상단의 **Selected (2)** 카운터가 2 가 되어야 합니다 — 검색바를 비워도 체크된 항목은 유지됩니다.

[Next ▶] → Step 3 of 3:

| 필드 | 값 |
|---|---|
| Role name | `farmos-ec2-instance` |
| Description | `EC2 instance profile for farmos (SSM + CodeDeploy agent + runtime)` |

[Create role] 클릭. 콘솔에서 Create role 시 **Instance Profile 도 자동 생성** (이름 동일).

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam list-attached-role-policies --role-name farmos-ec2-instance \
  --query 'AttachedPolicies[].PolicyName' --output text
```
**기대 출력**: `AmazonSSMManagedInstanceCore   AmazonEC2RoleforAWSCodeDeploy` (탭 구분)

```bash
aws iam get-instance-profile --instance-profile-name farmos-ec2-instance \
  --query 'InstanceProfile.Arn' --output text
```
**기대 출력**: `arn:aws:iam::242201280878:instance-profile/farmos-ec2-instance` → 캐치 테이블 #3 기록.

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| 검색바 비웠더니 체크가 풀림 | 콘솔이 "필터된 결과만 본다" 모드 | 상단 [Selected only] 토글로 확인 가능. 체크 자체는 유지됨 |
| AmazonEC2RoleforAWSCodeDeploy 검색 결과 0건 | 오타 (RoleForAWSCodeDeploy 대소문자) | 정확히 `AmazonEC2RoleforAWSCodeDeploy` (`for` 소문자) |
| Create role 후 Instance Profile 안 보임 | CLI로 만들었거나 Role 만 만든 경우 | 콘솔로 만들면 자동. CLI 였다면 `aws iam create-instance-profile` + `add-role-to-instance-profile` 추가 |
| Selected 카운터가 1 | 검색 후 체크박스 1개만 누름 | 검색바 비우고 두 번째 정책 검색해서 체크 |

---

## Step 7. EC2 Instance Profile — Inline Policy 추가 (`farmos-ec2-runtime`)

### 블록 A — 화면 진입 경로

```
Step 6 끝나면 자동으로 Roles 목록으로 복귀
  ↓ farmos-ec2-instance 행 클릭
Role 상세 페이지
  ↓ ⭐ "Permissions" 탭 (기본 활성)
Permissions 탭
  ↓ 우측 상단 [Add permissions ▼] 드롭다운 클릭
드롭다운 메뉴:
  • Attach policies
  • ⭐ Create inline policy   ← 클릭
Create policy 페이지
  ↓ 상단 [Visual] / [JSON] 탭 중 ⭐ JSON 클릭
```

### 블록 B — 화면 레이아웃

블록 B 는 Step 4 와 동일 구조 (Create policy → JSON 탭).

### 블록 C — 붙여넣을 Inline Policy + Step 2 입력

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ParameterStoreRead",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      "Resource": "arn:aws:ssm:ap-northeast-2:242201280878:parameter/farmos/prod/*"
    },
    {
      "Sid": "KMSDecryptForSSM",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:ap-northeast-2:242201280878:alias/aws/ssm"
    },
    {
      "Sid": "S3DeployRead",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::farmos-codedeploy-242201280878-ap-northeast-2",
        "arn:aws:s3:::farmos-codedeploy-242201280878-ap-northeast-2/*"
      ]
    },
    {
      "Sid": "GHCRImagePull",
      "Effect": "Allow",
      "Action": "logs:CreateLogStream",
      "Resource": "*"
    }
  ]
}
```

[Next ▶] → Step 2 of 2:

| 필드 | 값 |
|---|---|
| Policy name | `farmos-ec2-runtime` |
| Description | `M4-A EC2 inline: SSM read + KMS decrypt + S3 bundle read` |

[Create policy] → Permissions 탭으로 자동 복귀.

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam list-role-policies --role-name farmos-ec2-instance
```
**기대 출력**: `{ "PolicyNames": ["farmos-ec2-runtime"] }`

```bash
aws iam list-attached-role-policies --role-name farmos-ec2-instance \
  --query 'length(AttachedPolicies)'
```
**기대 출력**: `2` (Managed) + Inline 1 = 화면 표에 3행

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Add permissions 드롭다운에 "Create inline policy" 가 비활성 | Role 상세가 아니라 Roles 목록에서 그 Role 선택만 한 상태 | Role 이름 클릭해서 상세 페이지로 진입 |
| JSON 붙여넣기 후 "Resource: invalid ARN" | `parameter/farmos/prod/*` 의 슬래시/별표 인코딩 깨짐 | 위 JSON 그대로 복사 붙여넣기 (변형 없이) |
| 정책 3개 보여야 하는데 2개만 | Inline policy 가 다른 Role 에 붙음 | Roles → farmos-ec2-instance → Permissions 탭 다시 확인 |
| Permissions 탭에 Inline 정책 표시 안 됨 | 페이지 새로고침 안 됨 | F5 새로고침 |
| Action `logs:CreateLogStream` 가 무관해 보임 | 향후 CloudWatch Logs 연동 대비 — 지금은 사용 안 해도 무해 | 그대로 둠 |

---

## Step 8. CodeDeploy Service Role (`farmos-codedeploy-svc`)

### 블록 A — 화면 진입 경로

```
IAM → Roles → [Create role]
Step 1 of 3 — Select trusted entity
  ↓ Trusted entity type: ⦿ AWS service
  ↓ Use case 박스 출현
  ↓ "Service or use case" 드롭다운에서 "CodeDeploy" 검색해 선택
  ↓ "Use case" 라디오 3개 중 ⭐ "CodeDeploy" (1번째) 선택
     ⦿ CodeDeploy            ← ★ EC2/On-premises 용
     ◯ CodeDeploy for ECS
     ◯ CodeDeploy for Lambda
  ↓ [Next ▶]
Step 2 of 3 — Add permissions
  ↓ ★ AWSCodeDeployRole 가 자동 체크되어 있음 (회색, 변경 불가)
  ↓ [Next ▶]
Step 3 of 3 — Name, review, and create
  ↓ Role name 입력 → [Create role]
```

### 블록 B — 화면 레이아웃 (Step 1 — Use case 선택 후)

```
┌────────────────────────────────────────────────────────────┐
│ Step 1: Select trusted entity                              │
├────────────────────────────────────────────────────────────┤
│ Trusted entity type:  ⦿ AWS service                        │
│                                                            │
│ Service or use case *                                      │
│  ┌────────────────────────────────────────────────┐        │
│  │ CodeDeploy                                   ▼ │        │
│  └────────────────────────────────────────────────┘        │
│                                                            │
│ Use case *                                                 │
│  ⦿ CodeDeploy                                              │ ★
│    Allows CodeDeploy to call AWS services on your behalf   │
│  ◯ CodeDeploy for ECS                                      │
│  ◯ CodeDeploy for Lambda                                   │
│                                                            │
│ [Cancel]                                          [Next ▶] │
└────────────────────────────────────────────────────────────┘
```

Step 2 화면에서 자동으로 첨부되는 Managed Policy:

```
┌─────────────────────────────────────────────────────────┐
│ Permissions policies                                    │
│  ☑ AWSCodeDeployRole   AWS managed   (자동, 변경 불가)  │
└─────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Trusted entity type | `AWS service` |
| Service or use case | `CodeDeploy` |
| Use case | `CodeDeploy` (ECS / Lambda 아님) |
| Role name | `farmos-codedeploy-svc` |
| Description | `CodeDeploy service role for farmos (EC2/On-premises)` |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws iam get-role --role-name farmos-codedeploy-svc \
  --query 'Role.Arn' --output text
```
**기대 출력**: `arn:aws:iam::242201280878:role/farmos-codedeploy-svc` → 캐치 테이블 #4 기록.

```bash
aws iam list-attached-role-policies --role-name farmos-codedeploy-svc \
  --query 'AttachedPolicies[].PolicyName' --output text
```
**기대 출력**: `AWSCodeDeployRole`

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Use case 라디오에 CodeDeploy for ECS / Lambda 가 안 보임 | 정상 — 일부 리전에서 옵션 1개만 표시 | 그대로 [Next ▶] |
| 자동 첨부 정책이 `AWSCodeDeployRoleForLambda` | Use case "CodeDeploy for Lambda" 잘못 선택 | 뒤로 → "CodeDeploy" 라디오 다시 선택 |
| CodeDeploy 가 Service 드롭다운에 안 뜸 | "Code Deploy" 공백 입력 | `CodeDeploy` (한 단어) |
| 검증 시 Role ARN 가 다른 계정 표시 | CLI profile 다름 | `aws --profile farmos-admin iam get-role ...` 또는 `aws sts get-caller-identity` |

> ✅ Phase 1 완료. ARN 캐치 테이블 #1~#4 모두 채워졌는지 확인.

---

# §3. Phase 2 — 인프라 리소스 (6단계)

## Step 9. S3 Deploy 버킷 생성

### 블록 A — 화면 진입 경로

```
AWS Console
  ↓ 우상단 리전 [Asia Pacific (Seoul) ap-northeast-2] 확인  ★
  ↓ 상단 검색바에 "S3" 입력 → S3 (Scalable Storage in the Cloud) 클릭
S3 서비스 페이지 (https://s3.console.aws.amazon.com/)
  ↓ 좌측 메뉴 "General purpose buckets" 클릭 (기본 활성일 가능성 높음)
Buckets 목록 페이지
  ↓ 우측 상단 [Create bucket] 주황 버튼 클릭
Create bucket 페이지로 전환 (긴 단일 페이지, 옵션 그룹 6~8개)
```

### 블록 B — 화면 레이아웃 (Create bucket — 옵션 그룹별)

```
┌─────────────────────────────────────────────────────────────┐
│ Create bucket                                               │
├─────────────────────────────────────────────────────────────┤
│ ─── General configuration ───                               │
│ Bucket name *                                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ farmos-codedeploy-242201280878-ap-northeast-2         │  │
│  └───────────────────────────────────────────────────────┘  │
│ AWS Region                                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Asia Pacific (Seoul) ap-northeast-2               ▼   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│ ─── Object Ownership ───                                    │
│  ⦿ ACLs disabled (recommended)   ★ 기본값 유지              │
│  ◯ ACLs enabled                                             │
│                                                             │
│ ─── Block Public Access settings for this bucket ───        │
│  ☑ Block all public access                                  │
│   ↳ 펼치면 4 하위 체크박스 — 모두 ☑ 유지 ★                  │
│   ☑ Block public access through new ACLs                    │
│   ☑ Block public access through any ACLs                    │
│   ☑ Block public access through new public bucket policies  │
│   ☑ Block public access through any public bucket policies  │
│                                                             │
│ ─── Bucket Versioning ───                                   │
│  ◯ Disable                                                  │
│  ⦿ Enable     ★                                             │
│                                                             │
│ ─── Tags - optional ─── (건너뛰기)                          │
│                                                             │
│ ─── Default encryption ───                                  │
│  ⦿ Server-side encryption with Amazon S3 managed keys (SSE-S3) │ ★ 기본
│  ◯ SSE-KMS                                                  │
│  ◯ DSSE-KMS                                                 │
│                                                             │
│ ─── Advanced settings ─── (Object Lock 등, 모두 기본값)     │
│                                                             │
│ [Cancel]                                  [Create bucket]   │
└─────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 옵션 그룹 | 필드 | 입력값 |
|---|---|---|
| General configuration | Bucket name | `farmos-codedeploy-242201280878-ap-northeast-2` |
| General configuration | AWS Region | `Asia Pacific (Seoul) ap-northeast-2` |
| Object Ownership | (라디오) | `ACLs disabled (recommended)` (기본) |
| Block Public Access | (체크박스) | `Block all public access` ☑ + 하위 4개 ☑ 유지 |
| Bucket Versioning | (라디오) | `Enable` ★ (기본은 Disable이므로 변경 필수) |
| Default encryption | (라디오) | `SSE-S3 (Amazon S3 managed keys)` (기본) |
| Tags / Object Lock / Advanced | — | 기본값 유지 |

### 블록 D — 검증 명령 + 기대 출력

```bash
ACCT=242201280878
BUCKET=farmos-codedeploy-${ACCT}-ap-northeast-2

aws s3api head-bucket --bucket $BUCKET                       # exit 0
aws s3api get-bucket-versioning --bucket $BUCKET \
  --query 'Status' --output text
# → Enabled
aws s3api get-public-access-block --bucket $BUCKET \
  --query 'PublicAccessBlockConfiguration' --output json
# 4개 키 모두 true
```
캐치 테이블 #5 = `farmos-codedeploy-242201280878-ap-northeast-2`

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| "Bucket name already exists" | 다른 계정이 이미 사용 (S3는 글로벌 네임스페이스) | 본 컨벤션은 ACCOUNT_ID 포함하므로 충돌 없음. 진짜 충돌이면 suffix 추가 |
| Region 드롭다운이 us-east-1 | 우상단 콘솔 리전과 무관, 폼 자체는 별도 | 드롭다운에서 ap-northeast-2 명시 선택 |
| Block Public Access 체크 풀림 | 사용자가 무심코 클릭 | 4개 모두 ☑ 다시 |
| Versioning Enable 안 했더니 CodeDeploy 가 revision 못 추적 | 기본값이 Disable | 버킷 → Properties 탭 → Bucket Versioning → Edit → Enable |
| Encryption 옵션이 SSE-KMS 로 자동 | 일부 계정에서 기본값 변경됨 | SSE-S3 로 다시 |

---

## Step 10. S3 버킷 — Versioning + Public Block 상태 재확인

> 콘솔에서 Step 9 작성 시 한 번에 처리되지만, **콘솔 폼이 옵션을 누락하는 경우가 잦아** 별도 검증 단계로 분리.

### 블록 A — 화면 진입 경로

```
S3 → Buckets → farmos-codedeploy-242201280878-ap-northeast-2 클릭
버킷 상세 페이지 (탭: Objects / Properties / Permissions / Metrics / Management / Access Points)
  ↓ ⭐ "Properties" 탭 클릭
Properties 탭 페이지
  ↓ "Bucket Versioning" 섹션 — 우측 [Edit] 버튼
  ↓ Enable 라디오 확인 → [Save changes]
다시 버킷 상세
  ↓ ⭐ "Permissions" 탭 클릭
  ↓ "Block public access (bucket settings)" 섹션 — 우측 [Edit]
  ↓ 4개 체크박스 모두 ☑ 확인 → [Save changes]
```

### 블록 B — 화면 레이아웃 (Properties 탭 / Versioning)

```
┌────────────────────────────────────────────────────────┐
│ Bucket Versioning                              [Edit]  │
├────────────────────────────────────────────────────────┤
│ Bucket Versioning: Enabled  ★                          │
│ Multi-factor authentication (MFA) delete: Disabled     │
└────────────────────────────────────────────────────────┘
```

### 블록 C — 확인 항목

| 탭 | 섹션 | 기대 상태 |
|---|---|---|
| Properties | Bucket Versioning | `Enabled` |
| Permissions | Block public access | `Block all public access: On` (4개 모두 On) |
| Properties | Default encryption | `Server-side encryption with Amazon S3 managed keys (SSE-S3)` |

### 블록 D — 검증 명령 (Step 9 와 동일)

```bash
aws s3api get-bucket-versioning --bucket $BUCKET --query 'Status' --output text
# → Enabled
aws s3api get-public-access-block --bucket $BUCKET \
  --query 'PublicAccessBlockConfiguration.BlockPublicPolicy' --output text
# → True
```

또는 CLI 일괄 적용:
```bash
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket $BUCKET \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Versioning이 "Suspended" | Step 9 라디오 미클릭 | Properties → Edit → Enable |
| Block Public 4개 중 일부 Off | 콘솔에서 토글 클릭 누락 | Permissions → Edit → 4개 모두 On |
| Edit 버튼 비활성 | 다른 IAM User로 로그인 (Read 권한만) | Admin 사용자로 다시 로그인 |
| Save changes 후 "Failed to save" | 버킷 정책과 충돌 (보통 없음) | F5 새로고침 후 재시도 |

---

## Step 11. S3 버킷 — Lifecycle Rule (`ArchiveAndExpireDeployBundles`)

### 블록 A — 화면 진입 경로

```
S3 → Buckets → farmos-codedeploy-242201280878-ap-northeast-2
버킷 상세
  ↓ ⭐ "Management" 탭 클릭 (탭 7개 중 5번째)
Management 탭
  ↓ "Lifecycle rules" 섹션 (가장 위)
  ↓ [Create lifecycle rule] 버튼 클릭
Create lifecycle rule 페이지로 전환
```

### 블록 B — 화면 레이아웃 (Create lifecycle rule)

```
┌──────────────────────────────────────────────────────────────┐
│ Create lifecycle rule                                        │
├──────────────────────────────────────────────────────────────┤
│ Lifecycle rule configuration                                 │
│  Lifecycle rule name *                                       │
│   ┌──────────────────────────────────────────────────────┐   │
│   │ ArchiveAndExpireDeployBundles                        │   │
│   └──────────────────────────────────────────────────────┘   │
│  Status: ⦿ Enabled  ◯ Disabled                               │
│                                                              │
│ Choose a rule scope                                          │
│  ⦿ Apply to all objects in the bucket   ★                    │
│  ◯ Limit the scope using filters                             │
│  ☑ I acknowledge that this rule applies to all objects ★    │
│                                                              │
│ Lifecycle rule actions (체크박스 4개):                       │
│  ☑ Move current versions of objects between storage classes  │ ★
│  ☐ Move noncurrent versions of objects between storage classes│
│  ☑ Expire current versions of objects                        │ ★
│  ☐ Permanently delete noncurrent versions of objects         │
│  ☐ Delete expired object delete markers / incomplete uploads │
│                                                              │
│ Transition current versions of objects between storage classes│
│  Days after object creation: 30                              │
│  Storage class: Standard-IA                          ▼       │
│                                                              │
│ Expire current versions of objects                           │
│  Days after object creation: 90                              │
│                                                              │
│  ─── Timeline preview (콘솔이 자동 그려줌) ───               │
│   Day 0: Standard → Day 30: Standard-IA → Day 90: Expired    │
│                                                              │
│ [Cancel]                              [Create rule]          │
└──────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Lifecycle rule name | `ArchiveAndExpireDeployBundles` |
| Status | `Enabled` |
| Rule scope | `Apply to all objects in the bucket` |
| Acknowledge 체크박스 | ☑ |
| Action 1 | ☑ Move current versions of objects between storage classes |
| Action 2 | ☑ Expire current versions of objects |
| Transition: Days | `30` |
| Transition: Storage class | `Standard-IA` |
| Expire: Days | `90` |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws s3api get-bucket-lifecycle-configuration --bucket $BUCKET --output json
```
**기대 출력**:
```json
{ "Rules": [ { "ID": "ArchiveAndExpireDeployBundles", "Status": "Enabled",
  "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}],
  "Expiration": {"Days": 90} } ] }
```

CLI 일괄 적용 대안:
```bash
cat > /tmp/lifecycle.json <<'EOF'
{ "Rules": [ { "ID": "ArchiveAndExpireDeployBundles", "Status": "Enabled",
  "Filter": {"Prefix": ""},
  "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}],
  "Expiration": {"Days": 90} } ] }
EOF
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET \
  --lifecycle-configuration file:///tmp/lifecycle.json
```

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Acknowledge 체크박스 안 보임 | 위에서 "Apply to all objects" 라디오 선택 안 함 | 라디오 확인 |
| Storage class 드롭다운 옵션 많음 (10+) | 정상 — Glacier/Deep Archive 등 | `Standard-IA` 선택 |
| Days 입력 후 [Create rule] 비활성 | 두 액션의 Days 가 모순 (Expire < Transition) | Transition=30 < Expire=90 |
| 룰 생성 후 Status: Disabled | 라디오 미선택 | Management → 룰 클릭 → Edit → Enabled |
| "ArchiveAndExpireDeployBundles already exists" | 중복 시도 | 그대로 두거나 삭제 후 재생성 |

---

## Step 12. CodeDeploy Application 생성

### 블록 A — 화면 진입 경로

```
AWS Console → 검색바 "CodeDeploy" → CodeDeploy 클릭
CodeDeploy 페이지
  ↓ 좌측 메뉴 "Deploy" 그룹 → "Applications" 클릭
Applications 목록
  ↓ 우측 상단 [Create application] 주황 버튼 클릭
Create application 페이지
```

### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────┐
│ Create application                                       │
├──────────────────────────────────────────────────────────┤
│ Application name *                                       │
│  ┌────────────────────────────────────────────────────┐  │
│  │ farmos                                             │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│ Compute platform *                                       │
│  ⦿ EC2/On-premises    ★                                  │
│  ◯ AWS Lambda                                            │
│  ◯ Amazon ECS                                            │
│                                                          │
│ [Cancel]                          [Create application]   │
└──────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Application name | `farmos` |
| Compute platform | `EC2/On-premises` |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws deploy get-application --application-name farmos \
  --query 'application.[applicationName,computePlatform]' --output text
# → farmos   Server
```
캐치 테이블 #6 = `farmos`

CLI 대안:
```bash
aws deploy create-application --application-name farmos --compute-platform Server
```

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Compute platform이 라디오 아닌 드롭다운 | 콘솔 변종 | EC2/On-premises 선택 |
| `computePlatform: Lambda` | Compute platform 잘못 선택 | Application 삭제 후 재생성 (이미 만들어진 application은 platform 변경 불가) |
| Create application 클릭 후 권한 오류 | IAM User에 `codedeploy:CreateApplication` 없음 | Admin User로 로그인 |
| 이미 존재 | 같은 이름 application 있음 | 그대로 사용 |

---

## Step 13. DeploymentGroup (`farmos-prod`, In-place, Auto-Rollback) — 클릭 단위 디테일

### 블록 A — 화면 진입 경로

```
CodeDeploy → Applications → ⭐ "farmos" 클릭
farmos Application 상세 페이지
  ↓ "Deployment groups" 탭 (Application 페이지의 두 번째 탭, 기본 활성일 수 있음)
  ↓ 우측 [Create deployment group] 주황 버튼 클릭
Create deployment group 페이지 (긴 단일 페이지, 옵션 그룹 6개)
```

### 블록 B — 화면 레이아웃 (Create deployment group)

```
┌──────────────────────────────────────────────────────────────┐
│ Create deployment group                                      │
├──────────────────────────────────────────────────────────────┤
│ ─── Deployment group name ───                                │
│  Deployment group name *                                     │
│   ┌────────────────────────────────────────────────────┐     │
│   │ farmos-prod                                        │     │
│   └────────────────────────────────────────────────────┘     │
│                                                              │
│ ─── Service role ───                                         │
│  Service role *                                              │
│   ┌────────────────────────────────────────────────────┐     │
│   │ arn:aws:iam::242201280878:role/farmos-codedeploy-svc▼│   │
│   └────────────────────────────────────────────────────┘     │
│   ※ Step 8에서 만든 역할이 여기 드롭다운에 보여야 함         │
│                                                              │
│ ─── Deployment type ───                                      │
│  ⦿ In-place      ★                                           │
│  ◯ Blue/green                                                │
│                                                              │
│ ─── Environment configuration ───                            │
│  ☑ Amazon EC2 instances        ★ (체크박스, 라디오 아님)     │
│  ☐ Amazon EC2 Auto Scaling groups                            │
│  ☐ On-premises instances                                     │
│                                                              │
│  ── 펼쳐진 Tag groups 영역 ──                                │
│  Tag group 1                                                 │
│   ┌──────────────┬──────────────┬─────────┐                  │
│   │ Key          │ Value        │ Type    │                  │
│   ├──────────────┼──────────────┼─────────┤                  │
│   │ App          │ farmos       │ Key=Val │ ★ Tag 1          │
│   │ Environment  │ prod         │ Key=Val │ ★ Tag 2          │
│   └──────────────┴──────────────┴─────────┘                  │
│  [+ Add tag] (Tag group 1 안에서 다음 행 추가)               │
│                                                              │
│  ※ ⚠️ "Add tag group" (Tag group 2 신설) 버튼은 ★누르지 마세요 │
│     같은 Tag group 안 = AND 조건                              │
│     Tag group이 분리 = OR 조건 (둘 중 하나만 매치되어도 배포) │
│                                                              │
│ ─── Agent configuration with AWS Systems Manager ───         │
│  (선택) — 기본값 유지                                        │
│                                                              │
│ ─── Deployment configuration ───                             │
│  Deployment configuration *                                  │
│   ┌────────────────────────────────────────────────────┐     │
│   │ CodeDeployDefault.AllAtOnce                      ▼ │     │
│   └────────────────────────────────────────────────────┘     │
│                                                              │
│ ─── Load balancer ───                                        │
│  ☐ Enable load balancing   ★ 체크 해제 유지 (CloudFlare 사용)│
│                                                              │
│ ─── Advanced - optional ───  (펼치기 ▼)                      │
│   Triggers — (skip)                                          │
│   Alarms — (skip)                                            │
│   Rollbacks                                                  │
│    ☑ Roll back when a deployment fails    ★                  │
│    ☐ Roll back when alarm thresholds are met                 │
│                                                              │
│ [Cancel]                              [Create deployment group] │
└──────────────────────────────────────────────────────────────┘
```

### 블록 C — 입력값 표

| 옵션 그룹 | 필드 | 값 | 비고 |
|---|---|---|---|
| Deployment group name | (입력) | `farmos-prod` | |
| Service role | (드롭다운) | `farmos-codedeploy-svc` | Step 8 결과물. 안 보이면 Step 8 미완료 |
| Deployment type | (라디오) | `In-place` | Blue/green 아님 |
| Environment configuration | (체크박스) | ☑ `Amazon EC2 instances` | 다른 두 개 ☐ |
| Tag group 1 / Tag 1 | Key/Value/Type | `App` / `farmos` / `Key=Value` | |
| Tag group 1 / Tag 2 | Key/Value/Type | `Environment` / `prod` / `Key=Value` | **같은 Tag group 안에 추가 (★ AND 조건)** |
| Add tag group 버튼 | — | **★ 누르지 말 것** | Tag group 분리 = OR (불일치) |
| Deployment configuration | (드롭다운) | `CodeDeployDefault.AllAtOnce` | |
| Load balancer | Enable load balancing | ☐ (체크 해제) | CloudFlare 사용 |
| Advanced > Rollbacks | (체크박스) | ☑ `Roll back when a deployment fails` | |

### 블록 D — 검증 명령 + 기대 출력

```bash
aws deploy get-deployment-group \
  --application-name farmos --deployment-group-name farmos-prod \
  --query 'deploymentGroupInfo.[deploymentGroupName,deploymentStyle.deploymentType,
           autoRollbackConfiguration.enabled,
           ec2TagSet.ec2TagSetList[0]]' --output json
```
**기대 출력**:
```json
[ "farmos-prod", "IN_PLACE", true,
  [ {"Key":"App","Value":"farmos","Type":"KEY_AND_VALUE"},
    {"Key":"Environment","Value":"prod","Type":"KEY_AND_VALUE"} ] ]
```

> ★ Tag set이 **2-tag in 1 group** 으로 묶여 있어야 합니다 (AND 조건). 만약 `ec2TagSetList[0]` 에 1개, `[1]` 에 1개로 분리되어 있으면 OR 조건이라 EC2 매칭이 깨집니다.

CLI 대안 (한 줄로 정확히 AND):
```bash
ACCT=242201280878
aws deploy create-deployment-group \
  --application-name farmos --deployment-group-name farmos-prod \
  --service-role-arn arn:aws:iam::${ACCT}:role/farmos-codedeploy-svc \
  --deployment-config-name CodeDeployDefault.AllAtOnce \
  --ec2-tag-set "ec2TagSetList=[[{Key=App,Value=farmos,Type=KEY_AND_VALUE},{Key=Environment,Value=prod,Type=KEY_AND_VALUE}]]" \
  --auto-rollback-configuration "enabled=true,events=DEPLOYMENT_FAILURE"
```
캐치 테이블 #7 = `farmos-prod`

### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| 배포 실행 시 "No instances found" | Tag group 분리 (OR 조건) — App=farmos OR Environment=prod 매치되는 EC2가 없음 | Deployment group 편집 → Tag group 1개로 합치기 (Tag 2개 모두 첫 그룹 안에) |
| Service role 드롭다운에 farmos-codedeploy-svc 없음 | Step 8 미완료 또는 Trust entity가 codedeploy.amazonaws.com 아님 | Step 8 다시 |
| Deployment configuration 드롭다운에 AllAtOnce 안 보임 | 미관리 deployment config | 기본 3개(AllAtOnce/HalfAtATime/OneAtATime) 중 하나 — 페이지 새로고침 |
| Load balancer 체크 해제 못함 | 체크박스 disabled | "Enable load balancing" 헤더의 체크박스가 맞는지 확인 |
| Advanced 펼쳤는데 Rollbacks 섹션 안 보임 | 콘솔 변종 — 일부 리전에서 별도 페이지 | Deployment group 생성 후 편집 → "Rollback configuration" 섹션 |

---

## Step 14. Parameter Store 28키 시드

> **D9**: backend/.env.example 1:1 정합. 모든 SecureString은 KMS `alias/aws/ssm` (AWS 관리 키, 무료) 사용.
> **REPLACE_ME 표시 키**: 운영자가 발급 후 `--overwrite`로 채워야 함 (10개).
> **콘솔로 28개 만들기는 비효율** — CloudShell 또는 로컬 CLI 권장. 콘솔 폼 구조는 참고용으로 아래 14.1 에 정리.

### 14.1 (참고) 콘솔 단일 파라미터 생성 화면 구조

#### 블록 A — 화면 진입 경로

```
AWS Console → 검색바 "Systems Manager" → Systems Manager 클릭
Systems Manager 페이지
  ↓ 좌측 메뉴 "Application Management" 그룹 펼치기
  ↓ ⭐ "Parameter Store" 클릭
Parameter Store 페이지
  ↓ 우측 상단 [Create parameter] 주황 버튼 클릭
Create parameter 페이지
```

#### 블록 B — 화면 레이아웃

```
┌────────────────────────────────────────────────────────────┐
│ Create parameter                                           │
├────────────────────────────────────────────────────────────┤
│ Name *                                                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ /farmos/prod/db/password                             │  │
│  └──────────────────────────────────────────────────────┘  │
│  ※ 슬래시로 시작, 계층 구조 권장                          │
│                                                            │
│ Description - optional                                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Postgres password (rotate by 2026-Q3)                │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│ Tier *                                                     │
│  ⦿ Standard   ★ (4KB, 무료, 10,000개 한도)                 │
│  ◯ Advanced   (8KB, $0.05/param/월, Policy 가능)           │
│  ◯ Intelligent-Tiering                                     │
│                                                            │
│ Type *                                                     │
│  ◯ String                                                  │
│  ◯ StringList                                              │
│  ⦿ SecureString    ★ (이번 키처럼 비밀번호인 경우)         │
│                                                            │
│ ── SecureString 선택 시 펼침 ──                            │
│ KMS key source                                             │
│  ⦿ My current account   ★                                  │
│  ◯ Another account                                         │
│ KMS Key ID                                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ alias/aws/ssm                                    ▼   │  │ ★ 무료 키
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│ Data type                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ text                                             ▼   │  │ ★ 기본
│  └──────────────────────────────────────────────────────┘  │
│  (aws:ec2:image 는 AMI ID용 — 사용 안 함)                  │
│                                                            │
│ Value *                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ <비밀번호 24자 이상>                                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│ [Cancel]                            [Create parameter]     │
└────────────────────────────────────────────────────────────┘
```

#### 블록 C — 28개를 콘솔로 만들 때 공통 입력값

| 필드 | 공통값 |
|---|---|
| Tier | `Standard` |
| KMS key source | `My current account` |
| KMS Key ID (SecureString만) | `alias/aws/ssm` |
| Data type | `text` |
| Type | 키별로 다름 (아래 표 참조) |

**28키는 콘솔 클릭으로 28번 반복하기엔 비효율적** — CloudShell 으로 14.2 의 명령 일괄 실행을 강력 권장.

### 14.2 CloudShell / 로컬 CLI 일괄 시드 (★ 권장 — v1 그대로 보존)

```bash
REGION=ap-northeast-2

# ─── db (2) ─────────────────────────────────
aws ssm put-parameter --name /farmos/prod/db/password \
  --type SecureString --tier Standard \
  --description "Postgres password (rotate by 2026-Q3)" \
  --key-id alias/aws/ssm \
  --value "$(openssl rand -base64 24)" --region $REGION

DB_PW=$(aws ssm get-parameter --name /farmos/prod/db/password \
  --with-decryption --query 'Parameter.Value' --output text --region $REGION)
aws ssm put-parameter --name /farmos/prod/db/url \
  --type SecureString --tier Standard \
  --description "DATABASE_URL — config.py:19" \
  --key-id alias/aws/ssm \
  --value "postgresql+asyncpg://farmos:${DB_PW}@postgres:5432/farmos" \
  --region $REGION

# ─── jwt (1) ────────────────────────────────
aws ssm put-parameter --name /farmos/prod/jwt/secret_key \
  --type SecureString --tier Standard \
  --description "JWT_SECRET_KEY — FastAPI signing" \
  --key-id alias/aws/ssm \
  --value "$(openssl rand -hex 32)" --region $REGION

# ─── cors (1) ───────────────────────────────
aws ssm put-parameter --name /farmos/prod/cors/origins \
  --type String --tier Standard \
  --description "CORS_ORIGINS — JSON array (list[str])" \
  --value '["http://iot.lilpa.moe","https://iot.lilpa.moe"]' --region $REGION

# ─── litellm (3) ────────────────────────────
aws ssm put-parameter --name /farmos/prod/litellm/url \
  --type String --tier Standard \
  --description "LITELLM_URL — all LLM calls go through this proxy (D9-B)" \
  --value "https://litellm.lilpa.moe/v1" --region $REGION

aws ssm put-parameter --name /farmos/prod/litellm/api_key \
  --type SecureString --tier Standard \
  --description "LITELLM_API_KEY (rotate by 2026-Q3)" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION   # ⚠️ LiteLLM 콘솔에서 발급 후 --overwrite

aws ssm put-parameter --name /farmos/prod/litellm/model \
  --type String --tier Standard \
  --description "LITELLM_MODEL default" \
  --value "gpt-oss-20b" --region $REGION

# ─── llm (6) ────────────────────────────────
aws ssm put-parameter --name /farmos/prod/llm/upstage_key \
  --type SecureString --tier Standard \
  --description "UPSTAGE_API_KEY — langchain-upstage direct (D9-D)" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION   # ⚠️ console.upstage.ai 발급 후 --overwrite

aws ssm put-parameter --name /farmos/prod/llm/reasoning_effort \
  --type String --tier Standard --value "minimal" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/provider \
  --type String --tier Standard --value "litellm" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/model \
  --type String --tier Standard --value "llama3.1:8b" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/embed_model \
  --type String --tier Standard --value "voyage-3.5" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/ai_agent_model \
  --type String --tier Standard --value "openai/gpt-5-mini" --region $REGION

# ─── groq (3) ───────────────────────────────
aws ssm put-parameter --name /farmos/prod/groq/api_key \
  --type SecureString --tier Standard \
  --description "GROQ_API_KEY — Whisper STT" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION   # ⚠️ groq.com 발급 후 --overwrite

aws ssm put-parameter --name /farmos/prod/groq/stt_url \
  --type String --tier Standard \
  --value "https://api.groq.com/openai/v1/audio/transcriptions" --region $REGION

aws ssm put-parameter --name /farmos/prod/groq/stt_model \
  --type String --tier Standard --value "whisper-large-v3" --region $REGION

# ─── iot_relay (3) ──────────────────────────
aws ssm put-parameter --name /farmos/prod/iot_relay/base_url \
  --type String --tier Standard \
  --description "IOT_RELAY_BASE_URL — N100 외부 호스트" \
  --value "http://relay.lilpa.moe:9000" --region $REGION

aws ssm put-parameter --name /farmos/prod/iot_relay/api_key \
  --type SecureString --tier Standard \
  --description "IOT_RELAY_API_KEY — Relay 공유 시크릿" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION   # ⚠️ Relay 서버에서 발급 후 --overwrite

aws ssm put-parameter --name /farmos/prod/iot_relay/bridge_enabled \
  --type String --tier Standard \
  --description 'AI_AGENT_BRIDGE_ENABLED — "true"/"false"' \
  --value "false" --region $REGION

# ─── external (7) ───────────────────────────
for KEY in kma_decoding_key ncpms_key pesticide_key food_safety_key kamis_key kamis_cert_id kakao_rest_key; do
  aws ssm put-parameter --name "/farmos/prod/external/${KEY}" \
    --type SecureString --tier Standard \
    --description "External SaaS — rotate per provider policy" \
    --key-id alias/aws/ssm \
    --value "REPLACE_ME" --region $REGION   # ⚠️ 각 SaaS 콘솔에서 발급 후 --overwrite
done

# ─── image (1) + ghcr (1) ───────────────────
aws ssm put-parameter --name /farmos/prod/image/tag \
  --type String --tier Standard \
  --description "IMAGE_TAG — GH Actions가 PutParameter로 갱신" \
  --value "latest" --region $REGION

aws ssm put-parameter --name /farmos/prod/ghcr/owner \
  --type String --tier Standard \
  --value "Himedia-AI-01" --region $REGION
```

### 14.3 블록 D — 검증 명령 (반드시 28 반환)

```bash
aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region ap-northeast-2 --query "length(Parameters)"
# 28 ← 캐치 테이블 #8 기록

# 카테고리별 분포 확인
aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region ap-northeast-2 --query "Parameters[].Name" --output text \
  | tr '\t' '\n' | awk -F'/' '{print $4}' | sort | uniq -c
# 기대: cors=1, db=2, external=7, ghcr=1, groq=3, image=1, iot_relay=3, jwt=1, litellm=3, llm=6
```

### 14.4 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `length(Parameters) = 27` | 한 키 누락 (보통 `external/kakao_rest_key` 또는 for 루프 한 번 끊김) | 위 카테고리 카운트로 빠진 그룹 식별 → 단일 명령 재실행 |
| `length(Parameters) = 29+` | 잔존 키 (`/farmos/prod/llm/legacy_key` 같은 이전 시도) | `aws ssm delete-parameter --name <키>` 로 정리 |
| put-parameter 시 "ParameterAlreadyExists" | 이미 시드됨 | `--overwrite` 추가 또는 무시 |
| SecureString 시드 후 `aws ssm get-parameter` 가 base64 출력 | `--with-decryption` 누락 | `--with-decryption` 추가 |
| CloudShell 세션 30분 idle 종료 | 정상 동작 | 재진입 후 환경변수 재설정 (`export AWS_DEFAULT_REGION=ap-northeast-2`) |

---

# §4. M4-A 완료 검증 (한 번에 실행)

```bash
ACCT=242201280878
REGION=ap-northeast-2

echo "=== IAM ==="
aws iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?contains(Arn,'token.actions.githubusercontent.com')].Arn" \
  --output text
aws iam get-role --role-name farmos-gh-actions-deploy --query 'Role.Arn' --output text
aws iam get-instance-profile --instance-profile-name farmos-ec2-instance --query 'InstanceProfile.Arn' --output text
aws iam get-role --role-name farmos-codedeploy-svc --query 'Role.Arn' --output text

echo "=== Infra ==="
aws s3api head-bucket --bucket farmos-codedeploy-${ACCT}-ap-northeast-2 && echo "✓ bucket OK"
aws deploy get-application --application-name farmos --region $REGION --query 'application.applicationName' --output text
aws deploy get-deployment-group --application-name farmos --deployment-group-name farmos-prod \
  --region $REGION --query 'deploymentGroupInfo.deploymentGroupName' --output text

echo "=== SSM ==="
COUNT=$(aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region $REGION --query "length(Parameters)" --output text)
[ "$COUNT" = "28" ] && echo "✓ SSM 28키 OK" || echo "✗ SSM count=$COUNT (28 기대)"
```

---

# §5. 미발급 키 채우기 (운영자 후속 작업)

`REPLACE_ME` 값으로 시드된 10개 키는 외부 SaaS 콘솔에서 실제 키를 발급받은 후 다음 명령으로 갱신:

```bash
aws ssm put-parameter --name /farmos/prod/litellm/api_key \
  --type SecureString --value "<실제-키>" \
  --overwrite --region ap-northeast-2
```

발급 대상 (10개):
- `/farmos/prod/litellm/api_key` — LiteLLM Proxy 콘솔
- `/farmos/prod/llm/upstage_key` — console.upstage.ai
- `/farmos/prod/groq/api_key` — groq.com
- `/farmos/prod/iot_relay/api_key` — Relay 서버에서 공유 시크릿 발급
- `/farmos/prod/external/kma_decoding_key` — data.go.kr
- `/farmos/prod/external/ncpms_key` — NCPMS
- `/farmos/prod/external/pesticide_key` — psis.rda.go.kr
- `/farmos/prod/external/food_safety_key` — foodsafetykorea.go.kr
- `/farmos/prod/external/kamis_key`, `kamis_cert_id` — kamis.or.kr
- `/farmos/prod/external/kakao_rest_key` — developers.kakao.com

---

# §6. 다음 단계

| Module | Skill 호출 | 목적 |
|---|---|---|
| **M4-B** | `/pdca do farmos-ec2-deploy --scope module-4b` | `appspec.yml` + lifecycle 5개 .sh (코드, 사용자 콘솔 작업 0) |
| **M4-C** | `/pdca do farmos-ec2-deploy --scope module-4c` | `.github/workflows/deploy.yml` + 본 런북의 ARN 캐치 테이블 #2/#5/#6/#7 을 GH Secrets로 등록 |
| **M5** | `/pdca do farmos-ec2-deploy --scope module-5` | EC2 시작 + Instance Profile attach (#3) + bootstrap-ec2.sh |
| **M6** | `/pdca do farmos-ec2-deploy --scope module-6` | CloudFlare DNS A 레코드(EIP) + Flexible SSL + Always Use HTTPS |

**M4-A 완료 조건**: 위 §4 스크립트가 IAM 4건 + Infra 3건 + SSM 28키 모두 OK 출력.

---

# §7. R9/R10 트러블슈팅 노트

| 증상 | 원인 | 해결 |
|---|---|---|
| GH Actions에서 `Error: Could not assume role` | Trust Policy sub 조건이 dev 브랜치 push와 매칭 안 됨 | 최초엔 `repo:Himedia-AI-01/FarmOS-Deploy-Test:*` 와일드카드, 동작 확인 후 `:ref:refs/heads/dev`로 좁히기 |
| EC2에서 `aws ssm get-parameter` 401/403 | Instance Profile 미부착 또는 KMS Decrypt 권한 누락 | Step 7 Inline Policy의 `KMSDecryptForSSM` Sid 확인 |
| CodeDeploy "No instances" | EC2 태그 누락 또는 Tag group OR 분리 | EC2 → Tags → Manage tags + Step 13 Tag group 1개 안에 2 tag 확인 |
| `aws s3 cp` 401 | GH Actions Role의 S3DeployBucket Resource ARN 오타 | Step 4 Permissions Policy의 `farmos-codedeploy-242201280878-ap-northeast-2` 정확성 확인 |
| `ssm:PutParameter` 거부 | GH Actions Role의 SSMParameterUpdate Sid 누락 | Step 4 Permissions Policy 재검토 (image/tag만 허용) |

---

# §A. 부록 A — AWS Console 일반 함정 (모든 단계 공통)

### A.1 리전 미스매치

AWS 서비스마다 리전 컨텍스트가 분리되어 있습니다. 콘솔 우상단에서 리전을 바꿔도 **이미 열어둔 다른 탭의 페이지는 이전 리전에 머무릅니다**. IAM 만 글로벌이고 나머지(S3 endpoint/CodeDeploy/SSM/EC2)는 리전별로 분리.

**증상**: "Bucket exists in another region" / "DeploymentGroup not found" / Parameter Store 빈 목록.

**즉시 해결**: 모든 서비스 페이지에서 우상단 리전 드롭다운이 **`Asia Pacific (Seoul) ap-northeast-2`** 인지 확인. CLI 도 `--region ap-northeast-2` 또는 `export AWS_DEFAULT_REGION=ap-northeast-2`.

### A.2 Permissions 탭 vs Trust relationships 탭 혼동

Role 상세 페이지에는 5개 탭이 있고, 그 중 두 개가 정책 관련:

| 탭 | 의미 | 편집 메뉴 |
|---|---|---|
| Permissions | "이 Role 이 무엇을 할 수 있나" | [Add permissions ▼] 드롭다운 |
| Trust relationships | "누가 이 Role 을 AssumeRole 할 수 있나" | [Edit trust policy] 버튼 |

**증상**: GH Actions OIDC sub 조건을 Permissions 탭에 붙여넣기. 또는 Inline policy 를 Trust relationships 에 붙여넣기.

**즉시 해결**: 위 표대로 정확한 탭으로 이동.

### A.3 Add permissions 드롭다운의 옵션 차이

[Add permissions ▼] 드롭다운 옵션:

| 옵션 | 의미 | 사용 위치 |
|---|---|---|
| Attach policies | 기존 Managed Policy 첨부 | Step 6에서 사용 (대신 Create role 단계에서 처리됨) |
| ⭐ Create inline policy | 이 Role 에 한정된 새 Inline Policy 생성 | Step 4, Step 7 |

> Create policy (Managed) 와 Create inline policy 의 차이: Managed는 다른 Role 에도 재사용 가능, Inline은 이 Role 에만 묶이고 Role 삭제 시 같이 삭제됨. **Step 4/7 의 정책은 Inline 사용** (이 Role 전용 + Role 삭제 시 자동 정리).

### A.4 JSON tab 진입 후 Visual editor 자동 회귀

콘솔 정책 편집기가 비어있을 때(Statement: []) JSON 탭을 클릭해도 자동으로 Visual 로 회귀하는 버그가 있습니다.

**즉시 해결**:
1. JSON 탭 클릭 후 편집 영역 안에 1글자라도 입력 (예: 공백 1개) → Visual 로 안 바뀜
2. Ctrl+A → Delete → 정식 JSON 붙여넣기

### A.5 Roles 페이지에서 Role 이 안 보임

콘솔이 기본적으로 "최근 활동순" 정렬 + 검색 필터를 기억합니다.

**증상**: Step 8 끝나고 Step 13 진입 시 Service role 드롭다운에 farmos-codedeploy-svc 가 없음.

**즉시 해결**:
- IAM → Roles 페이지에서 검색바 비우기 (`Q`)
- 정렬 컬럼 "Role name" 클릭해 알파벳 순
- 그래도 없으면 CloudShell 에서 `aws iam list-roles --query "Roles[?starts_with(RoleName,'farmos')]"` 로 실제 존재 확인

### A.6 캐치 테이블 ARN 복사 실수

ARN 을 콘솔에서 마우스 드래그해 복사할 때 앞뒤 공백/줄바꿈이 같이 들어가면 GH Secret/Terraform 에서 silent 에러.

**즉시 해결**: 콘솔 내장 [📋 copy] 아이콘 사용 (모든 ARN 옆에 있음). 직접 드래그 후엔 `echo "$ARN" | xxd | head` 로 끝에 `0a` (LF) 가 없는지 확인.

---

# §B. 부록 B — CLI 권장 흐름

### B.1 AWS CloudShell vs 로컬 CLI

| | CloudShell | 로컬 CLI |
|---|---|---|
| 자격증명 | 자동 (콘솔 로그인 IAM User 동일) | `aws configure --profile <name>` 필요 |
| 설치 | 0 | aws-cli v2 설치 + IAM User 액세스 키 발급 |
| 영속성 | 홈 디렉터리 1GB 영속 / 30분 idle 종료 | 로컬 디스크 |
| 멀티 리전 | 매 세션마다 환경변수 재설정 | profile/region 영구 |
| 권장 시점 | M4-A 셋업처럼 일회성 작업 | 반복 운영 |

### B.2 Profile 분리

```bash
aws configure --profile farmos-admin
# AWS Access Key ID: <Admin User Key>
# Secret Access Key: <Admin User Secret>
# Default region: ap-northeast-2
# Default output: json

# 사용
aws --profile farmos-admin iam list-roles --query 'Roles[?starts_with(RoleName,`farmos`)]'

# 또는 환경변수로 기본 profile 지정
export AWS_PROFILE=farmos-admin
aws sts get-caller-identity
```

### B.3 리전 명시 vs 환경변수

```bash
# 옵션 1: 매 명령 명시
aws ssm get-parameter --name /farmos/prod/db/url --region ap-northeast-2

# 옵션 2: 환경변수 (★ 권장)
export AWS_DEFAULT_REGION=ap-northeast-2
aws ssm get-parameter --name /farmos/prod/db/url
```

### B.4 AWS_PAGER 비활성화

기본적으로 AWS CLI v2 는 긴 출력을 `less` 페이저로 보여 SSH 세션이나 CI 에서 멈춥니다.

```bash
export AWS_PAGER=""        # 빈 문자열 = 페이저 비활성화
echo 'export AWS_PAGER=""' >> ~/.bashrc
```

### B.5 자격증명 검증 한 줄

```bash
aws sts get-caller-identity
# Account: 242201280878
# Arn: arn:aws:iam::242201280878:user/<Admin>
# 또는 OIDC 라면: arn:aws:sts::242201280878:assumed-role/<Role>/...
```

이 출력이 본 런북의 `242201280878` 와 일치하는지 첫 단계에서 확인.

---

# §C. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | (이전) | 14단계 골격, CLI 명령 + Console 한 줄 요약 |
| **v2** | 2026-04-28 | 14단계 모두 5블록(A/B/C/D/E) 균일화 / 클릭 단위 디테일 / 화면 전환 명시 / §-1 사전점검 / §A 콘솔 함정 / §B CLI 흐름 / ACCOUNT_ID 형식 박스 |

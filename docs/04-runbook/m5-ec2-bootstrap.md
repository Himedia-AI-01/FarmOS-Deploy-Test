# M5 Runbook — EC2 인스턴스 + Bootstrap

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` §12.1~§12.4, §16.14 (v0.4)
> **Plan SC**: SC-7 (IAM 최소권한), SC-9 (SSM→.env), R10/R13 완화
> **선행**: M4-A 완료 (캐치 테이블 #3 IAM Instance Profile, SSM 28키)
> **후행**: M4-C 의 첫 배포 (M5 가 끝나야 첫 push 가능)
> **소요 시간**: 40~60분 (Console 클릭 15~20분 + bootstrap 실행 20~30분 + 검증 5분)

---

## §0. 사전 점검 (10분)

### 0.1 M4-A 캐치 검증 (3건 필수)

본 모듈은 M4-A 의 다음 산출물을 사용합니다.

| # | 캐치 | 본 모듈 사용 위치 | 검증 명령 |
|---|---|---|---|
| 3 | IAM Instance Profile `farmos-ec2-instance` | EC2 launch Step 7 | `aws iam get-instance-profile --instance-profile-name farmos-ec2-instance --query 'InstanceProfile.Arn' --output text` |
| — | SSM 28키 시드 | bootstrap 9/9 검증 | `aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 --query "length(Parameters)" --output text` → `28` |
| — | KMS `alias/aws/ssm` Decrypt 권한 | (간접) AfterInstall 훅 동작 | M4-A §3 Step 7 inline policy 의 `KMSDecryptForSSM` Sid |

```bash
# 한 번에 검증
ACCT=242201280878
REGION=ap-northeast-2
aws iam get-instance-profile --instance-profile-name farmos-ec2-instance \
  --query 'InstanceProfile.{Arn:Arn,Roles:Roles[].RoleName}' --output text
# → arn:aws:iam::242201280878:instance-profile/farmos-ec2-instance  farmos-ec2-instance

aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region $REGION --query "length(Parameters)" --output text
# → 28
```

### 0.2 운영자 IP 확인 (SSH inbound 룰 사용)

```bash
# 본인의 공인 IP (외부)
curl -s https://checkip.amazonaws.com
# → 121.xxx.xxx.xxx

# CIDR 형식: 121.xxx.xxx.xxx/32 (단일 IP)
```

이 값을 Step 5 Security Group SSH 룰의 Source 로 사용. 운영자 IP 가 변동(공유기 재시작) 가능하므로 본 IP 로 잠그되, 변경 시 SG 룰 갱신.

### 0.3 보유 도메인 (M6 용 — 본 모듈은 사용 안 함)

M6 에서 도메인을 CloudFlare 에 등록하므로 **현재 시점에 도메인이 있어야 하는 건 아님**. M5 후 M6 진행 시 필요.

---

## §1. EC2 인스턴스 시작 (콘솔 9 step)

### Step 1.1 AMI 선택 (Ubuntu Server 24.04 LTS)

#### 블록 A — 화면 진입 경로

```
AWS Console (https://console.aws.amazon.com)
   ↓ 우상단 리전 = "Asia Pacific (Seoul) ap-northeast-2" 확인
   ↓ 상단 검색바에 "EC2" 입력 → EC2 (Virtual Servers in the Cloud) 클릭
EC2 Dashboard
   ↓ 좌측 메뉴 "Instances" 그룹 → "Instances" 클릭
Instances 목록 페이지
   ↓ 우측 상단 [Launch instances] 주황 버튼 클릭
Launch an instance 페이지 (긴 단일 페이지, 7개 섹션)
```

#### 블록 B — 화면 레이아웃 (Application and OS Images 섹션)

```
┌──────────────────────────────────────────────────────────────────┐
│ Launch an instance                                               │
├──────────────────────────────────────────────────────────────────┤
│ Name and tags                                                    │
│  Name *                                                          │
│   ┌──────────────────────────────────────────────────┐           │
│   │ farmos-prod-1                              ★★★    │           │
│   └──────────────────────────────────────────────────┘           │
│   [Add additional tags]   ← Step 9에서 사용                     │
├──────────────────────────────────────────────────────────────────┤
│ ── Application and OS Images (Amazon Machine Image) ──            │
│  Quick Start  [Recents] [My AMIs] [AWS Marketplace] ...           │
│   ┌─ Tabs ─┬─ Tabs ─┬─ Tabs ─┬─ Tabs ─┬─ Tabs ─┬─ Tabs ─┐         │
│   │Amazon  │ macOS  │  ★Ubuntu │ Windows│  Red   │  SUSE  │         │
│   │ Linux  │        │          │        │  Hat   │        │         │
│   └────────┴────────┴──────────┴────────┴────────┴────────┘         │
│                                                                  │
│   AMI:  ┌──────────────────────────────────────────┐ [Browse...] │
│         │ ★ Ubuntu Server 24.04 LTS (HVM), SSD     │             │
│         │   Volume Type, ami-XXXXXXXX (64-bit x86) │ ← 자동 선택  │
│         └──────────────────────────────────────────┘             │
│                                                                  │
│   Architecture:  ⦿ 64-bit (x86)   ◯ 64-bit (ARM)                 │
│                  (★ x86 — backend Dockerfile 이 amd64)           │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 입력값 | 비고 |
|---|---|---|
| Name and tags > Name | `farmos-prod-1` | EC2 의 `Name` 태그로 자동 매핑 (DG 의 App=farmos 와는 별개) |
| AMI Quick Start 탭 | `Ubuntu` ★ 클릭 | |
| AMI 드롭다운 | `Ubuntu Server 24.04 LTS (HVM), SSD Volume Type` | 가장 위 행 — 24.04 LTS Free tier eligible |
| Architecture | `64-bit (x86)` | ARM 선택 시 Docker 이미지 풀 실패 (backend Dockerfile linux/amd64) |

#### 블록 D — 검증

(이 단계는 launch 버튼 누르기 전이므로 별도 CLI 검증 없음 — 다음 step 으로)

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| AMI 드롭다운에 22.04 만 있음 | 콘솔 캐시 | 페이지 새로고침 |
| `ARM 64-bit` 라디오 자동 선택 | t3 family 가 아닌 t4g (ARM) 가정 시 | x86 라디오 명시 클릭 — t3.medium = x86 |
| 24.04 옆 "Free tier eligible" 안 보임 | 신규 계정 12개월 free tier 만료 | 그래도 진행 — t3.medium 은 free tier 외 |
| Marketplace 탭에서 다른 24.04 AMI 선택 | 광고/유료 AMI 잘못 선택 | Quick Start > Ubuntu 탭의 가장 위 AMI 그대로 사용 |
| 한국어 콘솔에서 "Ubuntu 서버" 표시 | 콘솔 언어 설정 | 그대로 진행 (AMI ID 동일) |

---

### Step 1.2 Instance Type (t3.medium)

#### 블록 A — 진입 경로

```
같은 Launch instance 페이지
   ↓ 스크롤 다운 → "Instance type" 섹션
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ ── Instance type ──                                              │
│  Instance type *                                                 │
│   ┌──────────────────────────────────────────────┐ [Compare ...] │
│   │ ★ t3.medium                                 ▼│              │
│   │   2 vCPU  4 GiB Memory  Up to 5 Gbps         │              │
│   └──────────────────────────────────────────────┘              │
│   On-Demand Linux pricing: $0.0416/h (~$30/월)                  │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Instance type | `t3.medium` (2 vCPU / 4 GiB) |

#### 블록 D — 검증 (launch 후)

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].InstanceType" --output text
# → t3.medium
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| t3.medium 드롭다운에 안 보임 | 콘솔 검색 필터 활성화 | 드롭다운 검색창에 "t3.medium" 입력 |
| t3.small 선택 (2GB RAM) | 기본값/오인 | sentence-transformers 메모리 부족 — t3.medium 필수 |
| t3a.medium (AMD) | t3 와 다름 | t3 (Intel) 권장. t3a 도 동작은 하지만 약간 저렴, 약간 느림 |
| Spot Instance 옵션 활성화 | Advanced details 의 Purchasing option | 운영용 prod 는 On-Demand. Spot 은 dev/staging 만 |
| t2.medium 선택 | 구세대 | t3.medium (Nitro 기반) 권장 |

---

### Step 1.3 Key Pair (신규 생성)

#### 블록 A — 진입 경로

```
Launch instance 페이지
   ↓ 스크롤 다운 → "Key pair (login)" 섹션
   ↓ Key pair name 드롭다운 우측 ⭐ "Create new key pair" 링크 클릭
   ↓ 모달 팝업 표시
```

#### 블록 B — 화면 레이아웃 (Create key pair 모달)

```
┌──────────────────────────────────────────────────────────────┐
│ Create key pair                                              │
├──────────────────────────────────────────────────────────────┤
│  Key pair name *                                             │
│   ┌──────────────────────────────────────────────────┐       │
│   │ farmos-prod-1                                    │       │
│   └──────────────────────────────────────────────────┘       │
│                                                              │
│  Key pair type                                               │
│   ⦿ RSA                ★ (4096-bit, 호환성 최대)               │
│   ◯ ED25519            (현대적, 더 짧음 — Windows OpenSSH 호환  │
│                          확인 필요)                           │
│                                                              │
│  Private key file format                                     │
│   ⦿ .pem               ★ (OpenSSH 호환 — chmod 400)            │
│   ◯ .ppk               (PuTTY 전용 — Windows PuTTY 사용 시만)  │
│                                                              │
│  [Cancel]                            [Create key pair] ★     │
└──────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 | 비고 |
|---|---|---|
| Key pair name | `farmos-prod-1` | EC2 Name 과 동일하게 통일 |
| Key pair type | `RSA` (라디오) | ED25519 도 가능 — Git Bash/WSL OpenSSH 8.x+ |
| Private key file format | `.pem` | 클릭 시 즉시 다운로드 — **단 1회 기회** |

#### 블록 D — 검증 (다운로드 직후)

```bash
# Windows: 다운로드 폴더로 이동
cd ~/Downloads
ls -la farmos-prod-1.pem
# -rw-rw-rw- 1 user user 3243 ... farmos-prod-1.pem

# 권한 잠금 (필수)
chmod 400 farmos-prod-1.pem
# Windows 의 경우 (Git Bash):
icacls farmos-prod-1.pem /inheritance:r /grant:r "$(whoami):R"
# 또는 PowerShell:
# icacls .\farmos-prod-1.pem /inheritance:r
# icacls .\farmos-prod-1.pem /grant:r "$($env:USERNAME):(R)"

# 안전한 위치로 이동 (예: ~/.ssh/)
mv farmos-prod-1.pem ~/.ssh/
ls -la ~/.ssh/farmos-prod-1.pem
# -r-------- 1 user user 3243 ... farmos-prod-1.pem
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| .pem 다운로드 모달 한 번만 뜸 | AWS 의 보안 정책 — 재다운로드 불가 | 잃어버리면 새 key pair 만들기 + 인스턴스 재기동 후 SSM Session Manager 로 authorized_keys 교체 |
| `chmod 400` 후에도 SSH `Permissions are too open` | Windows NTFS 의 ACL 가 chmod 무시 | `icacls` 사용 (위 예시) |
| `ssh: Load key "farmos-prod-1.pem": invalid format` | .ppk 를 받음 | .pem 다시 받거나 `puttygen .ppk -O private-openssh -o .pem` 변환 |
| .pem 파일 첫줄이 `-----BEGIN OPENSSH PRIVATE KEY-----` (ED25519) | 정상 — RSA 면 `-----BEGIN RSA PRIVATE KEY-----` | 둘 다 OpenSSH 호환 — SSH 명령 그대로 동작 |
| Key pair name 중복 | 이미 같은 이름 존재 | 새 이름 또는 EC2 → Key pairs 에서 기존 삭제 |

---

### Step 1.4 Network 설정

#### 블록 A — 진입 경로

```
Launch instance 페이지
   ↓ 스크롤 다운 → "Network settings" 섹션
   ↓ 우측 상단 [Edit] 버튼 클릭 (펼치기)
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ ── Network settings ──                                  [Edit ▼] │
│  VPC *                                                           │
│   ┌──────────────────────────────────────────────────┐           │
│   │ vpc-xxxxxxx (default)                          ▼ │ ★         │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Subnet                                                          │
│   ┌──────────────────────────────────────────────────┐           │
│   │ No preference (Default subnet in any AZ)       ▼ │           │
│   └──────────────────────────────────────────────────┘           │
│   (또는 명시적으로 ap-northeast-2a 선택)                         │
│                                                                  │
│  Auto-assign public IP                                           │
│   ┌──────────────────────────────────────────────────┐           │
│   │ ⚠️ Disable                                     ▼ │ ★          │
│   └──────────────────────────────────────────────────┘           │
│   ※ Enable 면 인스턴스 재기동마다 IP 변경됨 — Elastic IP 사용     │
│                                                                  │
│  Firewall (security groups)                                      │
│   ⦿ Create security group   ★ (신규 생성)                        │
│   ◯ Select existing security group                               │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 | 비고 |
|---|---|---|
| VPC | `default` (drop-down 첫 항목) | 신규 VPC 만들 필요 없음 |
| Subnet | `No preference` 또는 `ap-northeast-2a` 명시 | Public subnet 이어야 함 (default subnet 모두 public) |
| Auto-assign public IP | `Disable` | ⚠️ Enable 시 EIP 와 충돌 가능 |
| Firewall | `Create security group` (라디오) | Step 1.5 에서 룰 입력 |

#### 블록 D — 검증 (launch 후)

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].{Subnet:SubnetId,PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress}" \
  --output table
# Auto-assign Public = Disable 했으므로 PublicIp 는 None — Step 2 EIP 후 채워짐
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Subnet 드롭다운 비어있음 | 리전 잘못 (서울 외) | 우상단 리전 = ap-northeast-2 |
| VPC 가 default 외 다중 표시 | 이전 VPC 만들기 시도 잔존 | default 만 선택 (다른 VPC 는 EIP/SG 룰 별개 관리) |
| Auto-assign 을 Enable 채로 두면 | EIP attach 후에도 두 IP 가 노출됨 (route 53 캐시 혼선) | Disable 권장 — EIP 만 단일 IP |
| Public subnet 이 아닌 private subnet 선택 | default subnet 도 region 별로 다름 | "Auto-assign public IP" 가 default 상태에서 Enable 인 subnet = public |
| Subnet AZ 선택 후 EIP 가 다른 AZ | 정상 — EIP 는 region 단위 | 선택한 instance 의 AZ 와 자동 매칭 |

---

### Step 1.5 Security Group (신규 생성)

#### 블록 A — 진입 경로

```
Network settings 의 Firewall 섹션
   ↓ "Create security group" 라디오 선택 후
   ↓ 펼쳐지는 Inbound rules 섹션
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│  Security group name *                                           │
│   ┌──────────────────────────────────────────────────┐           │
│   │ farmos-prod-sg                                   │           │
│   └──────────────────────────────────────────────────┘           │
│  Description *                                                   │
│   ┌──────────────────────────────────────────────────┐           │
│   │ FarmOS prod single-host: 22 ops, 80 CF flexible  │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Inbound security groups rules                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Type      | Protocol | Port  | Source         | Description│ │
│  ├───────────┼──────────┼───────┼────────────────┼────────────┤ │
│  │ ssh       │ TCP      │ 22    │ My IP / 32     │ ops SSH    │ │
│  │ HTTP      │ TCP      │ 80    │ Anywhere-IPv4  │ CF→ origin │ │
│  │ HTTP      │ TCP      │ 80    │ Anywhere-IPv6  │ CF→ origin │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  [Add security group rule]                                       │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표 (3 룰)

| # | Type | Protocol | Port | Source (Type) | Source (값) | 설명 |
|---|---|---|---|---|---|---|
| 1 | `ssh` | TCP | 22 | `My IP` | `121.xxx.xxx.xxx/32` (자동) | 운영자 SSH |
| 2 | `HTTP` | TCP | 80 | `Anywhere-IPv4` | `0.0.0.0/0` | CloudFlare → origin (CF는 IPv4 다수) |
| 3 | `HTTP` | TCP | 80 | `Anywhere-IPv6` | `::/0` | CF IPv6 |

> ⚠️ HTTPS (443) 는 **추가하지 마세요**. Plan D6 에서 CloudFlare Flexible 모드 채택 — CF→origin 은 항상 HTTP. 443 inbound 가 있으면 ACM 인증서 미설정으로 ssl_handshake 실패. (만약 향후 Full SSL 로 전환 시 추가 가능.)

> ⚠️ 더 단단한 운영을 원할 때는 80 의 Source 를 [CloudFlare 의 IPv4/IPv6 대역만](https://www.cloudflare.com/ko-kr/ips/) 으로 좁힐 수 있습니다. 본 런북은 0.0.0.0/0 (Direct origin 접속 차단은 CF Page Rule + Authenticated Origin Pulls 로 별도 처리).

#### 블록 D — 검증

```bash
SG_ID=$(aws ec2 describe-security-groups --filters Name=group-name,Values=farmos-prod-sg \
  --query 'SecurityGroups[0].GroupId' --output text)
echo "$SG_ID"
# → sg-xxxxxxxx

aws ec2 describe-security-groups --group-ids "$SG_ID" \
  --query 'SecurityGroups[0].IpPermissions[].{Port:FromPort,Source:IpRanges[].CidrIp,SourceV6:Ipv6Ranges[].CidrIpv6}'
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `My IP` 가 잘못된 IP 로 자동 채워짐 | NAT 장비 또는 콘솔 IPv6 우선 | 수동으로 `curl -s checkip.amazonaws.com` 결과 + `/32` |
| 443 inbound 도 추가함 | 과도한 룰 | 본 시점엔 제거 — Flexible SSL 은 80만 |
| All TCP / All Traffic 오픈 | 가독성/실수 | 정확히 22 + 80 만 |
| Outbound rules 손댐 | 기본값 (All traffic / 0.0.0.0/0 / ::/0) 은 그대로 | EC2 → SSM/GHCR/CodeDeploy 가 다 outbound 필요 — 변경 X |
| Security group name 중복 | 이전 시도 잔존 | EC2 → Security Groups 에서 기존 삭제 후 재생성 |

---

### Step 1.6 Storage (gp3 50GB)

#### 블록 A — 진입 경로

```
Launch instance 페이지
   ↓ 스크롤 다운 → "Configure storage" 섹션
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ ── Configure storage ──                            [Advanced ▼]  │
│                                                                  │
│  1x  ┌─────┬───────┬──────────┬────────────────┐                  │
│      │ 50  │  GiB ▼│ gp3    ▼ │ Root volume     │                  │
│      └─────┴───────┴──────────┴────────────────┘                  │
│      [+ Add new volume]                                          │
│                                                                  │
│  Advanced (펼치기) ▼                                             │
│   IOPS:        3000  (gp3 기본)                                  │
│   Throughput:  125 MB/s  (gp3 기본)                              │
│   Encryption:  ☐ Encrypt this volume                             │
│   Delete on termination:  Yes  (Root 라 자동)                    │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 | 비고 |
|---|---|---|
| Size | `50` GiB | 기본 8GB → 50GB 로 늘림. Postgres data + ChromaDB + ML models |
| Volume Type | `gp3` | gp2 보다 저렴 + 기본 IOPS 3000 보장 |
| IOPS | `3000` (기본) | t3.medium 에 충분 |
| Throughput | `125` MB/s (기본) | |
| Encryption | (미체크) | 본 시점은 비활성. 운영 단계에서 EBS encryption default 활성화 권장 |

#### 블록 D — 검증

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].BlockDeviceMappings[].Ebs.VolumeId" --output text \
  | xargs aws ec2 describe-volumes --volume-ids \
  --query 'Volumes[].[VolumeId,Size,VolumeType,Iops]' --output table
# │ vol-xxx │ 50 │ gp3 │ 3000 │
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| 8GB 로 launch | Size 변경 누락 | 시작 후 EC2 → Volumes → Modify volume → 50GB 로 확장 (root 는 grow-fs 필요 — bootstrap 후 `sudo growpart /dev/xvda 1 && sudo resize2fs /dev/xvda1`) |
| gp2 선택 | 기본 변경 누락 | gp3 로 수정 |
| 30GB 만 사용 | Free tier 한도 | t3.medium 자체가 free tier 외 — 50GB 진행 |
| Encryption ON 후 KMS 권한 누락 | KMS Decrypt 허용 안 된 default key | M5 시작 시점은 미체크 권장. 향후 EC2 setting 의 EBS encryption default 활성 후 신규 인스턴스부터 적용 |
| `Delete on termination = No` 로 변경 | 종료 후에도 EBS 잔존 (비용) | 기본값 Yes 그대로 |

---

### Step 1.7 Advanced details — IAM Instance Profile

#### 블록 A — 진입 경로

```
Launch instance 페이지
   ↓ 스크롤 다운 → "Advanced details" 섹션
   ↓ 우측 [▼] 화살표 펼치기 (기본은 접혀있음)
   ↓ 펼친 후 첫 번째 항목 "IAM instance profile"
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ ── Advanced details (펼침) ──                                    │
│                                                                  │
│  IAM instance profile                                            │
│   ┌──────────────────────────────────────────────────┐           │
│   │ ★ farmos-ec2-instance                          ▼ │           │
│   └──────────────────────────────────────────────────┘           │
│   [Create new IAM profile] (안 누름 — M4-A 에서 만들었음)         │
│                                                                  │
│  Hostname type      Resource name (default)                     │
│  Metadata accessible    ⦿ Enabled  ◯ Disabled  ★ (IMDSv2 필요)   │
│  Metadata version       ⦿ V2 only (token required)  ★            │
│  Metadata token response hop limit  ┌── 2 ──┐ ★ (Docker 컨테이너  │
│                                              에서 IMDS 접근 시   │
│                                              hop 1 → 2 권장)    │
│                                                                  │
│  Detailed CloudWatch monitoring     ☐ (미체크 — 기본 5분 간격 OK)│
│  Termination protection             ☑ Enable  ★ (실수 종료 방지) │
│                                                                  │
│  ── User data — optional ──                                      │
│   (옵션) bootstrap-ec2.sh 의 내용을 여기에 붙여넣을 수 있음.       │
│   본 런북은 SSH 후 수동 실행을 권장 (디버깅 용이).                │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 | 비고 |
|---|---|---|
| IAM instance profile | `farmos-ec2-instance` (★ 드롭다운) | M4-A 캐치 #3 |
| Metadata accessible | `Enabled` | IMDSv2 필요 |
| Metadata version | `V2 only (token required)` | after-install.sh 의 IMDSv2 토큰 사용에 필수 |
| Metadata token response hop limit | `2` | 기본 1 → 2. Docker 컨테이너에서 IMDS 접근 시 hop +1 |
| Detailed CloudWatch monitoring | (미체크) | 1분 간격 모니터링이 필요해지면 체크 |
| Termination protection | `Enable` | 실수 종료 방지 (다음 단계 Step 9 의 태그와 함께 운영 모드 강화) |
| User data | (빈칸) | bootstrap 은 SSH 후 수동 실행 권장 |

#### 블록 D — 검증

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].{
    Profile: IamInstanceProfile.Arn,
    IMDSv2: MetadataOptions.HttpTokens,
    HopLimit: MetadataOptions.HttpPutResponseHopLimit,
    DisableTermination: DisableApiTermination
  }" --output json
```

기대 출력:
```json
[ {
  "Profile": "arn:aws:iam::242201280878:instance-profile/farmos-ec2-instance",
  "IMDSv2": "required",
  "HopLimit": 2,
  "DisableTermination": true
} ]
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| IAM profile 드롭다운에 `farmos-ec2-instance` 없음 | M4-A §3 Step 5/6/7 미완료 또는 다른 리전 | M4-A 검증: `aws iam get-instance-profile --instance-profile-name farmos-ec2-instance` |
| IMDSv2 = `optional` 로 둠 | 기본값 | after-install.sh 의 IMDSv2 토큰이 fallback 으로 동작은 하지만, `required` 로 보안 강화 권장 |
| Hop limit = 1 | 기본값 | 컨테이너 안에서 IMDS 호출 시 fail. **2 로 변경 필수** |
| Termination protection 누락 | 기본 disabled | 운영 인스턴스는 enable. 종료 시 EC2 → Instance settings → Change termination protection |
| User data 에 bootstrap 붙여넣기 | 가능하지만 디버깅 어려움 | SSH 후 수동 실행 권장 |

---

### Step 1.8 Tags 추가 (Tag group AND 매칭 — 핵심)

#### 블록 A — 진입 경로

```
Launch instance 페이지
   ↓ 최상단 "Name and tags" 섹션의 [Add additional tags] 클릭
   ↓ 또는 우측 "Summary" 영역의 [Edit] 누르고 Tags 섹션 추가
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ ── Name and tags ──                                              │
│  Name                                                            │
│   farmos-prod-1                                                  │
│  ── Additional tags ── [Add tag]                                 │
│  ┌─────────────┬─────────────┬─────────────────────────┐         │
│  │ Key         │ Value       │ Resource types          │         │
│  ├─────────────┼─────────────┼─────────────────────────┤         │
│  │ App         │ farmos      │ ☑ Instances             │         │
│  │             │             │ ☑ Volumes               │         │
│  ├─────────────┼─────────────┼─────────────────────────┤         │
│  │ Environment │ prod        │ ☑ Instances             │         │
│  │             │             │ ☑ Volumes               │         │
│  └─────────────┴─────────────┴─────────────────────────┘         │
│  ※ "Name" 태그(farmos-prod-1) 는 위에 자동 + 이 두 줄 추가        │
└──────────────────────────────────────────────────────────────────┘
```

> ⚠️ **이 두 태그가 CodeDeploy DeploymentGroup `farmos-prod` 의 Tag group 1개 안의 2 태그(AND)에 정확히 매칭되어야** 첫 배포가 성공합니다. 키 대소문자 / 값 대소문자 모두 정확히.

#### 블록 C — 입력값 표 (3 태그)

| Key | Value | Resource types | 용도 |
|---|---|---|---|
| `Name` | `farmos-prod-1` | Instance / Volume | 콘솔 가독성 (자동) |
| `App` | `farmos` | Instance / Volume | ★ DG 매칭 (Tag 1) |
| `Environment` | `prod` | Instance / Volume | ★ DG 매칭 (Tag 2) |

#### 블록 D — 검증

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].Tags" --output json
# [ [
#   {"Key": "Name", "Value": "farmos-prod-1"},
#   {"Key": "App", "Value": "farmos"},
#   {"Key": "Environment", "Value": "prod"}
# ] ]

# DG 매칭 시뮬레이션 (CodeDeploy 가 동일하게 호출)
aws ec2 describe-instances \
  --filters "Name=tag:App,Values=farmos" "Name=tag:Environment,Values=prod" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text
# → i-xxxxxxxx (1개)
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `app=farmos` 처럼 소문자 키 | 대소문자 mismatch — DG 못 찾음 | EC2 → Instance → Tags → Manage tags → 정확히 `App` (P 대문자) |
| `Environment=production` 또는 `Production` | 값 mismatch | 정확히 `prod` (소문자) |
| Resource types 에 Volumes 만 체크하고 Instances 미체크 | EC2 인스턴스 자체에 태그 안 붙음 | 양쪽 모두 ☑ |
| Name 태그를 별도로 추가 | "Name" 은 자동 생성됨 (Step 1.1 의 Name 필드) | 추가 안 해도 OK |
| DG 매칭이 OR (Tag group 분리) | M4-A Step 13 Tag group 2개 분리 | M4-A 의 DG 편집 → 1 group 안에 2 tag |

---

### Step 1.9 Review + Launch

#### 블록 A — 진입 경로

```
Launch instance 페이지 우측 패널 "Summary"
   ↓ 모든 섹션 ✓ 표시 확인 후
   ↓ ⭐ [Launch instance] 주황 버튼 (페이지 우측 하단)
```

#### 블록 B — Summary 패널 ASCII

```
┌──────────────────────────────────────────────────────────┐
│ Summary                                                  │
├──────────────────────────────────────────────────────────┤
│ Number of instances: 1                                   │
│ ───                                                      │
│ Software Image (AMI): Ubuntu Server 24.04 LTS x86_64     │
│ Virtual server type: t3.medium                           │
│ Firewall (security group): farmos-prod-sg (new)          │
│ Storage (volumes): 1 EBS gp3 50 GiB                      │
│ ───                                                      │
│ ⓘ Free tier: 12 months — t3.medium 은 외                 │
│                                                          │
│ [Launch instance] ★                                      │
└──────────────────────────────────────────────────────────┘
```

#### 블록 C — 클릭 후 화면

```
Success
Successfully initiated launch of instance (i-XXXXXXXX)
   ↓ [View all instances] 클릭
Instances 목록 → farmos-prod-1 행 → State: pending → running (1~2분)
```

#### 블록 D — 검증

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" \
  --query "Reservations[].Instances[].{Id:InstanceId,State:State.Name,LaunchTime:LaunchTime}" \
  --output table
# │ i-xxx │ running │ 2026-04-28T13:24:05.000Z │
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Launch 클릭 후 "InsufficientInstanceCapacity" | 해당 AZ 의 t3.medium 부족 | 다른 AZ 선택 (Subnet 변경) |
| `pending` 에서 `terminated` 로 즉시 | AMI 호환성 또는 EBS 한도 초과 | Account → Service quotas 확인 |
| `running` 인데 SSH 가 안 됨 | Step 5 SG 설정 문제 또는 EIP 미부착 | Step 2 EIP 작업 후 다시 시도 |
| `Insufficient permissions to launch instance` | 콘솔 로그인 IAM User 가 ec2:RunInstances 없음 | Admin User 로 로그인 |
| 1분 후에도 `pending` | AMI/EBS 가져오는 중 | 2~3분 대기 |

---

## §2. Elastic IP 할당 + Attach

### Step 2.1 Allocate Elastic IP

#### 블록 A — 화면 진입 경로

```
EC2 Dashboard
   ↓ 좌측 메뉴 "Network & Security" 그룹 → ⭐ "Elastic IPs" 클릭
Elastic IP addresses 목록
   ↓ 우측 상단 [Allocate Elastic IP address] 주황 버튼
Allocate Elastic IP address 페이지
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ Allocate Elastic IP address                                      │
├──────────────────────────────────────────────────────────────────┤
│  Network Border Group                                            │
│   ┌──────────────────────────────────────────────────┐           │
│   │ ap-northeast-2                                 ▼ │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Public IPv4 address pool                                        │
│   ⦿ Amazon's pool of IPv4 addresses    ★                         │
│   ◯ Public IPv4 address that you bring to your AWS account       │
│   ◯ Customer owned pool of IPv4 addresses                        │
│                                                                  │
│  Tags - optional                                                 │
│   Key: Name        Value: farmos-prod-1-eip                      │
│                                                                  │
│  [Cancel]                              [Allocate] ★              │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Network Border Group | `ap-northeast-2` |
| Public IPv4 address pool | `Amazon's pool` (라디오) |
| Tags | Key=`Name`, Value=`farmos-prod-1-eip` |

#### 블록 D — 검증

```bash
aws ec2 describe-addresses \
  --filters "Name=tag:Name,Values=farmos-prod-1-eip" \
  --query 'Addresses[0].{IP:PublicIp,AllocId:AllocationId,Assoc:AssociationId}' \
  --output json
```

기대 출력:
```json
{ "IP": "13.124.xxx.xxx",
  "AllocId": "eipalloc-xxxxxxxx",
  "Assoc": null }       ← 아직 attach 안 됨
```

이 `IP` 값을 캐치하세요. **M6 §3 의 DNS A 레코드의 IPv4 값으로 사용됩니다**.

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| 할당 limit (5개) 초과 | 기존 unattached EIP 잔존 | EC2 → Elastic IPs → 미사용 EIP 선택 → Release |
| `attempt to alloc more than your limit` | 계정 신규 + 기본 limit | Service quotas → Increase 요청 |
| `IPv6 only` 옵션 활성 | IPv4 기능 비활성 | "Amazon's pool of IPv4 addresses" 라디오 |
| Allocated 후 Associate 안 함 | 다음 step 에서 attach | 미부착 상태로 두면 시간당 $0.005 과금 |
| 다른 리전에서 allocate | 리전 미스 | 우상단 리전 = ap-northeast-2 |

---

### Step 2.2 Associate Elastic IP

#### 블록 A — 화면 진입 경로

```
Elastic IP addresses 목록
   ↓ 방금 만든 행(13.124.xxx.xxx) 클릭 (체크박스)
   ↓ 우측 상단 [Actions ▼] → "Associate Elastic IP address" 클릭
Associate Elastic IP address 페이지
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ Associate Elastic IP address                                     │
├──────────────────────────────────────────────────────────────────┤
│  Resource type                                                   │
│   ⦿ Instance       ★                                             │
│   ◯ Network interface                                            │
│                                                                  │
│  Instance                                                        │
│   ┌──────────────────────────────────────────────────┐           │
│   │ ★ i-xxxxxxx (farmos-prod-1)                    ▼ │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Private IP address                                              │
│   ┌──────────────────────────────────────────────────┐           │
│   │ 172.31.xx.xx (자동 채워짐)                     ▼ │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Reassociation                                                   │
│   ☐ Allow this Elastic IP address to be reassociated             │
│                                                                  │
│  [Cancel]                              [Associate] ★             │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Resource type | `Instance` |
| Instance | `i-xxxxxxx (farmos-prod-1)` |
| Private IP address | (자동) |
| Reassociation | (미체크) |

#### 블록 D — 검증

```bash
aws ec2 describe-addresses \
  --filters "Name=tag:Name,Values=farmos-prod-1-eip" \
  --query 'Addresses[0].{IP:PublicIp,Instance:InstanceId,Assoc:AssociationId}' \
  --output json
# { "IP": "13.124.xxx.xxx", "Instance": "i-xxx", "Assoc": "eipassoc-xxxxxxxx" }

# 인스턴스에서도 확인
aws ec2 describe-instances --instance-ids <i-xxx> \
  --query "Reservations[].Instances[].PublicIpAddress" --output text
# → 13.124.xxx.xxx
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| Instance 드롭다운에 farmos-prod-1 없음 | 인스턴스가 stopped 또는 다른 AZ | 인스턴스 running 확인 |
| "InvalidAssociationID.NotFound" | 이미 다른 EIP 가 attach 됨 | 기존 EIP disassociate 먼저 |
| Associate 후에도 PublicIp = None | 콘솔 캐시 | 30초 후 새로고침 |
| Reassociation 체크 함 | 다른 인스턴스로 옮길 때 사용 | 본 시점 미체크 권장 |
| EIP attach 후 SSH 안 됨 | SG 22 의 Source 가 운영자 IP 아님 | Step 1.5 SG 의 SSH 룰 확인 |

---

## §3. SSH 접속 + Bootstrap 실행

### Step 3.1 SSH 키 권한 + 첫 접속

#### 블록 A — 진입점

```
로컬 터미널 (Bash / Git Bash / WSL / PowerShell)
   ↓ Step 1.3 에서 다운로드 + chmod 400 한 .pem 사용
   ↓ EIP 13.124.xxx.xxx 로 접속
```

#### 블록 B — 명령 시퀀스

```bash
# 권한 확인
ls -la ~/.ssh/farmos-prod-1.pem
# → -r--------  (chmod 400)

EIP=13.124.xxx.xxx   # ← Step 2.1 캐치 값

# 첫 접속 (host key 추가 — yes)
ssh -i ~/.ssh/farmos-prod-1.pem ubuntu@$EIP
# The authenticity of host '13.124.xxx.xxx (...)' can't be established.
# ED25519 key fingerprint is SHA256:...
# Are you sure you want to continue connecting (yes/no)? yes
#
# Welcome to Ubuntu 24.04 LTS (GNU/Linux 6.8.0-XX-aws x86_64)
# ubuntu@ip-172-31-xx-xx:~$
```

#### 블록 C — 입력값 표

| 항목 | 값 |
|---|---|
| 키 경로 | `~/.ssh/farmos-prod-1.pem` |
| 사용자 | `ubuntu` (Ubuntu AMI 기본) |
| 호스트 | EIP (Step 2.1 결과) — 도메인 아직 없음 |
| 포트 | 22 (기본) |

#### 블록 D — 검증 (EC2 안에서)

```bash
# 메타데이터로 자기 인스턴스 ID 확인
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id
# → i-xxxxxxxx (콘솔의 i-xxx 와 일치해야 함)

# OS / 커널
uname -a
# → Linux ip-172-31-xx-xx 6.8.0-XX-aws ... x86_64
lsb_release -d
# → Description: Ubuntu 24.04.X LTS
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `Permissions are too open` (.pem 0644) | chmod 400 누락 | `chmod 400 ~/.ssh/farmos-prod-1.pem` |
| `Connection refused` | SG 의 22 룰 Source mismatch | 운영자 IP 변경된 경우 SG 룰 갱신 |
| `Connection timed out` | EIP attach 안 됨 또는 인스턴스 stopped | Step 2.2 검증 + 콘솔에서 인스턴스 running |
| `Permission denied (publickey)` | username 잘못 (`ec2-user` 같은) | Ubuntu AMI 의 기본 user = `ubuntu` |
| `Host key verification failed` | 이전 IP 의 host key 가 known_hosts 에 잔존 | `ssh-keygen -R $EIP` 후 재시도 |

---

### Step 3.2 bootstrap-ec2.sh 업로드

#### 블록 A — 진입점

```
로컬 터미널 (FarmOS-Deploy-Test 디렉토리)
   ↓ scripts/bootstrap-ec2.sh 가 이미 존재 (M5 산출물)
   ↓ scp 로 EC2 의 ~/ 로 업로드
```

#### 블록 B — 명령 시퀀스

```bash
cd E:/new_my_study/FarmOS-Deploy-Test

# 로컬에서 LF 검증
file scripts/bootstrap-ec2.sh
# → ASCII text  (CRLF 면 dos2unix scripts/bootstrap-ec2.sh)

# 업로드
scp -i ~/.ssh/farmos-prod-1.pem scripts/bootstrap-ec2.sh ubuntu@$EIP:~/
# bootstrap-ec2.sh    100%  ~5KB  ~5KB/s   00:01

# EC2 안에서 다시 SSH (또는 같은 세션)
ssh -i ~/.ssh/farmos-prod-1.pem ubuntu@$EIP
ls -la ~/bootstrap-ec2.sh
# → -rwxr--r-- 1 ubuntu ubuntu 5234 ... bootstrap-ec2.sh
```

#### 블록 C — 입력값 표

| 항목 | 값 |
|---|---|
| 로컬 경로 | `scripts/bootstrap-ec2.sh` |
| EC2 경로 | `/home/ubuntu/bootstrap-ec2.sh` |
| 파일 EOL | LF (필수) |
| 권한 | `+x` (sudo bash 로 실행하므로 불필수, 단 유지 권장) |

#### 블록 D — 검증

```bash
# EC2 에서
file ~/bootstrap-ec2.sh
# → ASCII text  (LF)

# bash 신택스 사전 체크
bash -n ~/bootstrap-ec2.sh && echo "✓ syntax OK"
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| `bad interpreter: /usr/bin/env\r` | scp 가 자동 CRLF 변환 (Windows scp 일부) | 로컬에서 `dos2unix scripts/bootstrap-ec2.sh` 후 재업로드 |
| scp `Permission denied` | .pem 권한 안 맞음 | `chmod 400 ~/.ssh/farmos-prod-1.pem` |
| 잘못된 파일 size (0 bytes) | scp 중단 | 재시도 |
| ~/bootstrap-ec2.sh 가 다른 디렉토리로 | scp `:~/` 명시 | `scp -i ... bootstrap-ec2.sh ubuntu@$EIP:~/` |
| SSH 잡혀있는 동안 다른 터미널에서 scp | 동시 세션 OK | 둘 다 동작 |

---

### Step 3.3 bootstrap 실행 (sudo bash)

#### 블록 A — 진입점

```
EC2 SSH 세션
   ↓ ubuntu 사용자 (NOPASSWD sudo)
   ↓ tee 로 stdout 동시 저장
```

#### 블록 B — 명령 + 진행 로그 ASCII

```bash
sudo bash ~/bootstrap-ec2.sh 2>&1 | tee bootstrap.log
```

기대 진행 로그:

```
[bootstrap-ec2] 2026-04-28T13:24:05+09:00 Region: ap-northeast-2
[bootstrap-ec2] 2026-04-28T13:24:05+09:00 User: root
[bootstrap-ec2] 2026-04-28T13:24:05+09:00 1/9 apt-get update + upgrade
... (apt-get 출력 ~30~120s) ...
[bootstrap-ec2] 2026-04-28T13:25:33+09:00 2/9 Docker Engine
... (Docker 설치 ~30~60s) ...
[bootstrap-ec2] 2026-04-28T13:26:14+09:00 3/9 CodeDeploy agent
... (Ruby + agent 설치 ~30~60s) ...
[bootstrap-ec2] 2026-04-28T13:26:55+09:00 4/9 AWS CLI v2
... (CLI v2 설치 ~30s) ...
[bootstrap-ec2] 2026-04-28T13:27:23+09:00 5/9 jq + rsync
[bootstrap-ec2] 2026-04-28T13:27:30+09:00 6/9 UFW firewall
[bootstrap-ec2] 2026-04-28T13:27:33+09:00 7/9 Swap (2GB)
[bootstrap-ec2] 2026-04-28T13:27:42+09:00 8/9 /opt/farmos + log rotation
[bootstrap-ec2] 2026-04-28T13:27:48+09:00 9/9 Verification
[bootstrap-ec2] 2026-04-28T13:27:48+09:00   docker --version:       Docker version 28.x.x, build xxxxxx
[bootstrap-ec2] 2026-04-28T13:27:48+09:00   docker compose version: Docker Compose version v2.x.x
[bootstrap-ec2] 2026-04-28T13:27:49+09:00   codedeploy-agent:       active
[bootstrap-ec2] 2026-04-28T13:27:49+09:00   aws --version:          aws-cli/2.x.x Python/3.x ...
[bootstrap-ec2] 2026-04-28T13:27:49+09:00   --- IAM Instance Profile ---
arn:aws:sts::242201280878:assumed-role/farmos-ec2-instance/i-xxxxxxxx
[bootstrap-ec2] 2026-04-28T13:27:50+09:00   ✓ IAM Instance Profile attached
[bootstrap-ec2] 2026-04-28T13:27:50+09:00   --- SSM Parameter Store /farmos/prod ---
[bootstrap-ec2] 2026-04-28T13:27:51+09:00   ✓ SSM keys: 28 (>= 28)
[bootstrap-ec2] 2026-04-28T13:27:51+09:00
[bootstrap-ec2] 2026-04-28T13:27:51+09:00 ============================================================
[bootstrap-ec2] 2026-04-28T13:27:51+09:00 Bootstrap complete. Instance ready for first CodeDeploy run.
[bootstrap-ec2] 2026-04-28T13:27:51+09:00 ============================================================
```

#### 블록 C — 입력값 표

| 항목 | 값 |
|---|---|
| 실행 사용자 | `sudo` (root) — 스크립트 자체가 ubuntu 권한 + sudo 사용 |
| 로그 저장 | `bootstrap.log` (`~/bootstrap.log`) |
| 예상 소요 시간 | 3~6분 (apt-get + Docker 설치 + CodeDeploy agent) |
| 종료 코드 | 0 (모든 단계 성공) |

#### 블록 D — 검증 (실행 후)

```bash
# 종료 코드
echo $?
# → 0

# bootstrap.log 의 마지막 줄
tail -3 ~/bootstrap.log
# → ✓ SSM keys: 28 (>= 28)
# → Bootstrap complete. Instance ready for first CodeDeploy run.
# → ============================================================

# 자세한 검증은 다음 §4 에서 수행
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `apt-get update` 가 멈춤 (NO_PUBKEY) | 해당 mirror 일시 장애 | 재실행 (idempotent) |
| Docker 설치 중 "/etc/apt/sources.list.d/docker.list" 충돌 | 이전 setup 잔존 | `sudo rm /etc/apt/sources.list.d/docker.list` 후 재실행 |
| `apt-get install -y` 가 다른 packagemanager 잠금 (unattended-upgrades) | EC2 launch 직후 cloud-init 의 자동 업데이트 진행 중 | 1~2분 대기 후 재실행 |
| ✗ IAM Instance Profile not attached | M4-A Step 5/6/7 또는 Step 1.7 누락 | EC2 → Instance → Actions → Security → Modify IAM role → farmos-ec2-instance attach |
| ✗ SSM keys: 0 | M4-A §3 Step 7 inline policy 누락 또는 KMS Decrypt 거부 | M4-A 캐치 #7 inline policy 의 KMSDecryptForSSM Sid 확인 |

---

## §4. 검증 (5단계)

bootstrap-ec2.sh 자체 검증 외에, 운영 시작 전 5건 추가 검증.

### Step 4.1 Docker daemon 동작

```bash
docker --version
# → Docker version 28.x.x

docker compose version
# → Docker Compose version v2.x.x

# 권한 (ubuntu 가 docker 그룹)
groups ubuntu | grep docker
# → docker  ← bootstrap 의 usermod -aG docker ubuntu 결과
# (안 보이면 이번 SSH 세션 종료 후 재접속, 또는 newgrp docker)

# hello-world 동작 (선택)
docker run --rm hello-world
# → Hello from Docker!
```

### Step 4.2 CodeDeploy agent 동작

```bash
sudo systemctl is-active codedeploy-agent
# → active

sudo systemctl is-enabled codedeploy-agent
# → enabled

# version
sudo /opt/codedeploy-agent/bin/codedeploy-agent --version
# → INFO 2026-04-28 ... Released by AWS Inc...

# 로그 확인 (에러 없는지)
sudo tail -50 /var/log/aws/codedeploy-agent/codedeploy-agent.log | tail -20
# → INFO ... [codedeploy-agent(...)] ... Started master ... 또는
#    INFO ... [codedeploy-agent(...)] ... agent finished startup successfully
```

### Step 4.3 IAM Instance Profile 검증

```bash
aws sts get-caller-identity --region ap-northeast-2
# {
#   "UserId": "AROAxxxxxxxx:i-xxxxxxxx",
#   "Account": "242201280878",
#   "Arn": "arn:aws:sts::242201280878:assumed-role/farmos-ec2-instance/i-xxxxxxxx"
# }
```

`Arn` 의 `assumed-role/farmos-ec2-instance/i-xxxx` 형식이면 OK.

### Step 4.4 SSM Parameter Store 28키 조회

```bash
aws ssm get-parameters-by-path \
  --path /farmos/prod --recursive \
  --region ap-northeast-2 \
  --query "length(Parameters)" --output text
# → 28

# 카테고리별 분포
aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region ap-northeast-2 --query "Parameters[].Name" --output text \
  | tr '\t' '\n' | awk -F'/' '{print $4}' | sort | uniq -c
#  cors=1, db=2, external=7, ghcr=1, groq=3, image=1, iot_relay=3, jwt=1, litellm=3, llm=6
```

### Step 4.5 KMS Decrypt 검증 (SecureString 1건 디코드)

```bash
aws ssm get-parameter \
  --name /farmos/prod/jwt/secret_key \
  --with-decryption \
  --region ap-northeast-2 \
  --query 'Parameter.{Name:Name, Type:Type, ValueLen:Value}' \
  --output json
# {
#   "Name": "/farmos/prod/jwt/secret_key",
#   "Type": "SecureString",
#   "ValueLen": "<실제 평문>"
# }
```

`<실제 평문>` 이 출력되면 KMS Decrypt 성공. `AccessDeniedException` 이면 M4-A §3 Step 7 의 `KMSDecryptForSSM` Sid 미부착.

### 검증 통합 한 줄

```bash
echo "=== M5 검증 ==="
echo "1. Docker:        $(docker --version | head -1)"
echo "2. Compose:       $(docker compose version | head -1)"
echo "3. CodeDeploy:    $(sudo systemctl is-active codedeploy-agent)"
echo "4. AWS CLI:       $(aws --version)"
echo "5. IAM Profile:   $(aws sts get-caller-identity --query 'Arn' --output text)"
echo "6. SSM keys:      $(aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 --query 'length(Parameters)' --output text)"
```

기대 6행 모두 정상.

---

## §5. GH Actions 첫 배포 트리거

본 단계는 M4-C 와 겹치지만, M5 입장에서 EC2 가 **첫 배포 가능 상태**임을 확인하는 의미가 있습니다.

### Step 5.1 사전 준비 (M4-C §0 항목)

- [ ] M4-C §1 의 GH Secrets 6개 등록 완료 (`gh secret list` 6행)
- [ ] EC2 태그 `App=farmos`, `Environment=prod` 부착 (Step 1.8 검증)
- [ ] EC2 running + EIP attached (Step 2.2 검증)
- [ ] bootstrap-ec2.sh 9/9 모두 ✓ (Step 3.3 + §4 5단계 검증)

### Step 5.2 트리거 (`git push origin dev`)

```bash
# 로컬
cd E:/new_my_study/FarmOS-Deploy-Test
git checkout dev
git pull origin dev
git commit --allow-empty -m "ci: trigger first deploy after EC2 bootstrap"
git push origin dev

# Actions 페이지 모니터링 (M4-C §3 참조)
gh run watch
```

### Step 5.3 EC2 측 라이프사이클 모니터링 (병행)

GH Actions 가 Trigger CodeDeploy 한 직후, EC2 SSH 세션에서:

```bash
# CodeDeploy agent 로그 실시간 추적
sudo tail -f /var/log/aws/codedeploy-agent/codedeploy-agent.log

# 또는 deployment 진행 중인 scripts.log
sudo find /opt/codedeploy-agent/deployment-root -name 'scripts.log' \
  -printf '%T@ %p\n' | sort -nr | head -1 | awk '{print $2}' \
  | xargs sudo tail -f
```

기대 흐름 (각 5블록 함정은 M4-B §2 참조):

```
[application-stop] First deploy — no docker-compose.yml yet, skipping stop
[before-install]   Ensuring /opt/farmos directories
[before-install]   No previous farmos-api container (first deploy)
[after-install]    Syncing release files to /opt/farmos
[after-install]    Detected region: ap-northeast-2
[after-install]    Fetched 28 parameters
[after-install]    SSM keys: 28 (expected 28), .env lines: 54
[after-install]    .env generated and validated
[application-start] Loading .env
[application-start] Pulling images (IMAGE_TAG=sha-xxxxxxxxxxxx)
[application-start] Starting stack
[validate-service] Healthy on attempt 4   ← /health 200 OK
```

### Step 5.4 컨테이너 동작 검증

```bash
# 3개 컨테이너 모두 Up
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
# farmos-postgres   Up   5432/tcp
# farmos-api        Up   8000/tcp
# farmos-nginx      Up   0.0.0.0:80->80/tcp

# /health (EC2 안에서)
curl -i http://localhost/health
# HTTP/1.1 200 OK
# ok

# /health (외부에서, 도메인 아직 없음 — EIP 직접)
curl -i http://13.124.xxx.xxx/health
# HTTP/1.1 200 OK
```

EIP 직접 접속이 200 OK 면 **M5 + 첫 배포 완료**. M6 (도메인 연결) 진행 가능.

---

## §6. 공통 함정 8건

### 함정 6.1 — SG 22 차단

| 가능한 원인 | 점검 방법 | 즉시 해결 |
|---|---|---|
| 운영자 IP 변경 (DHCP 재발급) | `curl -s checkip.amazonaws.com` 결과와 SG 룰 비교 | EC2 → Security Groups → farmos-prod-sg → SSH 룰의 Source 갱신 |
| ISP 가 22 outbound 차단 | `nc -vz $EIP 22` (네트워크 측 timeout 인지 verification) | 다른 네트워크/VPN 사용 또는 SSM Session Manager 활성화 |
| SG 룰 0.0.0.0/0 으로 바뀜 | `aws ec2 describe-security-groups` | `My IP/32` 으로 좁힘 |
| /32 누락 (단순 IP) | CIDR notation | `121.xxx.xxx.xxx/32` (32 추가) |

### 함정 6.2 — IAM Instance Profile 미부착

| 가능한 원인 | 해결 |
|---|---|
| Step 1.7 Advanced details 펼치지 않고 launch | EC2 → Instance → Actions → Security → Modify IAM role → farmos-ec2-instance attach (인스턴스 stop 불필요) |
| Profile 자체 없음 (M4-A 미완) | M4-A §3 Step 5/6/7 재실행 |
| Profile 의 ARN 변경됨 | `aws iam get-instance-profile --instance-profile-name farmos-ec2-instance` |

### 함정 6.3 — EC2 태그 누락 또는 OR 분리

| 증상 | 해결 |
|---|---|
| 태그 1개 (App만 또는 Environment만) | EC2 → Tags → Manage tags → 2개 모두 추가 |
| 콘솔 표시는 OK 인데 CodeDeploy "No instances" | M4-A Step 13 의 DG Tag group 1개 안에 2태그 (AND) 인지 확인 |

### 함정 6.4 — Swap 부족 (sentence-transformers OOM)

| 증상 | 해결 |
|---|---|
| `dmesg \| grep -i kill` 에 Out of memory: Killed process | swap 2GB → 4GB 늘리기 (`sudo swapoff -a` + 4GB 재생성) |
| 첫 배포 ApplicationStart 가 OOM 으로 컨테이너 종료 | bootstrap 의 swap step 재실행 (idempotent) |

### 함정 6.5 — GHCR private 레포

| 증상 | 해결 |
|---|---|
| ApplicationStart `unauthorized` | 레포 Settings → Packages → farmos-api → 가시성 = Public 변경 (권장) |
| Public 으로 바꿀 수 없음 (private 강제) | bootstrap 후 `docker login ghcr.io -u <user> -p <PAT>` (PAT scope: read:packages) |

### 함정 6.6 — IMDSv2 Hop limit 1

| 증상 | 해결 |
|---|---|
| AfterInstall 의 IMDSv2 토큰이 docker exec 안에서 401 | EC2 → Modify instance metadata options → Hop limit = 2 |
| after-install.sh 자체는 호스트에서 동작하므로 hop=1 도 OK | 컨테이너 안에서 IMDS 호출 시만 영향 |

### 함정 6.7 — UFW 가 80 차단

| 증상 | 해결 |
|---|---|
| 외부에서 80 접속 안 됨 (`curl http://$EIP/`) | `sudo ufw status` → 80/tcp 가 ALLOW 인지 |
| bootstrap-ec2.sh 6/9 가 실패했음 | `sudo ufw allow 80/tcp` |
| SG 와 UFW 둘 다 통과해야 함 | 둘 다 80 open |

### 함정 6.8 — Time skew (codedeploy-agent 인증 실패)

| 증상 | 해결 |
|---|---|
| codedeploy-agent.log 에 `clock skew detected` | `timedatectl` 결과의 `System clock synchronized: yes` 확인 |
| chrony 미설치 | `sudo apt-get install -y chrony && sudo systemctl enable --now chrony` (Ubuntu 24.04 는 systemd-timesyncd 가 기본 제공) |

---

## §7. M5 완료 검증 (단일 한 줄)

```bash
echo "=== M5 완료 검증 ==="
echo "1. Instance state:    $(aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" --query 'Reservations[].Instances[].State.Name' --output text)"
echo "2. EIP attached:      $(aws ec2 describe-addresses --filters "Name=tag:Name,Values=farmos-prod-1-eip" --query 'Addresses[0].PublicIp' --output text)"
echo "3. Tags AND match:    $(aws ec2 describe-instances --filters "Name=tag:App,Values=farmos" "Name=tag:Environment,Values=prod" "Name=instance-state-name,Values=running" --query 'Reservations[].Instances[].InstanceId' --output text)"
echo "4. IAM Profile:       $(aws ec2 describe-instances --filters "Name=tag:Name,Values=farmos-prod-1" --query 'Reservations[].Instances[].IamInstanceProfile.Arn' --output text)"
# ── EC2 ssh 후 ──
echo "5. Bootstrap done:    $(tail -1 ~/bootstrap.log 2>/dev/null | grep -q '======' && echo OK || echo MISSING)"
echo "6. SSM keys:          $(aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 --query 'length(Parameters)' --output text)"
echo "7. CodeDeploy agent:  $(sudo systemctl is-active codedeploy-agent)"
```

7행 모두 OK → **M4-C 의 첫 dev push 가능**.

---

## §8. 다음 단계

| Module | 다음 행위 |
|---|---|
| **M4-C** (이미 GH Secrets 등록 완료한 경우) | `git push origin dev` 트리거 — M4-C §2 |
| (M4-C 의 첫 배포 후) **M6** | [`m6-cloudflare-dns.md`](./m6-cloudflare-dns.md) — 도메인 + CloudFlare DNS A + Flexible SSL |

---

## §9. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | 2026-04-28 | EC2 launch 9 step + EIP + IAM Profile + 태그 + bootstrap 실행 + 5건 검증 + 8 함정 |

# M6 Runbook — CloudFlare DNS + Flexible SSL

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` §12.5 (Route 53 미사용), §16.3 (nginx CF Flexible 대응) (v0.4)
> **Plan SC**: SC-2 (`/health` 200), R12 (CF Flexible 무한 리다이렉트 방지)
> **Plan D6 (확정)**: CloudFlare SSL/TLS 모드 = `Flexible`. **Let's Encrypt 도입 절대 금지** (사용자 명시).
> **선행**: M5 완료 (EC2 + EIP), M4-C 첫 배포 성공 (`curl http://{EIP}/health` → 200)
> **후행**: 운영 모드 진입 (모니터링, 백업)
> **소요 시간**: 작업 20~30분 + Nameserver 전파 1~24시간 (대기 중 다른 검증 가능)

---

## §0. 사전 점검 (5분)

### 0.1 도메인 보유 확인

운영자가 도메인을 이미 보유 중이라고 가정합니다. 가비아 / Route 53 / Namecheap / GoDaddy / 후이즈 등 어디든 OK.

```
도메인 변수 (사용자 채움):
  {Domain} = __REPLACE_WITH_DOMAIN__   ← 예: farmos.example.com 또는 example.com
  {EIP}    = 13.124.xxx.xxx (M5 §2 캐치)
```

본 런북의 모든 명령 안의 `__REPLACE_WITH_DOMAIN__` 을 본인 도메인으로 치환하세요. (VS Code Ctrl+H)

### 0.2 EC2 EIP + 첫 배포 검증

```bash
# 외부에서 EIP 직접 호출 — 200 OK 면 M6 진행 가능
curl -sI http://__YOUR_EIP__/health
# HTTP/1.1 200 OK
# Content-Type: text/plain
```

200 OK 가 안 나오면 M5 §5.4 부터 재검증.

### 0.3 CloudFlare 계정

| 옵션 | 사전 준비 |
|---|---|
| 신규 계정 | 본 런북 §1 Step 1 부터 |
| 기존 계정 (다른 사이트가 이미 있음) | 같은 계정에 사이트 추가만 — Step 1 의 회원 가입은 skip |

### 0.4 이메일 (uio400@naver.com)

CloudFlare 가입에 사용할 이메일. 인증 메일을 받을 수 있어야 합니다.

---

## §1. CloudFlare 계정 + 사이트 추가

### Step 1.1 계정 가입 (이미 있으면 skip)

#### 블록 A — 화면 진입 경로

```
https://dash.cloudflare.com/sign-up
   ↓ Email + Password 입력
   ↓ "I'm not a robot" CAPTCHA
   ↓ [Create Account] 클릭
   ↓ 인증 메일 발송 → 메일에서 [Verify email] 클릭
   ↓ 가입 완료 → Dashboard 진입
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────┐
│ Sign up to Cloudflare                                    │
├──────────────────────────────────────────────────────────┤
│  Email *                                                 │
│   ┌────────────────────────────────────────────┐         │
│   │ uio400@naver.com                           │         │
│   └────────────────────────────────────────────┘         │
│                                                          │
│  Password *                                              │
│   ┌────────────────────────────────────────────┐         │
│   │ ●●●●●●●●●●●● (8+자, 1특수문자)              │         │
│   └────────────────────────────────────────────┘         │
│                                                          │
│  ☐ I agree to ...                                        │
│  [Create Account] ★                                      │
└──────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Email | `uio400@naver.com` |
| Password | (8+자, 영대소문자+숫자+특수문자) |
| Agreement | ☑ |

#### 블록 D — 검증

가입 완료 후 Dashboard 진입 → 좌측 상단 "Add a site" 또는 "Get started with Cloudflare" 카드 노출.

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| 인증 메일 안 옴 | 스팸 폴더 | 스팸함 확인. naver.com 은 cloudflare 메일 차단 거의 없음 |
| 한국 IP 차단 | (드물게) 신규 가입 abuse 방지 | VPN 또는 다른 네트워크에서 시도 |
| 비밀번호 8자 미만 | 정책 | 8자 이상 + 특수문자 포함 |
| 동일 이메일 재가입 | 이미 계정 있음 | "Log in" 클릭 |
| 가입 후 결제 카드 요구 | Free 플랜은 카드 불필요 | "Free" 플랜 선택 (Step 1.2 에서) |

---

### Step 1.2 사이트 추가 (Add a site)

#### 블록 A — 화면 진입 경로

```
CloudFlare Dashboard (https://dash.cloudflare.com/)
   ↓ 우측 상단 [Add] 버튼 → ⭐ "Add site" 클릭
또는 Dashboard 첫 화면 카드의 [Add a site]
   ↓
Add a site 페이지
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ Add a site                                                       │
├──────────────────────────────────────────────────────────────────┤
│  Enter your site                                                 │
│                                                                  │
│   ┌──────────────────────────────────────────────────┐           │
│   │ __REPLACE_WITH_DOMAIN__                          │           │
│   └──────────────────────────────────────────────────┘           │
│   (예: farmos.example.com 의 root 도메인 example.com 입력)        │
│                                                                  │
│   ⓘ Apex 도메인 입력 권장. 서브도메인 (api.example.com) 입력해도   │
│     CF 가 자동으로 root 등록 안내함.                              │
│                                                                  │
│  [Continue] ★                                                    │
└──────────────────────────────────────────────────────────────────┘
```

다음 페이지에서 **Free 플랜** 선택:

```
┌──────────────────────────────────────────────────────────────────┐
│ Select a plan for example.com                                    │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ⓘ Free  Pro  Business  Enterprise                              │
│  ┌──────────────────────┐                                        │
│  │ Free $0 / month     │ ★ ← 가장 아래 옵션                       │
│  │  - SSL/TLS           │                                        │
│  │  - 글로벌 CDN        │                                        │
│  │  - DDoS 보호 (basic) │                                        │
│  │  [Select plan] ★     │                                        │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Site (apex 도메인) | `__REPLACE_WITH_DOMAIN__` (root: 예 `example.com`) |
| Plan | `Free` |

#### 블록 D — 검증

CloudFlare 가 기존 DNS 레코드를 스캔하여 가져옴. 이후 페이지에서:

```
DNS records review
  ──────────────────────────────────
  Type   Name      Content        Proxy status
  A      @         (기존 IP)       (있으면 표시)
  CNAME  www       (기존)          ...
```

(기존 레코드가 없으면 빈 표 — 다음 §3 에서 신규 추가)

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| "This domain is already in use" | 다른 CF 계정 또는 같은 계정에 등록됨 | Dashboard → Websites 목록에서 확인 |
| Apex 가 아닌 서브도메인 입력 | CF 는 zone 단위 = apex | apex 입력 (예: `example.com`) |
| 한국어 .kr 도메인 | 가능 — 단 .kr 등록기관 일부에서 NS 변경 시 추가 인증 절차 | 그대로 진행, NS 변경 시 등록기관 안내 따라 진행 |
| Free 플랜이 안 보임 | 페이지 위쪽 카드에 가려짐 | 스크롤 다운 |
| Plan 선택 후 카드 정보 요구 | Pro/Business 잘못 선택 | 뒤로 → Free 선택 |

---

## §2. Nameserver 전환 (도메인 등록기관 측)

### Step 2.1 CloudFlare 가 보여주는 NS 2개 캐치

#### 블록 A — 화면 진입 경로

```
DNS records review 페이지에서 [Continue] 클릭
   ↓
Change your nameservers 페이지
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ Update your nameservers                                          │
├──────────────────────────────────────────────────────────────────┤
│ Step 1: Remove these nameservers                                 │
│  - ns1.gabia.co.kr  (또는 현재 등록기관의 NS — 예시)              │
│  - ns2.gabia.co.kr                                               │
│                                                                  │
│ Step 2: Add Cloudflare's nameservers                             │
│  ★ tom.ns.cloudflare.com         ← 본인 zone 별로 다름             │
│  ★ luna.ns.cloudflare.com        ← (예시. 실제 표시값 사용)        │
│                                                                  │
│  ⓘ It can take up to 24 hours for nameserver changes to take    │
│     effect.                                                      │
│                                                                  │
│  [Done, check nameservers] ← 변경 후 클릭                        │
└──────────────────────────────────────────────────────────────────┘
```

> ⚠️ NS 2개는 zone 마다 다릅니다. 위 `tom.ns.cloudflare.com` / `luna.ns.cloudflare.com` 는 예시 — 본인 zone 의 NS 를 정확히 사용.

#### 블록 C — 입력값 표

| 항목 | 값 |
|---|---|
| 기존 NS (제거 대상) | (도메인 등록기관 기본 NS — 예: 가비아면 `ns.gabia.co.kr` 등) |
| 신규 NS 1 | `xxx.ns.cloudflare.com` (CF 페이지의 첫 번째) |
| 신규 NS 2 | `yyy.ns.cloudflare.com` (CF 페이지의 두 번째) |

#### 블록 D — 검증 (캐치 후)

```bash
# CF 페이지의 NS 2개를 메모장에 캐치
echo "NS1=tom.ns.cloudflare.com   # 예시 — 실제 값 사용"
echo "NS2=luna.ns.cloudflare.com  # 예시 — 실제 값 사용"
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| NS 2개 모두 cloudflare.com 인데 운영자 페이지에 다른 NS 표시 | 일반적인 동작 — 무시 | 정확히 CF 가 보여주는 2개 사용 |
| NS 4개 표시됨 | Pro/Enterprise 추가 NS — Free 는 2개 | 2개만 사용 |
| zone 등록 미완 | Step 1.2 의 [Continue] 안 눌렀음 | 다시 진행 |
| NS 가 .net 도메인 | 정상 (`xxx.ns.cloudflare.com`) | 그대로 사용 |
| 같은 계정 다른 zone 의 NS | 잘못 캐치 | 본 zone 의 페이지에서 표시되는 값 |

---

### Step 2.2 등록기관에서 NS 변경

#### 블록 A — 화면 진입 경로 (가비아 예시)

```
가비아 (https://my.gabia.co.kr/)
   ↓ 도메인 통합관리 → 도메인 목록 → __DOMAIN__ 행
   ↓ 우측 [관리] 버튼
도메인 관리 페이지
   ↓ 좌측 메뉴 "네임서버" 클릭
네임서버 변경 페이지
   ↓ "1차 네임서버" / "2차 네임서버" 입력란
   ↓ CloudFlare 의 NS 2개 입력
   ↓ [저장] 또는 [변경] 버튼
```

> 등록기관마다 화면이 다르지만 공통 흐름:
> 1. 도메인 목록 → 해당 도메인 관리
> 2. "네임서버" / "Nameserver" / "DNS" 메뉴
> 3. "기본/회사 네임서버 사용" 옵션 → "직접 입력" 또는 "외부 네임서버" 선택
> 4. CloudFlare 의 NS 2개 입력
> 5. 저장

#### 블록 B — 화면 레이아웃 (가비아 일반)

```
┌──────────────────────────────────────────────────────────────────┐
│ 네임서버 변경                                                    │
├──────────────────────────────────────────────────────────────────┤
│ 도메인:  __REPLACE_WITH_DOMAIN__                                 │
│                                                                  │
│  ◯ 가비아 네임서버 사용 (기본값)                                  │
│  ⦿ 다른 네임서버 사용                              ★              │
│                                                                  │
│  1차 네임서버 *  ┌─────────────────────────┐                    │
│                  │ tom.ns.cloudflare.com   │                    │
│                  └─────────────────────────┘                    │
│  2차 네임서버 *  ┌─────────────────────────┐                    │
│                  │ luna.ns.cloudflare.com  │                    │
│                  └─────────────────────────┘                    │
│  3차 네임서버    (빈칸 OK)                                        │
│                                                                  │
│  [저장] ★                                                        │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| 1차 네임서버 | (CF Step 2.1 의 NS1) |
| 2차 네임서버 | (CF Step 2.1 의 NS2) |
| 3차/4차 | (빈칸) |

#### 블록 D — 검증 (전파 확인 — 1~24시간)

```bash
# 등록기관 측 NS 변경 직후
dig NS __REPLACE_WITH_DOMAIN__ +short
# 처음엔 등록기관의 기존 NS 가 보임
# ns.gabia.co.kr.
# ns2.gabia.co.kr.

# 1~24시간 후 (전파 완료 시)
dig NS __REPLACE_WITH_DOMAIN__ +short @1.1.1.1
# tom.ns.cloudflare.com.
# luna.ns.cloudflare.com.
```

CloudFlare 측에서도 자동 감지 — Dashboard → 본 zone → 상단 배너에서 `Pending nameserver update` → `Active` 로 전환.

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| `dig` 가 24시간 후에도 옛 NS 반환 | 등록기관 저장 실패 또는 typo | 등록기관 페이지에서 NS 정확성 재확인 + 저장 |
| 등록기관의 ".kr 도메인 NS 변경 본인 인증" 추가 절차 | .kr KISA 정책 | 안내 따라 휴대폰 인증 |
| 변경 직후 사이트 다운 | 정상 (전파 중) — DNS resolver 마다 timing 다름 | 1~6시간 대기. 그동안은 EIP 직접 접속 가능 |
| CF Dashboard 가 24시간 후에도 "Pending" | NS 부분 변경 (1차만 변경) | 1차 + 2차 모두 CF NS 로 |
| `.kr` 도메인의 한글 자국어 변환 문제 | 일부 등록기관 한글 도메인 처리 | 영문 도메인 그대로 |

---

## §3. DNS A 레코드 추가 (Proxied)

> ⚠️ NS 전환 완료 (CF 가 Active 상태) **전이라도** A 레코드는 미리 등록해 둘 수 있습니다. NS 전파 완료 시점에 즉시 동작.

### Step 3.1 A 레코드 추가

#### 블록 A — 화면 진입 경로

```
CloudFlare Dashboard
   ↓ 좌측 사이트 목록 → __REPLACE_WITH_DOMAIN__ 클릭
사이트 Overview
   ↓ 좌측 메뉴 "DNS" 그룹 → ⭐ "Records" 클릭
DNS Records 페이지
   ↓ 우측 상단 [+ Add record] 버튼 클릭
Add record 모달
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ Add record                                                       │
├──────────────────────────────────────────────────────────────────┤
│  Type *                                                          │
│   ┌──────────────┐                                               │
│   │ A          ▼ │   ★                                           │
│   └──────────────┘                                               │
│                                                                  │
│  Name *           (★ "@" = apex 도메인. "app" = app.example.com) │
│   ┌──────────────────────────────────────────────────┐           │
│   │ @                                                │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  IPv4 address *                                                  │
│   ┌──────────────────────────────────────────────────┐           │
│   │ 13.124.xxx.xxx       ← M5 §2 의 EIP             │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                  │
│  Proxy status                                                    │
│   ⦿ Proxied (오렌지 구름 ☁️) ★ — CF가 트래픽 중계 + Flexible SSL  │
│   ◯ DNS only (회색 구름)     — CF 우회, 오리진 IP 노출           │
│                                                                  │
│  TTL                                                             │
│   ┌──────────────┐                                               │
│   │ Auto       ▼ │   ★ (Proxied 일 때 자동 — 변경 불가)            │
│   └──────────────┘                                               │
│                                                                  │
│  [Cancel]                              [Save] ★                  │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표 (A 레코드 — apex)

| 필드 | 값 | 비고 |
|---|---|---|
| Type | `A` | IPv4 |
| Name | `@` | apex 도메인. 서브도메인 운영 시 `app` 또는 `api` 등 |
| IPv4 address | `13.124.xxx.xxx` | M5 §2.1 의 EIP |
| Proxy status | `Proxied` (오렌지 구름) | ★ Flexible SSL 의 핵심 — CF 가 TLS 종단 |
| TTL | `Auto` | Proxied 시 변경 불가 (CF 가 관리) |

#### 블록 D — 추가 (선택) www CNAME

```
Add record (두 번째)
   Type:  CNAME
   Name:  www
   Target: __REPLACE_WITH_DOMAIN__   (apex 자기 자신)
   Proxy: Proxied (오렌지 구름)
   TTL:   Auto
```

이 설정으로 `www.example.com` 도 같은 EIP 로 라우팅.

#### 블록 E — 검증

NS 전파 완료 후:

```bash
# CloudFlare Proxied IP 가 반환되어야 함 (EIP 가 직접 노출되지 않음)
dig A __REPLACE_WITH_DOMAIN__ +short @1.1.1.1
# 104.21.xx.xx     ← CloudFlare 의 Anycast IP (EIP 13.124.xxx.xxx 가 아님)
# 172.67.xx.xx     ← (또 다른 CF IP)

# Proxy status 가 회색 (DNS only) 인 경우 EIP 직접 노출:
# dig A __REPLACE_WITH_DOMAIN__ +short
# 13.124.xxx.xxx   ← 회색 구름이면 이렇게 나옴 (보안 약화)
```

#### 블록 F — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `dig` 결과가 EIP 그대로 (CF IP 아님) | Proxy status = DNS only (회색 구름) | DNS Records → 행 우측 구름 클릭하여 오렌지(Proxied) 로 |
| `Save` 클릭 후 "Invalid IPv4 address" | EIP 오타 (예: 마지막 옥텟에 `.0` 누락) | 정확히 4 옥텟 |
| Name `@` 대신 `__DOMAIN__` 입력 | 필드는 host name 만 (도메인 자동 붙음) | `@` 또는 빈칸 (CF 가 apex 로 자동 처리) |
| TTL 입력 가능 | Proxy = DNS only 일 때만 | Proxied 면 Auto 고정 — 정상 |
| 이미 A 레코드 있음 | Step 1.2 에서 CF 가 가져옴 | Edit 으로 IPv4 만 EIP 로 수정 |

---

## §4. SSL/TLS Flexible 모드 활성화

> Plan **D6 확정**: `Flexible`. 다른 모드 선택 시 무한 리다이렉트 또는 SSL handshake 실패. **변경 금지.**

### Step 4.1 SSL/TLS Overview → Flexible 선택

#### 블록 A — 화면 진입 경로

```
CloudFlare Dashboard → __REPLACE_WITH_DOMAIN__ zone
   ↓ 좌측 메뉴 "SSL/TLS" 그룹 → ⭐ "Overview" 클릭
SSL/TLS Overview 페이지
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ SSL/TLS                                                          │
│  Overview  Edge Certificates  Origin Server  Custom Hostnames... │
├──────────────────────────────────────────────────────────────────┤
│  Your SSL/TLS encryption mode is                                 │
│                                                                  │
│   ◯  Off (not secure)                                            │
│   ⦿  Flexible              ★★★ ← 클릭                            │
│         Browser ↔ CF: HTTPS (CF 인증서)                           │
│         CF ↔ Origin: HTTP (평문)                                 │
│   ◯  Full                                                        │
│         CF ↔ Origin: HTTPS (자가서명 OK)                         │
│   ◯  Full (strict)                                               │
│         CF ↔ Origin: HTTPS (Public CA 인증서 필요)               │
│                                                                  │
│  [Save (자동 저장)]                                              │
└──────────────────────────────────────────────────────────────────┘
```

> ⚠️ **Full / Full (strict) 절대 선택 금지** — nginx 가 80 만 listen 하므로 CF 가 origin 의 443 에 connect 하려다 실패. 사용자 명시 정책: **Let's Encrypt 도입 절대 금지**.

#### 블록 C — 입력값 표

| 필드 | 값 |
|---|---|
| Encryption mode | `Flexible` (라디오) |

#### 블록 D — 검증

```bash
# 외부에서 HTTPS 응답 (NS 전파 + CF active 상태에서)
curl -sI https://__REPLACE_WITH_DOMAIN__/health
# HTTP/2 200
# server: cloudflare
# content-type: text/plain
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| Full 선택 후 사이트 다운 (525, 526) | Origin 443 listen 안 함 | **즉시 Flexible 로 복원** |
| Full(strict) 선택 후 1014 / 526 | Origin 인증서 미설정 | 즉시 Flexible 로 복원. Let's Encrypt 도입 금지 (사용자 정책) |
| Off 로 둠 | HTTPS 미동작 | Flexible 로 변경 |
| 변경 후 즉시 적용 안 됨 | CF edge 캐시 (1~5분) | 5분 대기 |
| 라디오 클릭이 안 됨 | NS 전파 미완 | NS 전파 완료까지 대기 (Step 2.2) |

---

## §5. Always Use HTTPS = ON

### Step 5.1 Edge Certificates → Always Use HTTPS

#### 블록 A — 화면 진입 경로

```
SSL/TLS 페이지
   ↓ 상단 탭 "Edge Certificates" 클릭
Edge Certificates 페이지
   ↓ 스크롤하여 "Always Use HTTPS" 항목 찾기
```

#### 블록 B — 화면 레이아웃

```
┌──────────────────────────────────────────────────────────────────┐
│ SSL/TLS / Edge Certificates                                      │
├──────────────────────────────────────────────────────────────────┤
│ ── Always Use HTTPS ──                              [────●] ★ ON │
│   Redirect all requests with scheme "http" to "https".           │
│                                                                  │
│ ── Automatic HTTPS Rewrites ──                       [────●] ON  │
│                                                                  │
│ ── Minimum TLS Version ──                                        │
│   TLS 1.2 ▼                                                      │
│                                                                  │
│ ── Opportunistic Encryption ──                       [────●] ON  │
│                                                                  │
│ ── HTTP Strict Transport Security (HSTS) ──    [Disabled] ★ OFF  │
│   ⚠️ Flexible 모드에서 HSTS 활성화 시 HTTPS 다운 시 페이지 접근    │
│      불가 — 보류 권장                                            │
└──────────────────────────────────────────────────────────────────┘
```

#### 블록 C — 입력값 표

| 항목 | 값 | 비고 |
|---|---|---|
| Always Use HTTPS | `ON` (토글 우측) | http → https 자동 301 |
| Automatic HTTPS Rewrites | `ON` (기본) | HTML 안의 http 링크를 https 로 자동 변환 |
| Minimum TLS Version | `TLS 1.2` (기본) | TLS 1.0/1.1 비활성 |
| Opportunistic Encryption | `ON` (기본) | HTTP/2 over TLS |
| HSTS | `OFF` ★ | ⚠️ Flexible 모드에선 보류 — Full strict 안정화 후 활성화 가능 |

> ⚠️ **HSTS OFF 유지 이유**: HSTS 가 ON 인 상태에서 만약 CF 가 다운되면, 브라우저는 캐시된 HSTS 정책으로 인해 HTTP fallback 도 막혀 사용자가 사이트에 접근 불가. Flexible 모드는 origin 인증서 없음 → CF 만이 HTTPS 종단 → CF 다운 시 비상 복구 어려움. 운영 안정화 후 별도 검토.

#### 블록 D — 검증

```bash
# HTTP → HTTPS 자동 301 리다이렉트
curl -sI http://__REPLACE_WITH_DOMAIN__/health
# HTTP/1.1 301 Moved Permanently
# Location: https://__REPLACE_WITH_DOMAIN__/health
# server: cloudflare

# HTTPS 정상 응답
curl -sI https://__REPLACE_WITH_DOMAIN__/health
# HTTP/2 200
# server: cloudflare
```

#### 블록 E — 공통 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| HTTP 가 200 OK 그대로 (301 안 됨) | Always Use HTTPS = OFF | 토글 ON |
| HTTPS 가 526/525 | SSL 모드 = Full / Full(strict) | §4 로 가서 Flexible 로 복원 |
| HSTS 활성 후 CF 다운 시 사이트 다운 | HSTS preload | HSTS OFF 유지 권장 (위 설명) |
| Minimum TLS 1.0 으로 둠 | 구버전 호환성 (deprecated) | 1.2 로 |
| Always Use HTTPS 토글이 안 바뀜 | NS 전파 미완 | 1~24시간 대기 |

---

## §6. 검증 (5건)

### 6.1 DNS 전파 (Multi-resolver)

```bash
# Cloudflare resolver
dig A __REPLACE_WITH_DOMAIN__ +short @1.1.1.1
# 104.21.xx.xx
# 172.67.xx.xx

# Google resolver
dig A __REPLACE_WITH_DOMAIN__ +short @8.8.8.8
# (위와 동일)

# 한국 ISP resolver
dig A __REPLACE_WITH_DOMAIN__ +short @168.126.63.1
# (KT) 위와 동일

# NS
dig NS __REPLACE_WITH_DOMAIN__ +short @1.1.1.1
# tom.ns.cloudflare.com.
# luna.ns.cloudflare.com.
```

### 6.2 HTTP → HTTPS 리다이렉트

```bash
curl -sI -o /dev/null -w "%{http_code} %{redirect_url}\n" \
  http://__REPLACE_WITH_DOMAIN__/health
# 301 https://__REPLACE_WITH_DOMAIN__/health
```

### 6.3 HTTPS 200 OK + 헤더

```bash
curl -sI https://__REPLACE_WITH_DOMAIN__/health
# HTTP/2 200
# date: ...
# content-type: text/plain
# server: cloudflare
# cf-ray: <ray-id>-ICN
# cf-cache-status: DYNAMIC
```

`server: cloudflare` 가 보이면 CF Proxy 동작 OK.

### 6.4 무한 리다이렉트 부재 검증 (Plan R12 — 가장 중요)

```bash
# -L: follow redirects, -v: verbose
curl -L -sv https://__REPLACE_WITH_DOMAIN__/health 2>&1 \
  | grep -E "^< HTTP|^> GET" | head -20
# > GET /health HTTP/2
# < HTTP/2 200    ← 정확히 1번만 200 — 추가 redirect 없음
```

**무한 리다이렉트 발생 시** (R12 violation):
```
> GET /health HTTP/2
< HTTP/2 301 → /health
> GET /health HTTP/2
< HTTP/2 301 → /health
... (curl -L 의 max-redirects 50 까지 반복)
```

위 패턴이 보이면 nginx.conf 에 `return 301 https` 가 추가되었거나 (M2 산출물 변경) `X-Forwarded-Proto` 처리 누락. nginx.conf 의 `map $http_x_forwarded_proto $real_scheme` 블록과 `proxy_set_header X-Forwarded-Proto $real_scheme;` 라인 확인.

### 6.5 EIP 직접 접속 차단 (선택 — 권장)

CF Proxy 우회 방지를 위해 향후:

```bash
# EIP 직접 호출 — 200 OK 가 나오면 origin 노출 (보안 약화)
curl -sI http://13.124.xxx.xxx/health
# HTTP/1.1 200 OK    ← 현재 시점에는 정상 (default SG 0.0.0.0/0)

# 단단한 운영을 원할 때는:
#  - SG 80 의 Source 를 CloudFlare IP 대역 (https://www.cloudflare.com/ips/) 으로 좁힘
#  - 또는 CF Authenticated Origin Pulls (Free 플랜 가능)
```

이 부분은 **본 운영 안정화 이후 별도 작업** — M6 의 필수 단계는 아님.

---

## §7. 공통 함정 5블록

### 함정 7.1 — Flexible 무한 리다이렉트 (Plan R12)

| 증상 | 원인 | 즉시 해결 |
|---|---|---|
| `ERR_TOO_MANY_REDIRECTS` (브라우저) | nginx 가 무조건 `return 301 https://...` 응답 | nginx.conf 에서 `return 301 https` 제거 — 본 프로젝트는 이미 제거됨, 절대 추가 금지 (Plan R12) |
| `X-Forwarded-Proto: http` 인 채로 리다이렉트 발생 | CF → nginx → CF 루프 | nginx.conf 의 `map $http_x_forwarded_proto $real_scheme` 적용 + `proxy_set_header X-Forwarded-Proto $real_scheme;` |
| FastAPI 가 `RedirectResponse` 로 https 강제 | 앱단 redirect | 백엔드에서 `request.url.scheme` 검사 후 redirect 하는 코드 제거 |
| trailing slash 무한 redirect | nginx try_files 또는 fastapi auto-redirect | nginx 의 `location` 매칭 검토 |

### 함정 7.2 — Nameserver 미전환 (24시간 대기)

| 증상 | 원인 | 해결 |
|---|---|---|
| 24시간 이상 NS 미전환 | 등록기관 측 저장 실패 | 등록기관 페이지 다시 방문 → NS 정확성 확인 |
| Dashboard 가 "Pending" | 일부 resolver 만 전환됨 | `dig NS @1.1.1.1` `@8.8.8.8` `@168.126.63.1` 모두 cloudflare 로 보일 때까지 대기 |
| `.kr` KISA 정책으로 본인인증 | KR 도메인 한정 | 등록기관 안내 따라 휴대폰 인증 |
| TTL 86400 (1일) 의 잔존 | 옛 NS의 긴 TTL | 24시간 후 자연 만료 |

### 함정 7.3 — DNS Proxy off (회색 구름)

| 증상 | 원인 | 해결 |
|---|---|---|
| `dig __DOMAIN__` 가 EIP 직접 반환 | Proxy status = DNS only (회색) | DNS Records 행 우측 구름 클릭 → 오렌지(Proxied) |
| `https://` 가 524/525 (CF 인증서 없이 origin 직결) | DNS only 모드 + http 만 listening | Proxied 로 변경 — Flexible 이 자동 동작 |
| EIP 가 그대로 노출 (whois) | 보안 약화 | Proxied 활성화 (CF Anycast IP 가 표시됨) |
| Proxied 활성 후에도 origin IP leak | Mail 레코드 (MX) 또는 다른 A 레코드 노출 | 모든 A/AAAA 레코드를 Proxied 로 일관 |

### 함정 7.4 — `ERR_TOO_MANY_REDIRECTS` (구체 디버깅)

| 점검 단계 | 명령 | 기대 |
|---|---|---|
| 1. CF SSL 모드 확인 | Dashboard → SSL/TLS → Overview | `Flexible` |
| 2. nginx return 301 | `grep -n "return 301" /opt/farmos/nginx.conf` | (출력 없음) |
| 3. X-Forwarded-Proto 처리 | `grep "X-Forwarded-Proto" /opt/farmos/nginx.conf` | `proxy_set_header X-Forwarded-Proto $real_scheme;` |
| 4. FastAPI redirect | backend/app/main.py 에서 redirect 코드 검색 | (없거나 `request.url.scheme` 검사 후만) |
| 5. CF Page Rules | Dashboard → Rules → Page Rules | "Always Use HTTPS" 룰 외 다른 redirect 룰 없음 |

### 함정 7.5 — HSTS 잘못 활성화

| 증상 | 원인 | 해결 |
|---|---|---|
| HSTS 활성 후 CF/origin 다운 시 비상 복구 못함 | 브라우저가 HSTS 캐시로 HTTP fallback 차단 | HSTS OFF 유지 (Flexible 모드에선 권장) |
| HSTS 활성 후 사이트 일시 다운 시 → 전 사용자 영향 | preload 까지 들어가면 더 심각 | HSTS preload 절대 신청 금지 (안정화 전) |
| HSTS 활성 → CF 인증서 expire | CF 가 자동 갱신하지만 만약 CF 차단 시 | 사용자 정책 (Let's Encrypt 금지) 와 충돌 — Flexible + HSTS off 조합 유지 |

---

## §8. M6 완료 검증 (한 줄)

```bash
DOMAIN=__REPLACE_WITH_DOMAIN__

echo "=== M6 검증 ==="
echo "1. NS 전환:        $(dig NS $DOMAIN +short @1.1.1.1 | grep -c cloudflare.com) / 2"
echo "2. A 레코드:       $(dig A $DOMAIN +short @1.1.1.1 | head -1)"
echo "3. HTTP → HTTPS:   $(curl -sI -o /dev/null -w "%{http_code}" http://$DOMAIN/health)"
echo "4. HTTPS 200:      $(curl -sI -o /dev/null -w "%{http_code}" https://$DOMAIN/health)"
echo "5. CF Proxy:       $(curl -sI https://$DOMAIN/health | grep -i "^server:" | head -1)"
```

기대:
```
1. NS 전환:        2 / 2
2. A 레코드:       104.21.xx.xx       (CF Anycast — EIP 가 아님)
3. HTTP → HTTPS:   301
4. HTTPS 200:      200
5. CF Proxy:       server: cloudflare
```

5행 모두 OK → **end-to-end 운영 모드 진입 완료**.

---

## §9. 다음 단계 (운영 모드)

| 영역 | 다음 행위 |
|---|---|
| **모니터링** | EC2 SSH 후 `docker logs -f farmos-api`, `journalctl -u codedeploy-agent -f` |
| **백업** | bootstrap-ec2.sh 의 cron 으로 pg_dump 가 매일 03:30 동작. `/opt/farmos/data/backup-*.sql.gz` 확인 |
| **Sentry/CloudWatch** | (Phase 2 — 본 사이클에는 미포함). Plan v0.4 §15 자동 롤백 + GH Actions Run 알림이 1차 알림 |
| **2번째 인스턴스** | 단일 EC2 → 2 EC2 + ALB 로 확장 시 — 별도 PDCA |
| **CloudFlare Page Rules** | Cache Everything (정적 자산), Block direct EIP 접속 등 — 운영 안정화 후 |

---

## §10. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | 2026-04-28 | CF 가입 + 사이트 추가 + NS 전환 + DNS A(Proxied) + Flexible SSL + Always Use HTTPS + 5건 검증 + 5블록 함정 |

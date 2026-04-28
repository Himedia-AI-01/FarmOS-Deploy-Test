---
template: design
version: 1.3
description: AWS EC2 + GitHub Actions 자동 배포 Design (v0.3 - backend/.env.example 1:1 정합 + LiteLLM 단일 출구 + SSM 28키)
feature: farmos-ec2-deploy
date: 2026-04-28
last_updated: 2026-04-28
author: cto-lead (PDCA Team Mode)
project: FarmOS-Deploy-Test
version_meta: 0.4
---

# farmos-ec2-deploy Design Document

> **Summary**: 단일 EC2(t3.medium, Ubuntu 24.04) 호스트에 nginx + FastAPI + Postgres docker-compose 스택을 배치하고, GHCR 이미지 + S3 번들 + AWS CodeDeploy + Parameter Store(SecureString) + CloudFlare Flexible SSL DNS로 dev push 자동 배포 + ValidateService 훅 자동 롤백을 구현한다. GH Actions는 OIDC로 AWS Role을 assume하며 장기 액세스 키를 보유하지 않는다.
>
> **Project**: FarmOS-Deploy-Test
> **Version**: 0.2.0
> **Author**: cto-lead (Team Mode)
> **Date**: 2026-04-28
> **Status**: Draft v0.2
> **Planning Doc**: [farmos-ec2-deploy.plan.md](../../01-plan/features/farmos-ec2-deploy.plan.md)

---

## Context Anchor

> Plan 문서에서 복사. Design→Do 전환 시 컨텍스트 보존.

| Key | Value |
|-----|-------|
| **WHY** | 수동 배포·환경 분리·재현 불가 + GH Secrets 단일 평문 보관에서 벗어나, dev push = prod 라는 단일 진실 흐름과 AWS 네이티브 (OIDC + CodeDeploy + Parameter Store) 자동 롤백을 확보. |
| **WHO** | FarmOS 운영자(개발자 1~2명), 베타 농가 사용자, GitHub Actions(OIDC) → AWS CodeDeploy 파이프라인. |
| **RISK** | (1) ML 이미지 2GB+, (2) Postgres 데이터 손실, (3) IAM OIDC trust policy 오작성, (4) EC2 IAM 권한 누락, (5) CF Flexible 무한 리다이렉트, (6) appspec timeout 초과, (7) EC2 SPOF. |
| **SUCCESS** | 머지→prod ≤5분, 자동 롤백 ≤1분, /health 200, 데이터 보존, 비용 ~$39, CI ≤8분, 다운타임 ≤10초, 장기 AWS 키 0개. |
| **SCOPE** | **v0.3 — backend/.env.example 1:1 정합 + LiteLLM 단일 출구 + SSM 28키**. M1✅ → M2 compose+nginx(CF)+.env.tmpl 정합 → M3 CI → M4-A AWS 사전준비(28키 시드) → M4-B appspec+훅 → M4-C deploy.yml → M5 부트스트랩 → M6 CloudFlare DNS. |

---

## 1. Overview

### 1.1 Design Goals

1. 단일 명령(`docker compose up -d`)으로 전체 스택 가동 (단, 운영 시에는 CodeDeploy가 호출)
2. dev push 1회로 prod 반영 — 운영자 개입 0 (D10: dev = trunk, main 부재)
3. **CodeDeploy ValidateService 훅 실패 → 자동 롤백** (직전 성공 리비전으로 복귀)
4. **Parameter Store 단일 출처** — 시크릿 평문 노출 0건
5. **GH Actions OIDC** — 장기 AWS 액세스 키 미보유
6. ML 콜드스타트 비용을 빌드 타임에 흡수
7. **CloudFlare Flexible SSL** — 인증서 갱신 cron 불필요

### 1.2 Design Principles

- **Immutable Artifact**: 이미지는 `sha-{git-sha}`로 불변. `:latest`는 포인터.
- **Single Source of Truth**: 시크릿 = Parameter Store, 코드 = dev (trunk, D10), 데이터 = EC2 EBS.
- **Fail Fast & Auto-Recover**: ValidateService 실패 → CodeDeploy가 직전 리비전 자동 재배포.
- **Least Privilege**: IAM Role 권한은 ARN별 명시 (와일드카드 금지), trust policy sub 조건은 `repo:OWNER/REPO:ref:refs/heads/dev` 한정 (D10).
- **Cache Aggressively**: GH Actions buildx 캐시 + uv 캐시 + npm 캐시.

---

## 2. Architecture Options (v1.7.0)

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal (t3.small 단일 볼륨) | Option B: Pragmatic (t3.medium + 분리된 ChromaDB 볼륨) | Option C: Robust (t3.medium + 멀티스테이지 + 헬스체크 강화) |
|----------|:-:|:-:|:-:|
| **Approach** | t3.small, 단일 EBS 30GB, 같은 볼륨 | t3.medium, EBS 50GB, ChromaDB / Postgres 별도 | t3.medium, EBS 50GB, 멀티스테이지 + buildx 캐시 + healthcheck 다중 + 자동 롤백 |
| **인스턴스 비용** | ~$15/월 | ~$30/월 | ~$30/월 |
| **메모리 여유 (ML 로딩)** | 2GB → OOM 위험 | 4GB → 안정 | 4GB → 안정 |
| **롤백 자동화** | 수동 | 수동 | **자동 (CodeDeploy ValidateService)** |
| **백업 전략** | 없음 | 일 1회 pg_dump 로컬 | 일 1회 pg_dump 로컬 + 주 1회 S3 |
| **이미지 크기 최적화** | 단일 (~3GB) | 멀티스테이지 (~2GB) | 멀티스테이지 + slim + .dockerignore (~1.8GB) |
| **CI/CD 복잡도** | Low | Medium | Medium-High |
| **첫 배포 콜드스타트** | 5분+ | 2분 | **1분** |
| **위험도** | High (OOM/SPOF) | Low | **Lowest** |
| **추천** | 데모 한정 | 검토 가치 있음 | **✅ Default choice** |

**Selected**: **Option C (Robust)** — Plan §7.2 결정.

### v0.3 변경: backend/.env.example 1:1 정합 + LiteLLM 단일 출구 + SSM 7→28키

v0.2의 인프라 토폴로지는 그대로 유지하되, **변수명 정합성 + LLM 호출 일원화** 두 축이 교정되었다:

#### v0.3-A: backend/.env.example 단일 진실 소스 (D9-A)

`backend/app/core/config.py` (137 lines, pydantic_settings BaseSettings) 가 정의한 변수명·타입을 prod .env의 SoT로 채택. v0.2의 5건 불일치 정정:

| v0.2 | v0.3 (정정) | 사유 |
|---|---|---|
| `DB_URL` | `DATABASE_URL` | config.py:19 정의 |
| `JWT_SECRET` | `JWT_SECRET_KEY` | config.py:30 정의 |
| `CORS_ALLOW_ORIGINS` | `CORS_ORIGINS: list[str]` | config.py:34 — JSON 배열 형식 (예: `["http://iot.lilpa.moe"]`) |
| `LLM_OPENAI_KEY` | (제거) | D9-B — LiteLLM 단일 출구로 OPENAI_API_KEY 폐기 |
| `LLM_UPSTAGE_KEY` | `UPSTAGE_API_KEY` | config.py:125 — langchain-upstage 직접 호출 (D9-D) |

#### v0.3-B: LiteLLM Proxy 단일 출구 (D9-B)

모든 LLM 호출(diagnosis/subsidy/journal/review/ai_agent)이 `https://litellm.lilpa.moe/v1` 으로 일원화. 코드 패턴: `ChatOpenAI(base_url=settings.LITELLM_URL, api_key=settings.LITELLM_API_KEY, model=settings.LITELLM_MODEL)`. `OPENAI_API_KEY`는 SSM/.env.tmpl/compose 어디에도 등장하지 않음.

#### v0.3-C: SSM 28키 카테고리화 (D9-C)

| 카테고리 | 키 수 | 비밀? |
|---|:--:|:---:|
| `db` (url, password) | 2 | ✅ |
| `jwt` (secret_key) | 1 | ✅ |
| `cors` (origins) | 1 | ❌ |
| `litellm` (url, api_key, model) | 3 | api_key만 ✅ |
| `llm` (upstage_key, reasoning_effort, provider, model, embed_model, ai_agent_model) | 6 | upstage_key만 ✅ |
| `groq` (api_key, stt_url, stt_model) | 3 | api_key만 ✅ |
| `iot_relay` (base_url, api_key, bridge_enabled) | 3 | api_key만 ✅ |
| `external` (kma/ncpms/pesticide/food_safety/kamis_key/kamis_cert_id/kakao_rest) | 7 | 모두 ✅ |
| `image` (tag) | 1 | ❌ |
| `ghcr` (owner) | 1 | ❌ |
| **합계** | **28** | **17 SecureString / 11 String** |

> SSM Standard 무료 한도 10,000개 이내라 비용 무관 (NFR 비용 표 변동 없음).

#### v0.3-D: Frontend API 호출 패턴 분석 결과 (이슈 A/B 해결)

**grep 결과 (`fetch\(|API_BASE|/api/v1` 패턴, frontend/src 전체)**:

- 모든 hook/page가 `/api/v1/*` 경로로 호출. (예시: `useSensorData.ts:4 API_BASE='https://iot.lilpa.moe/api/v1'`, `useDailyJournal.ts:8 API_BASE='http://localhost:8000/api/v1'`, `AuthContext.tsx:3 API_BASE='http://localhost:8000/api/v1'`, `useReviewAnalysis.ts:5 API_BASE='/api/v1/reviews'`)
- 일부 코드는 `import.meta.env.VITE_FARMOS_API_BASE` 또는 `VITE_BACKEND_ORIGIN`으로 base를 주입하나 path는 항상 `/api/v1/...`.
- **결론 1 (이슈 A)**: prod의 `API_V1_PREFIX=/api/v1` 로 확정. backend/.env.example:3 와 일치.
- **결론 2 (이슈 B)**: nginx `proxy_pass http://farmos_api;` (trailing slash **없음**) 유지. 클라이언트 요청 `/api/v1/auth/login` → nginx → backend가 그대로 `/api/v1/auth/login` 수신 → FastAPI router가 `prefix=/api/v1`로 매칭. **변경 불필요**.

> 만약 nginx를 `proxy_pass http://farmos_api/;` (slash) 로 바꿨다면 `/api/`가 잘려 backend가 `/auth/login`을 받게 되고, `API_V1_PREFIX=""`로 별도 설정이 필요해진다. 본 사이클은 frontend 코드 수정 없이 정합되도록 첫 번째 옵션 채택.

---

### v0.2 변경: CodeDeploy + SSM Parameter Store + CloudFlare DNS

Option C의 토폴로지는 유지하되, 다음 4가지 축이 v0.1의 SSH 직접 배포 방식에서 AWS 네이티브로 전환되었다:

1. **배포 트리거**: SSH `docker compose up -d` → **CodeDeploy `create-deployment` + 5종 라이프사이클 훅**
2. **시크릿 저장소**: GitHub Secrets `ENV_FILE_BASE64` → **Parameter Store SecureString `/farmos/prod/*`**
3. **GH Actions → AWS 인증**: 장기 SSH 키 → **OIDC AssumeRole (장기 키 0개)**
4. **HTTPS 종단**: Let's Encrypt + Certbot cron → **CloudFlare Flexible SSL (CF가 TLS 종료, EC2는 80만 listen)**

### 2.1 인프라 토폴로지 (v0.2)

```
                  ┌──────────────────────────────┐
                  │       Internet (Users)       │
                  └──────────────┬───────────────┘
                                 │ HTTPS
                                 ▼
                  ┌──────────────────────────────┐
                  │   CloudFlare (Free plan)     │
                  │   - DNS (Proxied 오렌지)      │
                  │   - SSL/TLS: Flexible        │
                  │   - Always Use HTTPS: ON     │
                  │   - TLS 종료 (Browser ↔ CF)  │
                  └──────────────┬───────────────┘
                                 │ HTTP (80) — origin pull
                                 ▼
                  ┌──────────────────────────────┐
                  │  AWS Security Group          │
                  │  Inbound:                    │
                  │   - 80/tcp  : 0.0.0.0/0      │
                  │   (또는 CF IP 범위만 허용 강화)│
                  │   - 22/tcp  : ${OPS_IP}/32   │
                  │   * SSH는 운영자 비상용 only  │
                  │  Outbound: All               │
                  └──────────────┬──────────────┘
                                 │
                  ┌──────────────▼──────────────┐
                  │    EC2 Instance              │
                  │    t3.medium (2 vCPU, 4GB)   │
                  │    Ubuntu 24.04 LTS          │
                  │    Elastic IP : x.x.x.x      │
                  │    EBS gp3 50GB              │
                  │    Tags: App=farmos,         │
                  │          Environment=prod    │
                  │    IAM Instance Profile:     │
                  │      farmos-ec2-instance     │
                  │      - SSMManagedInstanceCore│
                  │      - EC2RoleforAWSCodeDeploy│
                  │      - ssm:Get* /farmos/prod*│
                  │      - kms:Decrypt aws/ssm   │
                  │      - s3:GetObject (deploy) │
                  │                              │
                  │  Daemons:                    │
                  │   - codedeploy-agent (Ruby)  │
                  │   - dockerd                  │
                  │                              │
                  │  ┌────────────────────────┐  │
                  │  │ Docker Network: farmos │  │
                  │  │ ┌──────────────────┐  │  │
                  │  │ │  nginx :80       │  │  │
                  │  │ │  CF Flexible     │  │  │
                  │  │ │  X-Forwarded-Proto│  │ │
                  │  │ └────────┬─────────┘  │  │
                  │  │          │ /api/*     │  │
                  │  │ ┌────────▼─────────┐  │  │
                  │  │ │  api (FastAPI)   │  │  │
                  │  │ │  ghcr.io/.../    │  │  │
                  │  │ │  farmos-api:sha  │  │  │
                  │  │ └────────┬─────────┘  │  │
                  │  │          │ asyncpg    │  │
                  │  │ ┌────────▼─────────┐  │  │
                  │  │ │  postgres:16     │  │  │
                  │  │ └──────────────────┘  │  │
                  │  └────────────────────────┘  │
                  │                              │
                  │  /opt/farmos/                │
                  │   ├── docker-compose.yml     │
                  │   ├── nginx.conf             │
                  │   ├── .env (perm 600,        │
                  │   │   AfterInstall 훅 생성)  │
                  │   ├── .prev-tag (롤백용)     │
                  │   ├── data/{postgres,chroma} │
                  │   └── dist/ (frontend build) │
                  └──────────────────────────────┘
                                 ▲
                                 │ CodeDeploy Agent ←─ S3 zip pull
                                 │
       ┌─────────────────────────┴────────────────────────────┐
       │  AWS Region: ap-northeast-2 (Seoul)                  │
       │                                                      │
       │  ┌──────────────────────────────────────────────┐    │
       │  │ S3 Bucket                                    │    │
       │  │  farmos-codedeploy-{ACCOUNT}-ap-northeast-2  │    │
       │  │   Versioning: ON, Public: BLOCKED            │    │
       │  │   Lifecycle: 30d→IA, 90d→delete              │    │
       │  └──────────────────────────────────────────────┘    │
       │  ┌──────────────────────────────────────────────┐    │
       │  │ CodeDeploy                                   │    │
       │  │   Application: farmos                        │    │
       │  │   Compute Platform: EC2/On-premises          │    │
       │  │   DeploymentGroup: farmos-prod               │    │
       │  │     Deployment Type: In-place                │    │
       │  │     Tag: App=farmos, Environment=prod        │    │
       │  │     Service Role: farmos-codedeploy-svc      │    │
       │  │     Auto-Rollback: ON                        │    │
       │  └──────────────────────────────────────────────┘    │
       │  ┌──────────────────────────────────────────────┐    │
       │  │ Parameter Store (Standard tier, free)        │    │
       │  │   /farmos/prod/db/password    SecureString   │    │
       │  │   /farmos/prod/db/url         SecureString   │    │
       │  │   /farmos/prod/jwt/secret     SecureString   │    │
       │  │   /farmos/prod/llm/openai_key SecureString   │    │
       │  │   /farmos/prod/llm/upstage_key SecureString  │    │
       │  │   /farmos/prod/image/tag      String         │    │
       │  │   /farmos/prod/ghcr/owner     String         │    │
       │  └──────────────────────────────────────────────┘    │
       │  ┌──────────────────────────────────────────────┐    │
       │  │ IAM                                          │    │
       │  │   OIDC Provider:                             │    │
       │  │     token.actions.githubusercontent.com      │    │
       │  │   Role: farmos-gh-actions-deploy             │    │
       │  │     Trust: sub=repo:OWNER/REPO:ref:refs/...  │    │
       │  │   Role: farmos-ec2-instance (Instance Profile)│   │
       │  │   Role: farmos-codedeploy-svc (Service Role) │    │
       │  └──────────────────────────────────────────────┘    │
       └──────────────────────────────────────────────────────┘
                                 ▲
                                 │ AWS API (OIDC AssumeRole)
                                 │
               ┌─────────────────┴──────────────────┐
               │   GitHub Actions (deploy.yml)      │
               │   - aws-actions/configure-aws-     │
               │     credentials@v4 (id-token)      │
               │   - docker/build-push-action@v6    │
               │     → ghcr.io/OWNER/farmos-api     │
               │   - aws s3 cp deploy-bundle.zip    │
               │   - aws deploy create-deployment   │
               │   - poll deployment status         │
               └────────────────────────────────────┘
```

### 2.2 Data Flow

```
[User Browser]
   │ HTTPS (TLS to CloudFlare)
   ▼
[CloudFlare Edge] (Flexible SSL)
   │ HTTP (origin pull, port 80)
   │ + headers: X-Forwarded-For, X-Forwarded-Proto=https, CF-Connecting-IP
   ▼
[EC2 / nginx 컨테이너 :80]
   ├── /              → /usr/share/nginx/html (React 정적)
   ├── /assets/*      → 캐시 1년 immutable
   ├── /api/*         → http://api:8000 (proxy_pass)
   ├── /health        → 200 "ok" (직접 응답, ValidateService 훅이 호출)
   └── /api/health    → api:8000/health (DB ping 포함)

[api 컨테이너]
   ├── FastAPI uvicorn (workers=2)
   ├── ENV: AfterInstall 훅이 Parameter Store에서 생성한 .env
   ├── 볼륨: /app/chroma_data → /opt/farmos/data/chroma
   └── postgres:5432 (asyncpg)

[postgres 컨테이너]
   └── 볼륨: /var/lib/postgresql/data → /opt/farmos/data/postgres
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| nginx | api (depends_on) | /api 프록시 대상 |
| api | postgres (depends_on + healthcheck) | DB 연결 대기 |
| postgres | (없음) | 가장 먼저 기동 |
| CodeDeploy 라이프사이클 | EC2 IAM Profile + S3 + Parameter Store | 번들 풀, 시크릿 조회 |
| GH Actions Deploy | OIDC + IAM Role + S3 + CodeDeploy + GHCR | 빌드/푸시/번들/트리거 |

---

## 3. Data Model

본 사이클은 **인프라 배포** 사이클이므로 신규 데이터 모델 없음.

볼륨 매핑 (v0.2):

| 컨테이너 경로 | 호스트 경로 | 목적 |
|---------------|-------------|------|
| `/var/lib/postgresql/data` | `/opt/farmos/data/postgres` | Postgres data dir |
| `/app/chroma_data` | `/opt/farmos/data/chroma` | ChromaDB persist dir |
| `/usr/share/nginx/html` | `/opt/farmos/dist` | React 정적 빌드 |
| `/etc/nginx/nginx.conf` | `/opt/farmos/nginx.conf` | nginx 설정 |
| (env_file) | `/opt/farmos/.env` | AfterInstall 훅이 SSM에서 생성 |

---

## 4. API Specification

본 사이클은 신규 API 없음. 단, **헬스체크 엔드포인트는 필수 신설**:

### 4.1 Endpoint List

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/health` | nginx 직접 응답 (200 "ok") — **CodeDeploy ValidateService 훅이 호출** | None |
| GET | `/api/health` | FastAPI 응답 (DB ping 포함) | None |

### 4.2 Detailed Specification

#### `GET /api/health`

```json
// 200 OK
{ "status": "ok", "version": "sha-abc123", "db": "ok", "chroma": "ok" }

// 503
{ "status": "degraded", "db": "down", "chroma": "ok" }
```

---

## 5. UI/UX Design

해당 없음 (인프라 사이클).

---

## 6. Error Handling

| Code | Message | Cause | Handling |
|------|---------|-------|----------|
| 502 | Bad Gateway (nginx) | api 컨테이너 다운 | docker-compose restart + ValidateService 실패 시 CodeDeploy 자동 롤백 |
| 503 | Service Unavailable | api healthy=false | 5회 실패 시 ValidateService 비-0 종료 → CodeDeploy 롤백 트리거 |
| Deploy fail (CodeDeploy) | LifecycleEvent 실패 | 훅 timeout/exit code !=0 | DeploymentGroup `Auto-rollback on failure` 설정 ON → 직전 성공 리비전으로 자동 재배포 |
| AssumeRole fail (GH Actions) | sub claim 불일치 | trust policy의 sub 조건과 워크플로우 ref 불일치 | GH Actions log + IAM Access Analyzer 검사 |
| Param fetch fail (AfterInstall) | AccessDeniedException | EC2 IAM Profile 권한 부족 | bootstrap 시 검증 명령으로 사전 감지 |

---

## 7. Sequence (v0.2 배포 시퀀스)

```
[Developer]                                           [GitHub]                                    [AWS]                                     [EC2]
    │                                                    │                                          │                                         │
    │ git push origin dev                                │                                          │                                         │
    │ ───────────────────────────────────────────────────▶                                          │                                         │
    │                                                    │ trigger deploy.yml                       │                                         │
    │                                                    ├─▶ aws-actions/configure-aws-credentials@v4│                                        │
    │                                                    │   (id-token: write)                      │                                         │
    │                                                    │   web-identity-token = OIDC JWT          │                                         │
    │                                                    │   ───────────────────────────────────────▶ STS AssumeRoleWithWebIdentity            │
    │                                                    │                                          │   (verify sub=repo:OWNER/REPO:ref/dev)  │
    │                                                    │   ◀────────────────── temp credentials ──┤                                         │
    │                                                    │                                          │                                         │
    │                                                    ├─▶ docker/login-action@v3 (ghcr.io)       │                                         │
    │                                                    ├─▶ docker/build-push-action@v6            │                                         │
    │                                                    │     cache-from/to=gha,mode=max           │                                         │
    │                                                    │     push: ghcr.io/OWNER/farmos-api:sha-* │                                         │
    │                                                    │                                          │                                         │
    │                                                    ├─▶ npm run build (frontend)               │                                         │
    │                                                    ├─▶ zip deploy bundle                      │                                         │
    │                                                    │     (appspec.yml, scripts/, nginx.conf,  │                                         │
    │                                                    │      docker-compose.yml, dist/)          │                                         │
    │                                                    ├─▶ aws s3 cp bundle.zip s3://...          │                                         │
    │                                                    │   ───────────────────────────────────────▶ S3 PutObject                            │
    │                                                    │                                          │                                         │
    │                                                    ├─▶ aws deploy create-deployment           │                                         │
    │                                                    │   ───────────────────────────────────────▶ CodeDeploy: create-deployment            │
    │                                                    │                                          │   (s3-location, app=farmos, group=prod)│
    │                                                    │                                          │                                         │
    │                                                    │                                          │                ◀── poll (codedeploy-agent)│
    │                                                    │                                          │ ─── deployment manifest ────────────────▶│
    │                                                    │                                          │                                         │
    │                                                    │                                          │                            ApplicationStop:
    │                                                    │                                          │                            └─ docker compose stop
    │                                                    │                                          │                            DownloadBundle (CD 관리):
    │                                                    │                                          │                            └─ unzip → /opt/farmos/release
    │                                                    │                                          │                            BeforeInstall:
    │                                                    │                                          │                            └─ 직전 IMAGE_TAG → .prev-tag
    │                                                    │                                          │                            Install (CD 관리):
    │                                                    │                                          │                            └─ files OVERWRITE
    │                                                    │                                          │                            AfterInstall:
    │                                                    │                                          │   ◀── ssm:GetParametersByPath ──────────│
    │                                                    │                                          │       /farmos/prod --recursive          │
    │                                                    │                                          │       --with-decryption                 │
    │                                                    │                                          │ ─── 7 parameters ───────────────────────▶│
    │                                                    │                                          │                            └─ /opt/farmos/.env (perm 600)
    │                                                    │                                          │                            ApplicationStart:
    │                                                    │                                          │                            └─ docker compose pull && up -d
    │                                                    │                                          │                            ValidateService:
    │                                                    │                                          │                            └─ 30회×2초 curl /health
    │                                                    │                                          │                            └─ 실패 시 exit 1
    │                                                    │                                          │                                         │
    │                                                    │                                          │                                실패 시:│
    │                                                    │                                          │ ◀── deployment FAILED ──────────────────│
    │                                                    │                                          │   Auto-Rollback: 직전 리비전으로 재배포 │
    │                                                    │                                          │   (.prev-tag → IMAGE_TAG 복원)          │
    │                                                    │                                          │                                         │
    │                                                    ├─▶ aws deploy get-deployment (poll)       │                                         │
    │                                                    │   ◀── Succeeded ─────────────────────────┤                                         │
    │                                                    │                                          │                                         │
    │ ◀───── GH Actions ✅ ─────────────────────────────┤                                          │                                         │
```

---

## 8. Test Plan (v2.3.0)

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1: API Tests | `/health`, `/api/health` | curl + GH Actions | Do/Check |
| L2: Routing Tests | nginx `/`, `/api/*`, `/assets/*`, **CF Flexible 무한 리다이렉트 부재** | curl + Playwright | Check |
| L3: E2E Scenario | 사용자 로그인 → IoT 페이지 (실제 prod URL) | Playwright | Check |
| L4: Deploy Resilience | 의도적 헬스체크 실패 → **CodeDeploy ValidateService 비-0 → 자동 롤백** | bash + AWS CLI | Check |
| L5: Data Persistence | Postgres 컨테이너 재기동 후 rowcount 동일 | psql | Check |
| **L6: SC-8 OIDC** | GH Actions가 OIDC AssumeRole → S3 → CodeDeploy 트리거 그린 | aws sts get-caller-identity in workflow | Check |
| **L7: SC-9 SSM** | EC2 IAM Profile이 `/farmos/prod/*` 7개 키 조회 + .env 생성 | aws ssm get-parameters-by-path on EC2 | Check |

### 8.2 L1: API Test Scenarios

| # | Endpoint | Method | Test | Expected |
|---|----------|--------|------|----------|
| 1 | `/health` | GET | nginx 직접 응답 | 200 `"ok"` |
| 2 | `/api/health` | GET | FastAPI + DB ping | 200 `.status="ok"` |
| 3 | `/api/health` (postgres 중지) | GET | DB down 시 503 | 503 `.db="down"` |
| 4 | `/api/{기존}` | GET | 회귀 | 라우트별 |

### 8.3 L2: Routing & CF Flexible Test

| # | Path | Action | Expected |
|---|------|--------|----------|
| 1 | `/` | curl GET | React `<div id="root">` |
| 2 | `/assets/index-*.js` | curl GET | `Cache-Control: max-age=31536000, immutable` |
| 3 | `/api/health` | curl GET | nginx → api 200 |
| 4 | **CF 도메인 HTTP** | `curl -I http://{도메인}/health` | 301 → CF Always Use HTTPS (CF가 처리, EC2 nginx는 응답하지 않음) |
| 5 | **CF 도메인 HTTPS** | `curl -I https://{도메인}/health` | 200 (CF → EC2:80 origin pull → nginx 응답) |
| 6 | **무한 리다이렉트 부재** | `curl -L --max-redirs 3 http://{EC2_IP}/health` | 200 (EC2 직접 접속 시 nginx는 리다이렉트 없이 200 응답) |

### 8.4 L3: E2E Scenario

| # | Scenario | Steps | Success |
|---|----------|-------|---------|
| 1 | Prod URL 접근 | Playwright `goto(https://{도메인}/)` | 로그인 페이지 |
| 2 | 로그인 → IoT | 로그인 → `/iot/dashboard` | API 401 없음 |
| 3 | 배포 후 회귀 | dev push 후 동일 시나리오 | 모두 통과, ≤ 1.5s |

### 8.5 L4: Deploy Resilience Test

```bash
# 1. 의도적 broken 이미지 푸시 (헬스체크 실패하는 backend/app/main.py 임시 수정)
# 2. dev push → GH Actions → CodeDeploy 트리거
# 3. ValidateService 훅이 30회×2초 curl /health 모두 실패 → exit 1
# 4. CodeDeploy DeploymentGroup의 Auto-Rollback 트리거
#    → 직전 성공 리비전으로 재배포
# 5. .prev-tag에서 직전 IMAGE_TAG 복원 → docker compose up -d
# 6. curl /health → 200 회복
# 성공 기준: 롤백 시작 → /health 200 회복 < 60초
# 검증: aws deploy get-deployment --deployment-id {id} --query "deploymentInfo.rollbackInfo"
```

### 8.6 L6: SC-8 OIDC Verification

```bash
# GH Actions 워크플로우 내부에서:
- name: Verify OIDC AssumeRole
  run: |
    aws sts get-caller-identity
    # 반환된 ARN이 arn:aws:sts::{ACCOUNT}:assumed-role/farmos-gh-actions-deploy/* 형식인지 확인
    # AccessKeyId가 ASIA로 시작 (임시 자격증명) 확인
```

### 8.7 L7: SC-9 Parameter Store Verification

```bash
# EC2 호스트에서 (수동 SSH):
aws ssm get-parameters-by-path \
  --path /farmos/prod \
  --recursive \
  --with-decryption \
  --region ap-northeast-2 \
  --query "Parameters[].Name"
# 기대 출력: 7개 키 모두 존재
# 추가: AfterInstall 훅 실행 후 cat /opt/farmos/.env 의 라인 수 ≥ 7
```

### 8.8 Seed Data Requirements

| Entity | Min Count | Notes |
|--------|:---------:|-------|
| Postgres `users` | 1 (admin) | 기존 마이그레이션 |
| ChromaDB `subsidy_index` | 자동 인덱싱 | `subsidy-ingest` 1회 |

---

## 9. Clean Architecture

본 사이클은 인프라 레이어이므로 코드 레이어 변경 없음. 단:

- Backend `app/main.py`에 `/health` 엔드포인트 존재 여부 확인 → 없으면 추가
- Frontend의 fetch는 모두 상대경로 `/api/...` 사용 확인
- **신규**: Backend가 환경변수를 `pydantic_settings`로 읽는지 확인 — Parameter Store에서 파생된 .env가 그대로 주입되어야 함.

---

## 10. Coding Convention Reference

| Item | Convention |
|------|-----------|
| Docker 이미지 태그 | `ghcr.io/{owner}/farmos-api:sha-{git-sha-short12}` + `:latest` |
| 컨테이너 이름 | `farmos-{service}` |
| 호스트 데이터 경로 | `/opt/farmos/data/{postgres,chroma}` |
| 환경변수 파일 | `/opt/farmos/.env` (perm 600, AfterInstall 훅이 SSM에서 생성) |
| Parameter Store 경로 | `/farmos/{env}/{category}/{key}` |
| 환경변수 변환 | `/farmos/prod/db/password` → `DB_PASSWORD` (path → UPPER_SNAKE_CASE, slash → underscore) |
| EC2 태그 | `App=farmos`, `Environment=prod` (CodeDeploy DeploymentGroup이 발견) |
| CodeDeploy Application | `farmos` |
| CodeDeploy DeploymentGroup | `farmos-prod` |
| AWS 리전 | `ap-northeast-2` |
| GH Actions job 명 | `ci`, `build-and-push`, `deploy` |
| 워크플로우 파일 | `.github/workflows/{ci,deploy}.yml` |

---

## 11. Implementation Guide

### 11.1 File Structure

```
/
├── backend/
│   ├── Dockerfile                       (M1 ✅완료)
│   └── ...
├── frontend/
│   └── (vercel.json 삭제)
├── nginx/
│   └── nginx.conf                       (M2, CF Flexible 대응)
├── scripts/
│   ├── application-stop.sh              (M4-B)
│   ├── before-install.sh                (M4-B)
│   ├── after-install.sh                 (M4-B, SSM→.env)
│   ├── application-start.sh             (M4-B)
│   ├── validate-service.sh              (M4-B)
│   └── bootstrap-ec2.sh                 (M5)
├── .github/workflows/
│   ├── ci.yml                           (M3)
│   └── deploy.yml                       (M4-C, OIDC + GHCR + S3 + CodeDeploy)
├── appspec.yml                          (M4-B)
├── docker-compose.yml                   (M2)
├── .env.tmpl                            (M2, Parameter Store 키 템플릿)
└── .dockerignore                        (M1 ✅완료)
```

### 11.2 Implementation Order

1. ✅ Backend `/health` 엔드포인트 확인/추가 (M1)
2. ✅ `backend/Dockerfile` 멀티스테이지 (M1)
3. ✅ `.dockerignore` (M1)
4. `frontend/vercel.json` 제거 (M2)
5. `nginx/nginx.conf` (M2, CF Flexible)
6. `docker-compose.yml` (M2)
7. `.env.tmpl` (M2)
8. `.github/workflows/ci.yml` (M3)
9. **AWS 사전 준비** (M4-A) — §12.6~§12.12 체크리스트
10. `appspec.yml` (M4-B)
11. `scripts/{application-stop,before-install,after-install,application-start,validate-service}.sh` (M4-B)
12. `.github/workflows/deploy.yml` (M4-C)
13. `scripts/bootstrap-ec2.sh` (M5)
14. CloudFlare DNS 추가 + Flexible SSL 활성화 + 검증 (M6)
15. dev 브랜치에 PR → CI 그린
16. dev push → CodeDeploy Succeeded + /health 200
17. 의도적 롤백 시나리오 검증 (L4)

### 11.3 Session Guide

#### Module Map (v0.2)

| Module | Scope Key | 내용 | Estimated Turns |
|--------|-----------|------|:---------------:|
| **M1** | `module-1` | ✅ Backend Dockerfile + .dockerignore (완료) | 30-40 |
| **M2** | `module-2` | docker-compose.yml + nginx.conf (CF Flexible 대응) + .env.tmpl + frontend/vercel.json 제거 | 25-35 |
| **M3** | `module-3` | .github/workflows/ci.yml | 20-25 |
| **M4-A** | `module-4a` | **AWS 사전 준비** — IAM OIDC Provider, IAM Role(GH Actions assume용), IAM Instance Profile(EC2용), S3 deploy 버킷, CodeDeploy Application/DeploymentGroup, Parameter Store 시드 (콘솔/CLI 단계별) | 40-50 |
| **M4-B** | `module-4b` | **appspec.yml + lifecycle scripts** (5개 .sh 파일) | 25-35 |
| **M4-C** | `module-4c` | **.github/workflows/deploy.yml** (OIDC + GHCR push + S3 upload + CodeDeploy trigger) | 30-40 |
| **M5** | `module-5` | EC2 부트스트랩 — `bootstrap-ec2.sh` (Ubuntu 24.04 Docker + CodeDeploy agent + IAM 프로필 검증 + swap + ufw) | 30-40 |
| **M6** | `module-6` | CloudFlare DNS 전환 + Flexible SSL + nginx 무한 리다이렉트 검증 + 백업 cron | 25-35 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 | Plan + Design v0.2 | 전체 | 30-35 (현재 세션) |
| Session 2 | Do | `--scope module-2` | 25-35 |
| Session 3 | Do | `--scope module-3,module-4a` | 60-75 |
| Session 4 | Do | `--scope module-4b,module-4c` | 55-75 |
| Session 5 | Do | `--scope module-5` | 30-40 |
| Session 6 | Do + Check | `--scope module-6` + L4/L6/L7 검증 | 50-70 |
| Session 7 | Report | 전체 | 25-30 |

---

## 12. AWS 사전 준비 체크리스트

### 12.1 EC2 인스턴스

- [ ] **AMI**: Ubuntu Server **24.04 LTS** (HVM, SSD, x86_64) — `ami-0xxx` (ap-northeast-2 최신 확인)
- [ ] **Instance Type**: t3.medium (2 vCPU, 4GB RAM)
- [ ] **EBS**: gp3 50GB, IOPS 3000, root device `/dev/sda1`
- [ ] **Key Pair**: ed25519 신규 생성, `.pem` 파일 안전 저장 (`~/.ssh/farmos-prod.pem`)
- [ ] **Subnet**: Public Subnet
- [ ] **Auto-assign Public IP**: Disabled (Elastic IP 사용)
- [ ] **IAM Instance Profile**: `farmos-ec2-instance` (§12.8 참조)
- [ ] **Tags**: `App=farmos`, `Environment=prod` (CodeDeploy 발견용 — 필수)
- [ ] **User Data** (선택): bootstrap-ec2.sh를 첫 부팅에 자동 실행

### 12.2 Elastic IP

- [ ] EIP 1개 할당 → EC2 인스턴스에 연결
- [ ] CloudFlare DNS A 레코드의 IPv4 값으로 사용 (M6)

### 12.3 Security Group

| Direction | Type | Protocol | Port | Source | Purpose |
|-----------|------|----------|------|--------|---------|
| Inbound | HTTP | TCP | 80 | 0.0.0.0/0 (또는 CF IP 범위) | nginx (CF origin pull) |
| Inbound | SSH | TCP | 22 | `${OPS_IP}/32` | 운영자 비상용 |
| Outbound | All | All | All | 0.0.0.0/0 | 이미지/SSM/S3/패키지 |

> **v0.2 변경**: GH Actions IP 화이트리스트 불필요 (CodeDeploy agent가 outbound로 polling). 443 포트도 불필요 (CF가 TLS 종료).

### 12.4 IAM Service Role for CodeDeploy (`farmos-codedeploy-svc`)

- [ ] 신규 IAM Role 생성, Trusted entity = `codedeploy.amazonaws.com`
- [ ] Managed policy attach: `AWSCodeDeployRole`

### 12.5 Route 53 (선택, 본 프로젝트는 CloudFlare 사용)

- [ ] **본 사이클은 Route 53 미사용**. M6에서 CloudFlare DNS로 처리.

### 12.6 IAM OIDC Provider for GitHub Actions

**AWS 콘솔 단계**:
1. AWS Console → IAM → Identity providers → **Add provider**
2. Provider type: **OpenID Connect**
3. Provider URL: `https://token.actions.githubusercontent.com`
4. Audience: `sts.amazonaws.com`
5. **Get thumbprint** 클릭 → 자동으로 `6938fd4d98bab03faadb97b34396831e3780aea1` 채움
6. **Add provider** → ARN 복사 (예: `arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com`)

**CLI 대안**:
```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

**검증**:
```bash
aws iam list-open-id-connect-providers
# 응답에 token.actions.githubusercontent.com 존재 확인
```

### 12.7 IAM Role for GH Actions (`farmos-gh-actions-deploy`)

**Trust Policy** (콘솔: IAM → Roles → Create role → Web identity → Identity provider 선택):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:{OWNER}/{REPO}:ref:refs/heads/dev"
        }
      }
    }
  ]
}
```

> **주의**: 최초 워크플로우 검증 시 `repo:{OWNER}/{REPO}:*` 와일드카드로 시작했다가, 정상 동작 확인 후 `:ref:refs/heads/dev` 으로 좁히기. (R9 완화, D10)

**Permissions Policy** (Inline, `farmos-deploy-permissions`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3DeployBucket",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2",
        "arn:aws:s3:::farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2/*"
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
        "arn:aws:codedeploy:ap-northeast-2:{ACCOUNT_ID}:application:farmos",
        "arn:aws:codedeploy:ap-northeast-2:{ACCOUNT_ID}:deploymentgroup:farmos/farmos-prod",
        "arn:aws:codedeploy:ap-northeast-2:{ACCOUNT_ID}:deploymentconfig:*"
      ]
    },
    {
      "Sid": "KMSDecryptForSSM",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:ap-northeast-2:{ACCOUNT_ID}:alias/aws/ssm"
    }
  ]
}
```

**검증**:
```bash
aws iam get-role --role-name farmos-gh-actions-deploy
aws iam list-role-policies --role-name farmos-gh-actions-deploy
```

### 12.8 IAM Instance Profile for EC2 (`farmos-ec2-instance`)

**Trust Policy**: `ec2.amazonaws.com`

**Managed Policies**:
- `AmazonSSMManagedInstanceCore` (Session Manager + SSM agent)
- `AmazonEC2RoleforAWSCodeDeploy` (CodeDeploy agent S3 read)

**Inline Policy** (`farmos-ec2-runtime`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ParameterStoreRead",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath"
      ],
      "Resource": "arn:aws:ssm:ap-northeast-2:{ACCOUNT_ID}:parameter/farmos/prod/*"
    },
    {
      "Sid": "KMSDecryptForSSM",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:ap-northeast-2:{ACCOUNT_ID}:alias/aws/ssm"
    },
    {
      "Sid": "S3DeployRead",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2",
        "arn:aws:s3:::farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2/*"
      ]
    }
  ]
}
```

**Instance Profile 생성** (콘솔에서 Role 생성 시 자동 생성):
```bash
aws iam list-instance-profiles-for-role --role-name farmos-ec2-instance
```

EC2 인스턴스에 attach (콘솔: EC2 → Instance → Actions → Security → Modify IAM role → `farmos-ec2-instance`)

**검증** (EC2 SSH 후):
```bash
aws sts get-caller-identity
# arn:aws:sts::{ACCOUNT}:assumed-role/farmos-ec2-instance/i-xxx 형식 확인

aws ssm get-parameters-by-path --path /farmos/prod --recursive --with-decryption --region ap-northeast-2
# 7개 파라미터 반환 확인
```

### 12.9 S3 Deploy Bucket

**콘솔 단계**:
1. S3 → Create bucket
2. Bucket name: `farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2`
3. Region: `ap-northeast-2`
4. Object Ownership: ACLs disabled
5. Block Public Access: **All blocked** (체크 모두 ON)
6. Versioning: **Enable**
7. Default encryption: SSE-S3 (Amazon S3 managed keys)
8. Create

**Lifecycle Rule** (Management → Lifecycle rules → Create):
- Rule name: `expire-old-bundles`
- Filter: prefix `bundles/`
- Transitions: 30 days → STANDARD_IA
- Expiration: 90 days

**CLI 대안**:
```bash
aws s3api create-bucket \
  --bucket farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2 \
  --region ap-northeast-2 \
  --create-bucket-configuration LocationConstraint=ap-northeast-2

aws s3api put-bucket-versioning \
  --bucket farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2 \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2 \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

**검증**:
```bash
aws s3api head-bucket --bucket farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2
aws s3api get-bucket-versioning --bucket farmos-codedeploy-{ACCOUNT_ID}-ap-northeast-2
```

### 12.10 CodeDeploy Application & DeploymentGroup

**Application 생성**:
```bash
aws deploy create-application \
  --application-name farmos \
  --compute-platform Server \
  --region ap-northeast-2
```

콘솔: CodeDeploy → Applications → Create application
- Application name: `farmos`
- Compute platform: **EC2/On-premises**

**DeploymentGroup 생성**:
- Deployment group name: `farmos-prod`
- Service role: `farmos-codedeploy-svc` (§12.4)
- Deployment type: **In-place**
- Environment configuration: **Amazon EC2 instances**
  - Tag group 1: Key=`App`, Value=`farmos`
  - Tag group 2: Key=`Environment`, Value=`prod`
- Deployment configuration: `CodeDeployDefault.AllAtOnce` (단일 인스턴스)
- Load balancer: **Disabled** (CloudFlare가 처리)
- **Rollback configuration**: ☑ Roll back when a deployment fails

**CLI 대안**:
```bash
aws deploy create-deployment-group \
  --application-name farmos \
  --deployment-group-name farmos-prod \
  --service-role-arn arn:aws:iam::{ACCOUNT_ID}:role/farmos-codedeploy-svc \
  --deployment-config-name CodeDeployDefault.AllAtOnce \
  --ec2-tag-set "ec2TagSetList=[[{Key=App,Value=farmos,Type=KEY_AND_VALUE},{Key=Environment,Value=prod,Type=KEY_AND_VALUE}]]" \
  --auto-rollback-configuration "enabled=true,events=DEPLOYMENT_FAILURE" \
  --region ap-northeast-2
```

**검증**:
```bash
aws deploy get-application --application-name farmos --region ap-northeast-2
aws deploy get-deployment-group --application-name farmos --deployment-group-name farmos-prod --region ap-northeast-2
```

### 12.11 Parameter Store 시드 (v0.3 — 28키)

> **v0.3**: backend/.env.example 1:1 정합 + LiteLLM 단일 출구. 모든 SecureString은 KMS `alias/aws/ssm` (AWS 관리 키, 무료) 사용.
>
> 카테고리별로 묶음. 미발급 키는 빈 문자열로 시드 후 운영자가 `--overwrite` 로 채움.

```bash
REGION=ap-northeast-2

# ───────────────────────────────────────────
# Category: db (2) — DB connection
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/db/password \
  --type SecureString --tier Standard \
  --description "Postgres password (rotate by 2026-Q3)" \
  --key-id alias/aws/ssm \
  --value "$(openssl rand -base64 24)" --region $REGION

aws ssm put-parameter --name /farmos/prod/db/url \
  --type SecureString --tier Standard \
  --description "DATABASE_URL — config.py:19" \
  --key-id alias/aws/ssm \
  --value "postgresql+asyncpg://farmos:$(aws ssm get-parameter --name /farmos/prod/db/password --with-decryption --query 'Parameter.Value' --output text --region $REGION)@postgres:5432/farmos" \
  --region $REGION

# ───────────────────────────────────────────
# Category: jwt (1) — config.py:30 JWT_SECRET_KEY
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/jwt/secret_key \
  --type SecureString --tier Standard \
  --description "JWT_SECRET_KEY — FastAPI signing (v0.2 jwt/secret 폐기)" \
  --key-id alias/aws/ssm \
  --value "$(openssl rand -hex 32)" --region $REGION

# ───────────────────────────────────────────
# Category: cors (1) — config.py:34 CORS_ORIGINS list[str]
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/cors/origins \
  --type String --tier Standard \
  --description 'CORS_ORIGINS — JSON array (list[str])' \
  --value '["http://iot.lilpa.moe","https://iot.lilpa.moe"]' --region $REGION

# ───────────────────────────────────────────
# Category: litellm (3) — D9-B 단일 출구
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/litellm/url \
  --type String --tier Standard \
  --description "LITELLM_URL — all LLM calls go through this proxy" \
  --value "https://litellm.lilpa.moe/v1" --region $REGION

aws ssm put-parameter --name /farmos/prod/litellm/api_key \
  --type SecureString --tier Standard \
  --description "LITELLM_API_KEY (rotate by 2026-Q3)" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION

aws ssm put-parameter --name /farmos/prod/litellm/model \
  --type String --tier Standard \
  --description "LITELLM_MODEL default — gpt-oss-20b/gpt-5-nano/gpt-5-mini/gemma-4-31b-it" \
  --value "gpt-oss-20b" --region $REGION

# ───────────────────────────────────────────
# Category: llm (6) — config.py:73~131 + UPSTAGE direct
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/llm/upstage_key \
  --type SecureString --tier Standard \
  --description "UPSTAGE_API_KEY — langchain-upstage direct (D9-D, NOT via LiteLLM)" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/reasoning_effort \
  --type String --tier Standard \
  --description "LLM_REASONING_EFFORT — minimal/low/medium/high/none" \
  --value "minimal" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/provider \
  --type String --tier Standard --value "litellm" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/model \
  --type String --tier Standard --value "llama3.1:8b" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/embed_model \
  --type String --tier Standard --value "voyage-3.5" --region $REGION

aws ssm put-parameter --name /farmos/prod/llm/ai_agent_model \
  --type String --tier Standard --value "openai/gpt-5-mini" --region $REGION

# ───────────────────────────────────────────
# Category: groq (3) — Whisper STT for journal
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/groq/api_key \
  --type SecureString --tier Standard \
  --description "GROQ_API_KEY — Whisper STT (rotate by 2026-Q3)" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION

aws ssm put-parameter --name /farmos/prod/groq/stt_url \
  --type String --tier Standard \
  --value "https://api.groq.com/openai/v1/audio/transcriptions" --region $REGION

aws ssm put-parameter --name /farmos/prod/groq/stt_model \
  --type String --tier Standard --value "whisper-large-v3" --region $REGION

# ───────────────────────────────────────────
# Category: iot_relay (3) — N100 Bridge
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/iot_relay/base_url \
  --type String --tier Standard \
  --description "IOT_RELAY_BASE_URL — N100 외부 호스트" \
  --value "http://relay.lilpa.moe:9000" --region $REGION

aws ssm put-parameter --name /farmos/prod/iot_relay/api_key \
  --type SecureString --tier Standard \
  --description "IOT_RELAY_API_KEY — Relay 공유 시크릿" \
  --key-id alias/aws/ssm \
  --value "REPLACE_ME" --region $REGION

aws ssm put-parameter --name /farmos/prod/iot_relay/bridge_enabled \
  --type String --tier Standard \
  --description 'AI_AGENT_BRIDGE_ENABLED — "true"/"false" string' \
  --value "false" --region $REGION

# ───────────────────────────────────────────
# Category: external (7) — 공공 SaaS API
# ───────────────────────────────────────────
for KEY in kma_decoding_key ncpms_key pesticide_key food_safety_key kamis_key kamis_cert_id kakao_rest_key; do
  aws ssm put-parameter --name "/farmos/prod/external/${KEY}" \
    --type SecureString --tier Standard \
    --description "External SaaS — rotate per provider policy" \
    --key-id alias/aws/ssm \
    --value "REPLACE_ME" --region $REGION
done

# ───────────────────────────────────────────
# Category: image (1) + ghcr (1)
# ───────────────────────────────────────────
aws ssm put-parameter --name /farmos/prod/image/tag \
  --type String --tier Standard \
  --description "IMAGE_TAG — GH Actions가 PutParameter로 갱신" \
  --value "latest" --region $REGION

aws ssm put-parameter --name /farmos/prod/ghcr/owner \
  --type String --tier Standard \
  --value "{your-github-org}" --region $REGION
```

**검증**:
```bash
aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 \
  --query "length(Parameters)"
# 28 (정확히 28개)

# 카테고리별 집계
aws ssm get-parameters-by-path --path /farmos/prod --recursive --region ap-northeast-2 \
  --query "Parameters[].Name" --output text | tr '\t' '\n' | awk -F'/' '{print $4}' | sort | uniq -c
# 기대: cors=1, db=2, external=7, ghcr=1, groq=3, image=1, iot_relay=3, jwt=1, litellm=3, llm=6
```

### 12.12 EC2 인스턴스 태깅

콘솔: EC2 → Instances → 선택 → Tags → Manage tags
- Key=`App`, Value=`farmos`
- Key=`Environment`, Value=`prod`
- Key=`Name`, Value=`farmos-prod-1` (선택)

CLI:
```bash
aws ec2 create-tags --resources i-xxxxxxxxxxxxxxxxx \
  --tags Key=App,Value=farmos Key=Environment,Value=prod \
  --region ap-northeast-2
```

**검증**: CodeDeploy DeploymentGroup에서 인스턴스 발견 확인:
```bash
aws deploy list-deployment-instances \
  --deployment-id d-XXXXXX --region ap-northeast-2
```

---

## 13. GitHub Secrets 목록 (v0.2)

> **v0.2 변경**: 시크릿(키/패스워드)은 모두 Parameter Store로 이동. GH Secrets에는 **OIDC ARN과 비-기밀 설정값만** 보관.

| Secret Name | Value | Purpose |
|-------------|-------|---------|
| `AWS_ROLE_ARN` | `arn:aws:iam::{ACCOUNT}:role/farmos-gh-actions-deploy` | OIDC AssumeRole 대상 |
| `AWS_REGION` | `ap-northeast-2` | AWS 리전 |
| `S3_DEPLOY_BUCKET` | `farmos-codedeploy-{ACCOUNT}-ap-northeast-2` | CodeDeploy 번들 버킷 |
| `CODEDEPLOY_APP` | `farmos` | CodeDeploy Application |
| `CODEDEPLOY_GROUP` | `farmos-prod` | DeploymentGroup |
| `GHCR_OWNER` | github username/org | GHCR push owner |
| (built-in) `GITHUB_TOKEN` | (자동) | GHCR push 자격증명 (`packages: write` 권한 필요) |

---

## 14. 시크릿 관리 전략 (v0.2)

```
AWS Systems Manager Parameter Store (Single Source of Truth)
    │
    │ /farmos/prod/db/password         SecureString (KMS aws/ssm)
    │ /farmos/prod/db/url              SecureString
    │ /farmos/prod/jwt/secret          SecureString
    │ /farmos/prod/llm/openai_key      SecureString
    │ /farmos/prod/llm/upstage_key     SecureString
    │ /farmos/prod/image/tag           String
    │ /farmos/prod/ghcr/owner          String
    │
    ├─ EC2 IAM Instance Profile (farmos-ec2-instance)
    │      │
    │      └─ AfterInstall 훅에서:
    │             aws ssm get-parameters-by-path --path /farmos/prod --recursive --with-decryption
    │             → jq 변환 → /opt/farmos/.env (perm 600)
    │
    └─ docker-compose.yml의 `env_file: ./.env` 가 컨테이너에 주입

GitHub Actions (OIDC, no long-lived keys)
    │
    └─ aws-actions/configure-aws-credentials@v4
           role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
           audience: sts.amazonaws.com
           → STS AssumeRoleWithWebIdentity → 임시 자격증명 (1시간)
```

**규칙**:
- Parameter Store 외 시크릿 보관 금지 (.env, GH Secrets 모두 ❌)
- `.env` 파일은 `.gitignore` + `.dockerignore` (M1 산출물에 이미 반영)
- `.env`는 컨테이너에 read-only mount
- 키 회전 시: `aws ssm put-parameter --overwrite` → CodeDeploy 재배포 → 자동 반영
- CloudTrail에 모든 GetParameter 호출 자동 기록 (감사 무료)

---

## 15. 헬스체크 & 롤백 절차 (v0.2)

### 15.1 헬스체크 레이어

| 레이어 | 위치 | 메커니즘 |
|--------|------|----------|
| L1: Container | docker-compose healthcheck | api: curl /health, postgres: pg_isready |
| L2: Compose dependency | `depends_on.condition: service_healthy` | api는 postgres healthy 후 |
| L3: **CodeDeploy ValidateService 훅** | `scripts/validate-service.sh` | 30회×2초 curl localhost/health |
| L4: GH Actions post-deploy | `aws deploy get-deployment` 폴링 | deployment Status=Succeeded 확인 |

### 15.2 자동 롤백 절차 (v0.2 — CodeDeploy 네이티브)

```
1. dev push → GH Actions deploy.yml 시작
2. OIDC AssumeRole → S3 zip 업로드 → CodeDeploy create-deployment
3. CodeDeploy agent on EC2가 라이프사이클 훅 순차 실행:
   ApplicationStop → BeforeInstall(.prev-tag 백업) → Install → AfterInstall(SSM→.env)
   → ApplicationStart(docker compose up) → ValidateService(30회×2초 curl)
4. ValidateService 훅이 exit 1로 실패하면:
   → CodeDeploy DeploymentGroup의 Auto-Rollback 트리거 ON
   → 직전 성공 리비전을 자동으로 재배포
   → BeforeInstall에서 백업한 .prev-tag로 IMAGE_TAG 복원
   → docker compose up -d (롤백 완료)
5. GH Actions는 aws deploy get-deployment 폴링 → 최종 status 확인
```

### 15.3 수동 롤백 절차

```bash
# AWS 콘솔: CodeDeploy → Deployments → 직전 성공 deployment 선택 → Retry deployment
# 또는 CLI:
aws deploy create-deployment \
  --application-name farmos \
  --deployment-group-name farmos-prod \
  --revision "revisionType=S3,s3Location={bucket=farmos-codedeploy-...,key=bundles/{previous-sha}.zip,bundleType=zip}" \
  --region ap-northeast-2

# 또는 EC2 SSH로 비상 수동 롤백 (CodeDeploy 우회):
ssh ubuntu@{eip}
cd /opt/farmos
PREV_TAG=$(cat .prev-tag)
export IMAGE_TAG=$PREV_TAG
docker compose pull && docker compose up -d
curl http://localhost/health
```

---

## 16. 파일별 전체 코드

### 16.1 `backend/Dockerfile` (M1 ✅완료 — 변경 없음)

> §16.1 v0.1과 동일. M1에서 산출됨. 본 문서에서는 재게재 생략.

### 16.2 `.dockerignore` (M1 ✅완료 — 변경 없음)

> §16.2 v0.1과 동일. M1에서 산출됨.

### 16.3 `nginx/nginx.conf` (v0.3 — proxy_pass slash 유지 결정 명문화)

> **v0.3 결정 (이슈 B)**: frontend grep 결과 모든 fetch가 `/api/v1/...` 경로 호출 → backend `API_V1_PREFIX=/api/v1` 와 일치해야 한다. nginx의 `proxy_pass http://farmos_api;` (trailing slash **없음**) 를 유지함으로써 클라이언트의 `/api/v1/auth/login` 요청이 backend에 그대로 전달되어 FastAPI router(`prefix=/api/v1`) 에 매칭된다. **slash를 추가하면(예: `proxy_pass http://farmos_api/;`) `/api/`가 잘려 `/auth/login`만 backend에 전달되어 매칭 실패한다 — 절대 변경 금지.**


```nginx
worker_processes auto;
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    tcp_nopush    on;
    tcp_nodelay   on;
    keepalive_timeout  65;
    server_tokens off;
    client_max_body_size 25m;

    # ────────────────────────────────────────────────
    # CloudFlare Flexible SSL 대응
    # ────────────────────────────────────────────────
    # CF → 오리진은 항상 HTTP(80) 평문.
    # CF가 X-Forwarded-Proto 헤더로 원본 클라이언트의 프로토콜을 알려줌 (https or http).
    # nginx는 절대로 무조건 HTTPS 리다이렉트 해서는 안 됨 (무한 루프).
    # 클라이언트의 실제 scheme이 필요한 경우 $real_scheme 변수를 사용.
    map $http_x_forwarded_proto $real_scheme {
        default $scheme;     # CF 헤더 없으면 nginx의 $scheme (http) 사용
        https   https;
        http    http;
    }

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain text/css text/xml application/json application/javascript
        application/xml+rss application/atom+xml image/svg+xml;

    # 로그
    log_format main '$remote_addr ($http_cf_connecting_ip) - $remote_user [$time_local] '
                    '"$request" $status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" rt=$request_time uct="$upstream_connect_time" '
                    'urt="$upstream_response_time" xfp=$http_x_forwarded_proto';
    access_log /var/log/nginx/access.log main;
    error_log  /var/log/nginx/error.log  warn;

    upstream farmos_api {
        server api:8000;
        keepalive 32;
    }

    server {
        listen 80 default_server;
        server_name _;

        root /usr/share/nginx/html;
        index index.html;

        # ⚠️ DO NOT ADD: return 301 https://$host$request_uri;
        # CloudFlare Flexible 모드에서 이 코드를 추가하면
        # CF (https) → nginx (301 https) → CF (https) → ... 무한 루프 발생.
        # HTTPS 강제는 CloudFlare 대시보드의 "Always Use HTTPS"가 처리한다.

        # CodeDeploy ValidateService 훅이 호출하는 경로 (LB-less 단일 호스트)
        location = /health {
            access_log off;
            add_header Content-Type text/plain;
            return 200 "ok";
        }

        # API 프록시 — X-Forwarded-Proto는 CF 원본 scheme을 그대로 전달
        location /api/ {
            proxy_http_version 1.1;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $http_cf_connecting_ip;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $real_scheme;  # CF 원본 scheme
            proxy_set_header Connection        "";
            proxy_read_timeout  60s;
            proxy_send_timeout  60s;
            proxy_pass http://farmos_api;
        }

        # SSE / 스트리밍
        location /api/stream/ {
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header X-Forwarded-Proto $real_scheme;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 300s;
            proxy_pass http://farmos_api;
        }

        # 정적 자산 — 1년 immutable 캐시 (CF가 추가로 캐싱)
        location ~* ^/assets/.*\.(js|css|woff2?|ttf|svg|png|jpg|jpeg|gif|webp|ico)$ {
            expires 1y;
            add_header Cache-Control "public, max-age=31536000, immutable";
            try_files $uri =404;
        }

        # SPA fallback
        location / {
            try_files $uri $uri/ /index.html;
            add_header Cache-Control "no-cache";
        }
    }
}
```

### 16.4 `docker-compose.yml` (v0.3 — env_file 단일화 + 변수명 정정)

> **v0.3 변경**: api 서비스의 `environment:` 블록에서 잘못된 키 매핑(DATABASE_URL←DB_URL, JWT_SECRET←JWT_SECRET, OPENAI_API_KEY←LLM_OPENAI_KEY, UPSTAGE_API_KEY←LLM_UPSTAGE_KEY) 모두 제거. **`env_file: ./.env` 단일 경로로 통일** — backend/.env.example 변수명 그대로 컨테이너에 주입. postgres 의 POSTGRES_PASSWORD는 `${POSTGRES_PASSWORD}` (config.py와 일치하는 이름)으로 정정.

```yaml
# docker-compose.yml — EC2 prod stack (v0.3)
# /opt/farmos/docker-compose.yml 에 배치됨 (CodeDeploy Install 단계).
# .env는 AfterInstall 훅이 Parameter Store에서 자동 생성 (perm 600, 28키).
#
# v0.3 정합 원칙:
#   - backend/.env.example 변수명을 SoT로 사용 (DATABASE_URL/JWT_SECRET_KEY/UPSTAGE_API_KEY 등)
#   - api 서비스는 env_file: ./.env 만 사용 (environment 블록 제거)
#   - LiteLLM 단일 출구 (D9-B) → OPENAI_API_KEY 항목 없음
#
# Usage (운영자 수동 시):
#   cd /opt/farmos
#   export IMAGE_TAG=sha-abc123
#   docker compose pull && docker compose up -d
services:
  postgres:
    image: postgres:16-alpine
    container_name: farmos-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER:     ${POSTGRES_USER:-farmos}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}      # /opt/farmos/.env (SSM /farmos/prod/db/password)
      POSTGRES_DB:       ${POSTGRES_DB:-farmos}
      TZ: Asia/Seoul
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    networks:
      - farmos
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-farmos} -d ${POSTGRES_DB:-farmos}"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  api:
    image: ghcr.io/${GHCR_OWNER}/farmos-api:${IMAGE_TAG:-latest}
    container_name: farmos-api
    restart: unless-stopped
    # ────────────────────────────────────────────────────────────────
    # v0.3: 단일 env_file. backend/app/core/config.py 가 요구하는 모든
    # 변수(DATABASE_URL/JWT_SECRET_KEY/CORS_ORIGINS/LITELLM_*/UPSTAGE_API_KEY/
    #  GROQ_*/IOT_RELAY_*/외부 API 키)는 .env 단일 파일에 평문 주입된다.
    # AfterInstall 훅이 SSM /farmos/prod/* 28키를 jq로 평탄화 → .env 생성.
    # ────────────────────────────────────────────────────────────────
    env_file:
      - ./.env
    environment:
      # config.py 외 컨테이너 전용 오버라이드만 명시 (.env 와 충돌 없음)
      CHROMA_DB_PATH: /app/chroma_data
      TZ: Asia/Seoul
    volumes:
      - ./data/chroma:/app/chroma_data
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - farmos
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 60s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  nginx:
    image: nginx:1.27-alpine
    container_name: farmos-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      # 443 미사용 — CloudFlare Flexible SSL이 TLS 종료
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./dist:/usr/share/nginx/html:ro
    depends_on:
      api:
        condition: service_healthy
    networks:
      - farmos
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

networks:
  farmos:
    driver: bridge
```

### 16.5 `.env.example` (legacy, v0.1) — `.env.tmpl` 로 대체

> v0.2부터는 `.env.tmpl` 사용 (§16.12 참조). `.env` 자체는 git에 커밋되지 않으며 AfterInstall 훅이 SSM에서 생성.

### 16.6 `appspec.yml` (v0.2 신설)

```yaml
# appspec.yml — CodeDeploy lifecycle (EC2/On-premises, In-place)
# 번들 zip의 root에 위치해야 함. CodeDeploy agent가 인식.
version: 0.0
os: linux

files:
  - source: /
    destination: /opt/farmos/release

file_exists_behavior: OVERWRITE

# 훅별 timeout 단위는 초 (최대 3600).
hooks:
  ApplicationStop:
    - location: scripts/application-stop.sh
      timeout: 120
      runas: ubuntu

  BeforeInstall:
    - location: scripts/before-install.sh
      timeout: 60
      runas: ubuntu

  AfterInstall:
    - location: scripts/after-install.sh
      timeout: 120
      runas: ubuntu

  ApplicationStart:
    - location: scripts/application-start.sh
      timeout: 180
      runas: ubuntu

  ValidateService:
    - location: scripts/validate-service.sh
      timeout: 90
      runas: ubuntu
```

### 16.7 `scripts/application-stop.sh` (v0.2 신설)

```bash
#!/usr/bin/env bash
# ApplicationStop 훅: 직전 컨테이너 정지 (첫 배포 시 docker-compose.yml 미존재 → || true 로 무시).
set -uo pipefail

LOG() { echo "[application-stop] $(date -Iseconds) $*"; }

if [ -f /opt/farmos/docker-compose.yml ]; then
  LOG "Stopping existing stack"
  cd /opt/farmos
  docker compose stop || true
else
  LOG "First deploy — no docker-compose.yml yet, skipping stop"
fi

exit 0
```

### 16.8 `scripts/before-install.sh` (v0.2 신설)

```bash
#!/usr/bin/env bash
# BeforeInstall 훅: 디렉토리 준비 + 직전 IMAGE_TAG 백업 (롤백용).
set -euo pipefail

LOG() { echo "[before-install] $(date -Iseconds) $*"; }

LOG "Ensuring /opt/farmos directories"
sudo mkdir -p /opt/farmos/data/postgres /opt/farmos/data/chroma /opt/farmos/dist /opt/farmos/release
sudo chown -R ubuntu:ubuntu /opt/farmos

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
```

### 16.9 `scripts/after-install.sh` (v0.3 — Parameter Store 28키 → .env, 명시적 매핑)

> **v0.3 변경**: SSM 키 트리 7→28로 확장됨에 따라 변환 매핑 테이블이 명시화됨. 단순 `path → UPPER_SNAKE_CASE`만으로는 일부 키(`db/url` → `DB_URL` 이지만 config.py는 `DATABASE_URL` 요구) 불일치. **jq에서 명시적 lookup 테이블**을 사용한다. 검증 임계값 7→28.

```bash
#!/usr/bin/env bash
# AfterInstall 훅: Parameter Store에서 /farmos/prod/* 28개 조회 → /opt/farmos/.env 생성.
# config.py 가 요구하는 변수명에 맞춰 명시적 매핑 적용.
set -euo pipefail

LOG() { echo "[after-install] $(date -Iseconds) $*"; }

# 0. release 디렉토리 → /opt/farmos 동기화
LOG "Syncing release files to /opt/farmos"
cp -f /opt/farmos/release/docker-compose.yml /opt/farmos/docker-compose.yml
cp -f /opt/farmos/release/nginx.conf         /opt/farmos/nginx.conf
if [ -d /opt/farmos/release/dist ]; then
  rsync -a --delete /opt/farmos/release/dist/ /opt/farmos/dist/
fi

# 1. EC2 region 자동 감지 (IMDSv2)
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/region)
LOG "Detected region: $REGION"

# 2. Parameter Store 조회 + 명시적 매핑 → .env
LOG "Fetching 28 parameters from /farmos/prod"
ENV_FILE=/opt/farmos/.env

# SSM path → config.py 변수명 명시 매핑 (jq lookup 테이블)
# 단순 변환으로 충분한 키는 default 분기로, 특수 케이스만 명시.
aws ssm get-parameters-by-path \
  --path /farmos/prod \
  --recursive \
  --with-decryption \
  --region "$REGION" \
  --output json \
  | jq -r '
      def name_map(n):
        # 명시적 SSM path → ENV var 매핑 (config.py 정합)
        if   n == "/farmos/prod/db/url"                       then "DATABASE_URL"
        elif n == "/farmos/prod/db/password"                  then "POSTGRES_PASSWORD"
        elif n == "/farmos/prod/jwt/secret_key"               then "JWT_SECRET_KEY"
        elif n == "/farmos/prod/cors/origins"                 then "CORS_ORIGINS"
        elif n == "/farmos/prod/litellm/url"                  then "LITELLM_URL"
        elif n == "/farmos/prod/litellm/api_key"              then "LITELLM_API_KEY"
        elif n == "/farmos/prod/litellm/model"                then "LITELLM_MODEL"
        elif n == "/farmos/prod/llm/upstage_key"              then "UPSTAGE_API_KEY"
        elif n == "/farmos/prod/llm/reasoning_effort"         then "LLM_REASONING_EFFORT"
        elif n == "/farmos/prod/llm/provider"                 then "LLM_PROVIDER"
        elif n == "/farmos/prod/llm/model"                    then "LLM_MODEL"
        elif n == "/farmos/prod/llm/embed_model"              then "EMBED_MODEL"
        elif n == "/farmos/prod/llm/ai_agent_model"           then "AI_AGENT_MODEL"
        elif n == "/farmos/prod/groq/api_key"                 then "GROQ_API_KEY"
        elif n == "/farmos/prod/groq/stt_url"                 then "GROQ_STT_URL"
        elif n == "/farmos/prod/groq/stt_model"               then "GROQ_STT_MODEL"
        elif n == "/farmos/prod/iot_relay/base_url"           then "IOT_RELAY_BASE_URL"
        elif n == "/farmos/prod/iot_relay/api_key"            then "IOT_RELAY_API_KEY"
        elif n == "/farmos/prod/iot_relay/bridge_enabled"     then "AI_AGENT_BRIDGE_ENABLED"
        elif n == "/farmos/prod/external/kma_decoding_key"    then "KMA_DECODING_KEY"
        elif n == "/farmos/prod/external/ncpms_key"           then "NCPMS_API_KEY"
        elif n == "/farmos/prod/external/pesticide_key"       then "PESTICIDE_API_KEY"
        elif n == "/farmos/prod/external/food_safety_key"     then "FOOD_SAFETY_API_KEY"
        elif n == "/farmos/prod/external/kamis_key"           then "KAMIS_API_KEY"
        elif n == "/farmos/prod/external/kamis_cert_id"       then "KAMIS_CERT_ID"
        elif n == "/farmos/prod/external/kakao_rest_key"      then "KAKAO_REST_API_KEY"
        elif n == "/farmos/prod/image/tag"                    then "IMAGE_TAG"
        elif n == "/farmos/prod/ghcr/owner"                   then "GHCR_OWNER"
        else
          # default fallback: path → UPPER_SNAKE_CASE
          n | sub("/farmos/prod/"; "") | gsub("/"; "_") | ascii_upcase
        end;
      .Parameters[] | "\(name_map(.Name))=\(.Value)"
    ' \
  > "$ENV_FILE"

# 3. 비-비밀 default 추가 (config.py 가 default를 갖지만 명시적으로 박는 게 운영 가시성 좋음)
cat >> "$ENV_FILE" <<'DEFAULTS'
PROJECT_NAME=FarmOS
API_V1_PREFIX=/api/v1
APP_TIMEZONE=Asia/Seoul
POSTGRES_USER=farmos
POSTGRES_DB=farmos
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
CHROMA_DB_PATH=/app/chroma_data
EMBED_DIM=1024
REVIEW_ANALYSIS_BATCH_SIZE=40
REVIEW_ANALYSIS_MAX_RETRIES=2
AI_AGENT_LLM_INTERVAL=300
AI_AGENT_RULE_INTERVAL=30
AI_AGENT_MIRROR_TTL_DAYS=30
AI_AGENT_BACKFILL_PAGE_SIZE=200
SOIL_MOISTURE_LOW=55.0
SOIL_MOISTURE_HIGH=70.0
FARM_NX=84
FARM_NY=106
ENV=production
LOG_LEVEL=INFO
DEFAULTS

# 4. 권한 잠금
chmod 600 "$ENV_FILE"
chown ubuntu:ubuntu "$ENV_FILE"

# 5. 검증 — SSM에서 정확히 28개 + default 추가분 → 50줄 이상
SSM_COUNT=$(aws ssm get-parameters-by-path --path /farmos/prod --recursive \
  --region "$REGION" --query "length(Parameters)" --output text)
LINE_COUNT=$(wc -l < "$ENV_FILE")
LOG "SSM keys: $SSM_COUNT (expected 28), .env lines: $LINE_COUNT"
if [ "$SSM_COUNT" -lt 28 ]; then
  LOG "ERROR: Expected at least 28 SSM parameters, got $SSM_COUNT"
  exit 1
fi

# 6. 핵심 변수 사전 정합 검증 (R13 완화)
for KEY in DATABASE_URL JWT_SECRET_KEY LITELLM_URL LITELLM_API_KEY UPSTAGE_API_KEY POSTGRES_PASSWORD; do
  if ! grep -q "^${KEY}=" "$ENV_FILE"; then
    LOG "ERROR: Critical variable $KEY missing in .env"
    exit 1
  fi
done

LOG ".env generated and validated"
exit 0
```

> **매핑 정합 검증** (`bkend-expert`):
> - `/farmos/prod/db/url` → `DATABASE_URL` (config.py:19) ✅
> - `/farmos/prod/jwt/secret_key` → `JWT_SECRET_KEY` (config.py:30) ✅
> - `/farmos/prod/litellm/url` → `LITELLM_URL` (config.py:63) ✅ — **사용자 질문 직접 응답: 이 키가 LITELLM_URL의 출처**
> - `/farmos/prod/llm/upstage_key` → `UPSTAGE_API_KEY` (config.py:125, D9-D 보존) ✅
> - `/farmos/prod/cors/origins` → `CORS_ORIGINS` (config.py:34, list[str]) ✅ — JSON 배열 문자열을 그대로 주입, pydantic이 파싱.
> - default 분기: `/farmos/prod/image/tag` → `IMAGE_TAG`, `/farmos/prod/ghcr/owner` → `GHCR_OWNER` (단순 변환) ✅

### 16.10 `scripts/application-start.sh` (v0.2 신설)

```bash
#!/usr/bin/env bash
# ApplicationStart 훅: docker-compose 기동.
set -euo pipefail

LOG() { echo "[application-start] $(date -Iseconds) $*"; }

cd /opt/farmos

# .env에 IMAGE_TAG, GHCR_OWNER 포함 (Parameter Store 출처)
LOG "Loading .env"
set -a
# shellcheck disable=SC1091
source ./.env
set +a

# GHCR 익명 풀이 가능한 public 레포면 login 불필요. private 레포는 별도 처리 필요.
LOG "Pulling images (IMAGE_TAG=$IMAGE_TAG)"
docker compose pull

LOG "Starting stack"
docker compose up -d --remove-orphans

LOG "Pruning dangling images (older than 72h)"
docker image prune -f --filter "until=72h" || true

exit 0
```

### 16.11 `scripts/validate-service.sh` (v0.2 신설)

```bash
#!/usr/bin/env bash
# ValidateService 훅: 30회 × 2초 = 최대 60초 동안 /health 폴링.
# 실패 시 exit 1 → CodeDeploy DeploymentGroup의 Auto-Rollback 트리거.
set -uo pipefail

LOG() { echo "[validate-service] $(date -Iseconds) $*"; }

for i in {1..30}; do
  if curl -fs -o /dev/null http://localhost/health; then
    LOG "Healthy on attempt $i"
    exit 0
  fi
  sleep 2
done

LOG "ERROR: /health failed after 30 attempts (60 seconds)"
LOG "Last status:"
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost/health || true
docker ps --format 'table {{.Names}}\t{{.Status}}'
exit 1
```

### 16.12 `.env.tmpl` (v0.3 — backend/.env.example 1:1 정합 + 28키 SSM 매핑)

> **v0.3 변경**: backend/.env.example (121 lines, config.py 가 요구하는 모든 변수)와 1:1 매칭. `${SSM:/farmos/prod/...}` 표기는 사람을 위한 주석 — 실제 치환은 after-install.sh가 수행. 카테고리 주석을 backend/.env.example과 동일 구조로 보존.

```dotenv
# .env.tmpl — v0.3 (backend/.env.example 1:1 정합 + LiteLLM 단일 출구)
# 실제 .env는 AfterInstall 훅이 Parameter Store에서 자동 생성 (perm 600, 28 SSM keys + defaults).
# 본 파일은 git에 커밋되며, 운영자가 어떤 SSM 키가 필요한지 한눈에 보기 위함.
#
# ${SSM:/farmos/prod/...} 표기는 사람을 위한 주석 — 실제 치환은 after-install.sh가 수행.

# ── 기본 설정 ──────────────────────────────────────────────────────────
PROJECT_NAME=FarmOS
API_V1_PREFIX=/api/v1
APP_TIMEZONE=Asia/Seoul

# ── 데이터베이스 (Postgres) ─────────────────────────────────────────────
POSTGRES_USER=farmos
POSTGRES_DB=farmos
POSTGRES_PASSWORD=${SSM:/farmos/prod/db/password}
DATABASE_URL=${SSM:/farmos/prod/db/url}
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800

# 벡터 데이터베이스 (ChromaDB)
CHROMA_DB_PATH=/app/chroma_data

# ── 보안 및 인증 ────────────────────────────────────────────────────────
JWT_SECRET_KEY=${SSM:/farmos/prod/jwt/secret_key}

# CORS 허용 도메인 (JSON 배열 — pydantic list[str] 파싱)
CORS_ORIGINS=${SSM:/farmos/prod/cors/origins}

# ── 외부 API (공공데이터/지도) — 모두 SSM SecureString ────────────────
KMA_DECODING_KEY=${SSM:/farmos/prod/external/kma_decoding_key}
NCPMS_API_KEY=${SSM:/farmos/prod/external/ncpms_key}
PESTICIDE_API_KEY=${SSM:/farmos/prod/external/pesticide_key}
FOOD_SAFETY_API_KEY=${SSM:/farmos/prod/external/food_safety_key}
KAMIS_API_KEY=${SSM:/farmos/prod/external/kamis_key}
KAMIS_CERT_ID=${SSM:/farmos/prod/external/kamis_cert_id}
KAKAO_REST_API_KEY=${SSM:/farmos/prod/external/kakao_rest_key}

# ── LLM & AI 서비스 — LiteLLM 단일 출구 (D9-B) ────────────────────────
# OPENAI_API_KEY 는 의도적으로 정의하지 않는다 (LiteLLM 경유)
LITELLM_URL=${SSM:/farmos/prod/litellm/url}
LITELLM_API_KEY=${SSM:/farmos/prod/litellm/api_key}
LITELLM_MODEL=${SSM:/farmos/prod/litellm/model}

# Groq (Whisper STT — 영농일지)
GROQ_API_KEY=${SSM:/farmos/prod/groq/api_key}
GROQ_STT_URL=${SSM:/farmos/prod/groq/stt_url}
GROQ_STT_MODEL=${SSM:/farmos/prod/groq/stt_model}

# 리뷰 분석 및 기타 LLM 설정
LLM_PROVIDER=${SSM:/farmos/prod/llm/provider}
LLM_MODEL=${SSM:/farmos/prod/llm/model}
LLM_REASONING_EFFORT=${SSM:/farmos/prod/llm/reasoning_effort}
EMBED_MODEL=${SSM:/farmos/prod/llm/embed_model}
EMBED_DIM=1024
REVIEW_ANALYSIS_BATCH_SIZE=40
REVIEW_ANALYSIS_MAX_RETRIES=2

# ── AI Agent (IoT 제어) ──────────────────────────────────────────────────
AI_AGENT_MODEL=${SSM:/farmos/prod/llm/ai_agent_model}
AI_AGENT_LLM_INTERVAL=300
AI_AGENT_RULE_INTERVAL=30

# IoT Relay Server Bridge — N100 외부 호스트
IOT_RELAY_BASE_URL=${SSM:/farmos/prod/iot_relay/base_url}
IOT_RELAY_API_KEY=${SSM:/farmos/prod/iot_relay/api_key}
AI_AGENT_BRIDGE_ENABLED=${SSM:/farmos/prod/iot_relay/bridge_enabled}
AI_AGENT_MIRROR_TTL_DAYS=30
AI_AGENT_BACKFILL_PAGE_SIZE=200

# 센서 임계값
SOIL_MOISTURE_LOW=55.0
SOIL_MOISTURE_HIGH=70.0

# ── 기타 설정 ────────────────────────────────────────────────────────────
FARM_NX=84
FARM_NY=106

# ── 공익직불 RAG (Upstage 직접 호출 — D9-D 보존) ────────────────────
UPSTAGE_API_KEY=${SSM:/farmos/prod/llm/upstage_key}
SUBSIDY_LLM_MODEL=google/gemma-4-31b-it
SUBSIDY_RERANKER_MODEL=dragonkue/bge-reranker-v2-m3-ko
SUBSIDY_PDF_PATH=data/gov/2026_공익직불_시행지침.pdf
SUBSIDY_MARKDOWN_CACHE_PATH=data/gov/2026_공익직불_시행지침.md

# ── Image / Registry ────────────────────────────────────────────────────
IMAGE_TAG=${SSM:/farmos/prod/image/tag}
GHCR_OWNER=${SSM:/farmos/prod/ghcr/owner}

# ── 운영 메타 ───────────────────────────────────────────────────────────
TZ=Asia/Seoul
ENV=production
LOG_LEVEL=INFO
```

### 16.13 `.github/workflows/deploy.yml` (v0.2 신설 — OIDC + GHCR + S3 + CodeDeploy)

```yaml
name: Deploy (prod)

on:
  push:
    branches: [ dev ]   # D10: dev = trunk = prod 트리거 (main 브랜치 부재)
  workflow_dispatch:

concurrency:
  group: deploy-prod
  cancel-in-progress: false

permissions:
  id-token: write       # OIDC AssumeRole용 (필수)
  contents: read
  packages: write       # GHCR push용

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ secrets.GHCR_OWNER }}/farmos-api
  AWS_REGION: ${{ secrets.AWS_REGION }}

jobs:
  build-and-push:
    name: Build & Push to GHCR
    runs-on: ubuntu-latest
    outputs:
      image_tag: ${{ steps.meta.outputs.image_tag }}
    steps:
      - uses: actions/checkout@v4

      - name: Compute tag
        id: meta
        run: |
          TAG="sha-${GITHUB_SHA::12}"
          echo "image_tag=$TAG" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v3

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build & Push (backend)
        uses: docker/build-push-action@v6
        with:
          context: .
          file: backend/Dockerfile
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ steps.meta.outputs.image_tag }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-frontend:
    name: Build Frontend
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
      - uses: actions/upload-artifact@v4
        with:
          name: frontend-dist
          path: frontend/dist
          retention-days: 7

  deploy:
    name: Deploy via CodeDeploy
    needs: [ build-and-push, build-frontend ]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Download frontend dist
        uses: actions/download-artifact@v4
        with:
          name: frontend-dist
          path: dist

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
          role-session-name: gh-actions-${{ github.run_id }}
          role-duration-seconds: 3600

      - name: Verify OIDC AssumeRole
        run: aws sts get-caller-identity

      - name: Update IMAGE_TAG in Parameter Store
        run: |
          aws ssm put-parameter \
            --name /farmos/prod/image/tag \
            --type String \
            --value "${{ needs.build-and-push.outputs.image_tag }}" \
            --overwrite \
            --region ${{ env.AWS_REGION }}

      - name: Build CodeDeploy bundle
        run: |
          mkdir -p bundle/scripts
          cp appspec.yml bundle/
          cp docker-compose.yml bundle/
          cp nginx/nginx.conf bundle/nginx.conf
          cp scripts/application-stop.sh   bundle/scripts/
          cp scripts/before-install.sh     bundle/scripts/
          cp scripts/after-install.sh      bundle/scripts/
          cp scripts/application-start.sh  bundle/scripts/
          cp scripts/validate-service.sh   bundle/scripts/
          chmod +x bundle/scripts/*.sh
          cp -r dist bundle/dist
          cd bundle && zip -rq ../deploy-bundle.zip . && cd ..
          ls -lh deploy-bundle.zip

      - name: Upload bundle to S3
        id: s3upload
        run: |
          BUNDLE_KEY="bundles/${{ needs.build-and-push.outputs.image_tag }}.zip"
          aws s3 cp deploy-bundle.zip "s3://${{ secrets.S3_DEPLOY_BUCKET }}/${BUNDLE_KEY}" \
            --region ${{ env.AWS_REGION }}
          echo "bundle_key=${BUNDLE_KEY}" >> "$GITHUB_OUTPUT"

      - name: Trigger CodeDeploy
        id: deploy
        run: |
          DEPLOY_ID=$(aws deploy create-deployment \
            --application-name "${{ secrets.CODEDEPLOY_APP }}" \
            --deployment-group-name "${{ secrets.CODEDEPLOY_GROUP }}" \
            --deployment-config-name CodeDeployDefault.AllAtOnce \
            --description "git ${{ github.sha }} by ${{ github.actor }}" \
            --revision "{\"revisionType\":\"S3\",\"s3Location\":{\"bucket\":\"${{ secrets.S3_DEPLOY_BUCKET }}\",\"key\":\"${{ steps.s3upload.outputs.bundle_key }}\",\"bundleType\":\"zip\"}}" \
            --region ${{ env.AWS_REGION }} \
            --query 'deploymentId' --output text)
          echo "deployment_id=$DEPLOY_ID" >> "$GITHUB_OUTPUT"
          echo "Deployment ID: $DEPLOY_ID"

      - name: Wait for deployment
        run: |
          aws deploy wait deployment-successful \
            --deployment-id ${{ steps.deploy.outputs.deployment_id }} \
            --region ${{ env.AWS_REGION }}

      - name: Show deployment summary
        if: always()
        run: |
          aws deploy get-deployment \
            --deployment-id ${{ steps.deploy.outputs.deployment_id }} \
            --region ${{ env.AWS_REGION }} \
            --query 'deploymentInfo.{Status:status,Created:createTime,Complete:completeTime,Rollback:rollbackInfo}'
```

### 16.14 `scripts/bootstrap-ec2.sh` (v0.2 — Ubuntu 24.04 + CodeDeploy agent)

```bash
#!/usr/bin/env bash
# scripts/bootstrap-ec2.sh — 신규 EC2 (Ubuntu 24.04 LTS) 1회 부트스트랩
# 사용:
#   ssh -i farmos-prod.pem ubuntu@<eip> 'bash -s' < scripts/bootstrap-ec2.sh
set -euo pipefail

LOG() { echo "[bootstrap] $(date -Iseconds) $*"; }

REGION="ap-northeast-2"   # ap-northeast-2 = Seoul

LOG "0/9 OS update"
sudo apt-get update -y
sudo apt-get upgrade -y

LOG "1/9 Base tools"
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release ufw rsync git unzip jq awscli ruby-full wget

LOG "2/9 Docker Engine (Ubuntu 24.04 official)"
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu

LOG "3/9 CodeDeploy agent"
cd /home/ubuntu
wget -q https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install
chmod +x ./install
sudo ./install auto
sudo systemctl enable codedeploy-agent
sudo systemctl status codedeploy-agent --no-pager || true

LOG "4/9 Swap (4GB) — ML 메모리 압박 완화"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

LOG "5/9 UFW firewall (80 + 22)"
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
# 443 미사용 (CloudFlare Flexible)
sudo ufw --force enable

LOG "6/9 /opt/farmos working directory"
sudo mkdir -p /opt/farmos/data/postgres /opt/farmos/data/chroma /opt/farmos/dist /opt/farmos/release
sudo chown -R ubuntu:ubuntu /opt/farmos

LOG "7/9 IAM Profile verification (Parameter Store read)"
aws sts get-caller-identity --region "$REGION"
echo "Testing /farmos/prod/* read..."
aws ssm get-parameters-by-path \
  --path /farmos/prod \
  --recursive \
  --with-decryption \
  --region "$REGION" \
  --query "length(Parameters)" \
  --output text || {
    LOG "ERROR: IAM Instance Profile missing ssm:GetParametersByPath or kms:Decrypt"
    LOG "Check farmos-ec2-instance role inline policy"
    exit 1
  }
LOG "IAM verification OK"

LOG "8/9 journald log rotation"
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=500M/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald

LOG "9/9 Cron — docker prune + pg_dump"
( crontab -l 2>/dev/null; cat <<'EOF'
# Weekly docker prune
0 4 * * 0 /usr/bin/docker system prune -af --filter "until=168h" >/dev/null 2>&1
# Daily pg_dump (local only — S3 백업은 향후 phase)
30 3 * * * /usr/bin/docker exec farmos-postgres pg_dump -U farmos farmos | gzip > /opt/farmos/data/backup-$(date +\%F).sql.gz
# Retain 14 days
0 5 * * * find /opt/farmos/data -name "backup-*.sql.gz" -mtime +14 -delete
EOF
) | crontab -

LOG "Bootstrap complete. Logout/login to apply docker group, then enable CodeDeploy DeploymentGroup."
```

---

## 17. Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-28 | Initial draft. Option C (Robust) 선택. SSH 직접 배포 + GH Secrets 통합. | cto-lead |
| **0.2** | **2026-04-28** | **D5~D8 결정 반영 — AWS 네이티브 스택 (CodeDeploy + Parameter Store + IAM OIDC + CloudFlare Flexible) 채택. M1 산출물 보존.** §2.0 v0.2 변경 단락 추가, §7 시퀀스 다이어그램 재작성, §11.3 Module Map을 M4-A/B/C 분할로 갱신, §12.6~§12.12 (IAM OIDC, GH Actions Role, EC2 Instance Profile, S3 deploy 버킷, CodeDeploy App/DG, SSM 시드, EC2 태깅) 신설, §13 GH Secrets 목록을 OIDC ARN 기반으로 단순화, §14 시크릿 전략을 Parameter Store 단일화로 재작성, §15 롤백을 CodeDeploy 네이티브로 전환, §16.3 nginx.conf를 CF Flexible 무한 리다이렉트 방지 + X-Forwarded-Proto 처리로 갱신, §16.6~§16.14 (appspec.yml, lifecycle 5종 .sh, .env.tmpl, deploy.yml v0.2, bootstrap-ec2.sh v0.2) 신설, §18 운영자 체크리스트 신설. | cto-lead (infra-architect + bkend-expert + qa-strategist) |
| **0.3** | **2026-04-28** | **D9 결정 반영 — backend/.env.example 1:1 정합. SSM 키 트리 7개→28개 확장. LiteLLM 단일 출구 확정. OPENAI_API_KEY 완전 제거. UPSTAGE_API_KEY는 langchain-upstage 직접 사용으로 보존.** 변수명 5건 정정 (DB_URL→DATABASE_URL, JWT_SECRET→JWT_SECRET_KEY, CORS_ALLOW_ORIGINS→CORS_ORIGINS list[str], LLM_OPENAI_KEY→제거, LLM_UPSTAGE_KEY→UPSTAGE_API_KEY). §2 안에 v0.3 변경 단락(A/B/C/D) 추가 — frontend grep 분석 결과 명기 (모든 fetch가 `/api/v1/...` → API_V1_PREFIX=/api/v1 + nginx proxy_pass slash 없음 유지). §12.11 SSM 시드 28키 카테고리별 CLI 전면 재작성 (--description, --tier Standard, SecureString은 --key-id alias/aws/ssm 명시). §16.3 nginx.conf에 slash 결정 사유 주석 추가 (실제 코드 변경 없음). §16.4 docker-compose.yml api 서비스 environment 잘못된 매핑 제거 → env_file 단일화. §16.9 after-install.sh에 명시적 SSM path → ENV var 매핑 jq 함수 추가, 검증 임계값 7→28, 핵심 6변수(DATABASE_URL/JWT_SECRET_KEY/LITELLM_URL/LITELLM_API_KEY/UPSTAGE_API_KEY/POSTGRES_PASSWORD) 사전 grep 검증 추가 (R13 완화). §16.12 .env.tmpl 전면 재작성 — backend/.env.example 카테고리 구조 보존, 28 SSM 매핑 + 비밀 아닌 default 평문. | cto-lead (infra-architect + bkend-expert) |
| **0.4** | **2026-04-28** | **D10 결정 반영 — `dev` 단일 브랜치(trunk) 채택, main 브랜치 부재.** v0.1~v0.3의 모든 'main' git 브랜치 참조를 'dev'로 정정 (15개 위치 수정: §Context Anchor WHY, §1.1 Goal-2, §1.2 Single SoT/Least Privilege, §7 sequence diagram git push 라인 + STS verify sub, §8.4 L3 회귀 시나리오, §8.5 L4 broken 이미지 푸시, §11.2 step 16, §12.7 Trust Policy JSON sub 조건 + R9 주의사항, §15.2 자동 롤백 절차 step 1, §16.13 deploy.yml `branches: [dev]`, §18 체크리스트 step 3 + step 24). 인프라 토폴로지·SSM 키·환경변수·M1~M3 산출물 변동 0건. ci.yml은 `branches-ignore: [dev]`로 정정 (CI는 feature/PR에서만 실행, dev push는 deploy.yml이 담당). | infra-architect |

---

## 18. 운영자 체크리스트 (M4-A 콘솔 가이드 압축본)

> 1-page 체크리스트. 각 단계 완료 시 검증 명령으로 확인.

### Phase 1 — IAM 사전 준비 (한 번만 실행)

| # | 단계 | 콘솔 경로 | 검증 명령 |
|---|------|-----------|-----------|
| 1 | OIDC Provider 등록 | IAM → Identity providers → Add provider → OpenID Connect | `aws iam list-open-id-connect-providers` (token.actions.githubusercontent.com 존재) |
| 2 | GH Actions Role 생성 (`farmos-gh-actions-deploy`) | IAM → Roles → Create role → Web identity → 위 OIDC 선택 | `aws iam get-role --role-name farmos-gh-actions-deploy` |
| 3 | Trust Policy sub 조건 설정 | Role → Trust relationships → Edit | `aws iam get-role --query 'Role.AssumeRolePolicyDocument'` (sub: `repo:OWNER/REPO:ref:refs/heads/dev`) |
| 4 | Permissions Policy 첨부 (S3 + CodeDeploy + KMS) | Role → Permissions → Add inline policy | `aws iam list-role-policies --role-name farmos-gh-actions-deploy` |
| 5 | EC2 Instance Profile Role 생성 (`farmos-ec2-instance`) | IAM → Roles → Create role → AWS service → EC2 | `aws iam get-instance-profile --instance-profile-name farmos-ec2-instance` |
| 6 | 관리형 정책 첨부 (`AmazonSSMManagedInstanceCore`, `AmazonEC2RoleforAWSCodeDeploy`) | Role → Permissions → Attach policies | `aws iam list-attached-role-policies --role-name farmos-ec2-instance` |
| 7 | Inline Policy 첨부 (`farmos-ec2-runtime` — SSM Get*, KMS Decrypt, S3 GetObject) | Role → Permissions → Add inline policy | `aws iam list-role-policies --role-name farmos-ec2-instance` |
| 8 | CodeDeploy Service Role 생성 (`farmos-codedeploy-svc`) | IAM → Roles → AWS service → CodeDeploy | `aws iam get-role --role-name farmos-codedeploy-svc` |

### Phase 2 — 인프라 리소스 (한 번만)

| # | 단계 | 콘솔 경로 | 검증 명령 |
|---|------|-----------|-----------|
| 9 | S3 deploy 버킷 생성 (`farmos-codedeploy-{ACCT}-ap-northeast-2`) | S3 → Create bucket | `aws s3api head-bucket --bucket ...` |
| 10 | 버전 관리 ON, 퍼블릭 차단 ON | S3 → Bucket → Properties / Permissions | `aws s3api get-bucket-versioning --bucket ...` |
| 11 | Lifecycle (30일 IA, 90일 삭제) | S3 → Bucket → Management → Lifecycle | `aws s3api get-bucket-lifecycle-configuration --bucket ...` |
| 12 | CodeDeploy Application 생성 (`farmos`, EC2/On-premises) | CodeDeploy → Applications → Create | `aws deploy get-application --application-name farmos` |
| 13 | DeploymentGroup 생성 (`farmos-prod`, In-place, 태그 App=farmos+Environment=prod, Auto-Rollback ON) | CodeDeploy → Applications → farmos → Create deployment group | `aws deploy get-deployment-group --application-name farmos --deployment-group-name farmos-prod` |
| 14 | Parameter Store 28개 키 시드 (§12.11 명령, D9) | Systems Manager → Parameter Store → Create parameter | `aws ssm get-parameters-by-path --path /farmos/prod --recursive --query "length(Parameters)"` → `28` |

### Phase 3 — EC2 인스턴스 (한 번만)

| # | 단계 | 콘솔 경로 | 검증 명령 |
|---|------|-----------|-----------|
| 15 | EC2 인스턴스 시작 (Ubuntu 24.04, t3.medium, gp3 50GB) | EC2 → Launch instance | `aws ec2 describe-instances --instance-ids i-...` |
| 16 | Elastic IP 할당 + attach | EC2 → Elastic IPs → Allocate → Associate | `aws ec2 describe-addresses` |
| 17 | IAM Instance Profile attach (`farmos-ec2-instance`) | EC2 → Instance → Actions → Security → Modify IAM role | `aws ec2 describe-iam-instance-profile-associations` |
| 18 | 태그 추가 (`App=farmos`, `Environment=prod`) — **CodeDeploy 발견 필수** | EC2 → Instance → Tags → Manage tags | `aws ec2 describe-tags --filters "Name=resource-id,Values=i-..."` |
| 19 | bootstrap-ec2.sh 실행 (Docker + CodeDeploy agent + IAM 검증) | SSH → `bash bootstrap-ec2.sh` | `sudo systemctl status codedeploy-agent` (active) |

### Phase 4 — GitHub & CloudFlare

| # | 단계 | 위치 | 검증 |
|---|------|------|------|
| 20 | GH Secrets 등록 (`AWS_ROLE_ARN`, `AWS_REGION`, `S3_DEPLOY_BUCKET`, `CODEDEPLOY_APP`, `CODEDEPLOY_GROUP`, `GHCR_OWNER`) | GitHub → Settings → Secrets and variables → Actions | 워크플로우 dry run |
| 21 | CloudFlare DNS A 레코드 (Proxied, EIP) | CF → DNS → Add record | `dig {도메인}` (CF IP 반환) |
| 22 | CloudFlare SSL/TLS → Flexible 선택 | CF → SSL/TLS → Overview | 대시보드 표시 확인 |
| 23 | CloudFlare "Always Use HTTPS" ON | CF → SSL/TLS → Edge Certificates | `curl -I http://{도메인}` → 301 to https |

### Phase 5 — 첫 배포

| # | 단계 | 검증 |
|---|------|------|
| 24 | dev push (또는 PR → dev 머지) | GH Actions deploy.yml 그린 |
| 25 | CodeDeploy 콘솔에서 Deployment Status = `Succeeded` | `aws deploy get-deployment --deployment-id d-...` |
| 26 | `curl https://{도메인}/health` → 200 "ok" | CF → CF → EC2 → nginx 응답 |
| 27 | `curl -I http://{EC2_EIP}/health` → 200 (무한 리다이렉트 부재) | 직접 접속 시 nginx가 리다이렉트 없이 응답 |
| 28 | L4 자동 롤백 시나리오 검증 (의도적 broken 이미지 푸시) | CodeDeploy 자동 롤백 ≤ 60초, /health 회복 |

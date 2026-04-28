---
template: plan
version: 1.3
description: AWS EC2 + GitHub Actions 자동 배포 파이프라인 구축 Plan (v0.3 - backend/.env.example 1:1 정합 + LiteLLM 단일 출구 + SSM 28키)
feature: farmos-ec2-deploy
date: 2026-04-28
last_updated: 2026-04-28
author: cto-lead (PDCA Team Mode)
project: FarmOS-Deploy-Test
version_meta: 0.4
---

# farmos-ec2-deploy Planning Document

> **Summary**: FarmOS 모놀리식 풀스택(React+FastAPI+Postgres+ChromaDB)을 단일 EC2(t3.medium)에 docker-compose로 배포하고, **GitHub Actions OIDC → AWS CodeDeploy + S3 + Parameter Store** 표준 파이프라인 + **CloudFlare Flexible SSL DNS**를 통해 dev push 시 자동 배포되는 PR-검증·prod-자동배포 파이프라인을 구축한다 (D10: dev = trunk, main 부재). v0.2에서 시크릿 관리는 GH Secrets에서 **AWS Systems Manager Parameter Store**로 이관하고, 배포 트리거는 SSH에서 **CodeDeploy 라이프사이클 훅 (ApplicationStop → BeforeInstall → AfterInstall → ApplicationStart → ValidateService)** 으로 전환한다.
>
> **Project**: FarmOS-Deploy-Test
> **Version**: 0.2.0
> **Author**: cto-lead (Team Mode: infra-architect + bkend-expert + qa-strategist)
> **Date**: 2026-04-28
> **Status**: Draft v0.2

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 현재 FarmOS는 배포 산출물(Dockerfile/compose/CI)이 전무하여 수동 배포에 의존하며, 프론트(Vercel 가정)와 백엔드 환경이 분리되어 있어 ML/RAG 파이프라인의 일관성·재현성·롤백 보장이 불가능하다. v0.1의 SSH 직접 배포 방식은 보안그룹 22 포트 노출/장기 SSH 키 보관/시크릿 GH Secrets 평문 보관 등 **AWS 모범사례에서 벗어난 임시방편**이었다. |
| **Solution** | 단일 EC2 호스트에 nginx(정적 + /api 리버스프록시) + FastAPI 컨테이너 + Postgres 컨테이너를 docker-compose로 통합 배포하고, **GHCR을 컨테이너 레지스트리로, S3를 CodeDeploy 번들 스토리지로, AWS CodeDeploy를 배포 오케스트레이터로, AWS Systems Manager Parameter Store를 시크릿 단일 출처로** 사용한다. GH Actions는 **OIDC로 AWS Role을 assume**하여 장기 액세스 키 없이 동작하며, CloudFlare DNS + Flexible SSL이 도메인 → CF → EC2:80 흐름으로 HTTPS를 제공한다. |
| **Function/UX Effect** | dev push → 5분 내 prod 자동 반영, /api 동일 오리진으로 CORS 제거, **CodeDeploy ValidateService 훅 실패 시 CodeDeploy가 자동으로 직전 리비전으로 롤백**, 시크릿은 Parameter Store에서 단일 출처로 관리(키 회전·CloudTrail 감사 무료), HTTPS는 CloudFlare가 종단 처리. |
| **Core Value** | 1인 운영 가능한 저비용(t3.medium 기준 월 약 $39) Single-EC2 배포 + AWS 네이티브 라이프사이클 자동 롤백 + OIDC 무자격증명 CI/CD를 통해 "배포 두려움 0" + "AWS 보안 모범사례 준수"를 달성. |

---

## Context Anchor

> 이 표는 Design / Do / Check / Report 문서로 자동 전파된다.

| Key | Value |
|-----|-------|
| **WHY** | 수동 배포·환경 분리·재현 불가 + GH Secrets 단일 평문 보관에서 벗어나, dev push = prod 라는 단일 진실 흐름과 AWS 네이티브 (OIDC + CodeDeploy + Parameter Store) 자동 롤백을 확보하기 위함. |
| **WHO** | FarmOS 운영자(개발자 1~2명), 베타 농가 사용자, GitHub Actions(OIDC) → AWS CodeDeploy 파이프라인. |
| **RISK** | (1) ML 의존성으로 백엔드 이미지가 2GB 초과, (2) 동일 호스트 Postgres 데이터 손실, (3) IAM OIDC trust policy 오작성 = AssumeRole 실패, (4) EC2 IAM 프로필 권한 누락 = Parameter Store 조회 실패 → 컨테이너 부팅 실패, (5) CloudFlare Flexible 모드에서 nginx 무한 리다이렉트, (6) appspec 훅 timeout 초과, (7) EC2 단일 인스턴스 SPOF. |
| **SUCCESS** | (1) dev push→prod 반영 ≤ 5분, (2) ValidateService 훅 실패 시 CodeDeploy 자동 롤백 ≤ 1분, (3) /health 200, (4) Postgres 컨테이너 재기동 후 데이터 보존, (5) 월 인프라 비용 ≤ $50 (실측 ~$39), (6) PR CI ≤ 8분, (7) GH Actions가 장기 키 없이 OIDC로 AWS Role assume, (8) Parameter Store에서 .env 자동 생성. |
| **SCOPE** | M1 백엔드 Docker (✅완료) → M2 docker-compose + nginx(CF Flexible) → M3 CI → **M4-A AWS 사전 준비 (IAM OIDC + S3 + CodeDeploy + Parameter Store)** → **M4-B appspec.yml + lifecycle 5종 .sh** → **M4-C deploy.yml (OIDC + GHCR + S3 + CodeDeploy)** → M5 EC2 부트스트랩(Ubuntu 24.04 + CodeDeploy agent) → M6 CloudFlare DNS + Flexible SSL. |

---

## 확정 결정사항 (사용자 사전 확정 — 변경 금지)

### 초기 결정 (v0.1)

| # | 결정 | 상세 | 함의 |
|---|------|------|------|
| **D1** | 프론트엔드 EC2 통합 | nginx가 `frontend/dist` 정적 서빙 + `/api/*` → FastAPI(8000) 리버스 프록시. `frontend/vercel.json` 제거. | 동일 오리진 → CORS 미들웨어 단순화, Vercel 비용 0. |
| **D2** | 백엔드 패키징 = Docker (GHCR) **+ S3 번들 + CodeDeploy 트리거** (v0.2 확장) | GitHub Actions에서 멀티스테이지 빌드 → ghcr.io 푸시 → **deploy 산출물(appspec.yml + scripts/) zip → S3 업로드 → CodeDeploy `create-deployment`** → EC2에서 CodeDeploy agent가 `docker pull` + `docker compose up`. | 이미지 불변성 + GHCR 단일 자격증명 + AWS 네이티브 배포 라이프사이클. |
| **D3** | DB = 동일 EC2 docker-compose Postgres | RDS 미사용. 호스트 볼륨 마운트(`./data/postgres`)로 영속화. | 비용 최소화. 단점: SPOF, 일일 백업 스크립트 필요. |
| **D4** | CI/CD 트리거 | PR/dev → CI(lint+test+build) only. **dev push/머지 → prod EC2 자동 배포**. staging 환경 없음. | 워크플로우 2개로 단순. **dev 보호 규칙 + Required reviews 1+ 필수**. |

### 신규 결정 (v0.2 — 2026-04-28 사용자 확정)

| # | 결정 | 상세 | 함의 |
|---|------|------|------|
| **D5** | EC2 1대(t3.medium) + docker-compose 3 컨테이너 | 논리적 subnet 분리는 도식 표현일 뿐, 실제로는 단일 호스트 docker-compose. | Design §16.4 docker-compose 그대로 유지. 컨테이너 간 isolation은 docker network로만 보장. |
| **D6** | 보유 도메인 + **CloudFlare DNS 호스팅 + Flexible SSL 모드 확정** | Let's Encrypt/Certbot **불필요**. nginx는 80포트 HTTP만 listen. CF가 TLS 종료. | nginx 무조건 HTTPS 리다이렉트 절대 금지(무한 루프). `X-Forwarded-Proto $http_x_forwarded_proto` 처리 필수. |
| **D7** | S3는 GH Actions 빌드 산출물 → CodeDeploy 가져오는 표준 패턴 | `farmos-codedeploy-{ACCOUNT_ID}-{REGION}` 버킷에 zip 번들 업로드 → CodeDeploy `--s3-location` 으로 참조. | 버전 관리 ON, 30일 후 STANDARD_IA, 90일 후 삭제 라이프사이클. |
| **D8** | **AWS CodeDeploy + AWS Systems Manager Parameter Store 필수 도입** | 배포 = CodeDeploy In-Place (5종 라이프사이클 훅). 시크릿 = Parameter Store SecureString (`/farmos/prod/*`). GH Actions 인증 = **IAM OIDC** (장기 키 없음). | M4 전면 재설계 (M4-A/B/C로 분할). IAM OIDC Provider + 2개 IAM Role(GH Actions assume용 + EC2 Instance Profile) 신설. |

### 신규 결정 (v0.3 — 2026-04-28 사용자 확정 — backend/.env.example 정합)

| # | 결정 | 상세 | 함의 |
|---|------|------|------|
| **D9-A** | **backend/.env.example을 prod 배포 .env의 단일 진실 소스로 채택** | `backend/app/core/config.py` (137 lines, pydantic_settings)에 정의된 변수명·타입을 .env.tmpl + docker-compose.yml + Plan/Design 모두에 1:1 적용. v0.2의 `DB_URL`/`JWT_SECRET`/`CORS_ALLOW_ORIGINS`/`LLM_OPENAI_KEY`/`LLM_UPSTAGE_KEY` 5건 변수명 불일치를 정정. | 컨테이너 부팅 시 ENV 값 빈 문자열로 인한 LLM 호출 401/DB 연결 실패 차단. config.py = Source of Truth. |
| **D9-B** | **LiteLLM Proxy(`https://litellm.lilpa.moe/v1`)를 모든 LLM 호출의 단일 출구로 확정** | diagnosis/subsidy/journal/review/ai_agent 모듈 전체가 `ChatOpenAI(base_url=settings.LITELLM_URL, api_key=settings.LITELLM_API_KEY, model=settings.LITELLM_MODEL)` 패턴으로만 LLM 호출. `OPENAI_API_KEY`는 SSM/.env.tmpl/compose에서 **완전 제거**. | 팀 LLM 사용량 통합 추적 + 단일 인증 경로. SPOF 위험은 R15에서 별도 관리. |
| **D9-C** | **SSM Parameter Store 키 트리를 7개 → 28개로 확장** | 카테고리: `db`(2) / `jwt`(1) / `cors`(1) / `litellm`(3) / `llm`(6) / `groq`(3) / `iot_relay`(3) / `external`(7) / `image`(1) / `ghcr`(1) = 총 28개. SSM Standard 무료 한도(10,000) 내라 비용 무관. | AfterInstall 훅의 jq 변환 로직이 모든 키를 수용하도록 §16.9 갱신. config.py가 요구하는 변수 누락 시 부팅 실패 → fail-fast로 사전 감지. |
| **D9-D** | **UPSTAGE_API_KEY는 LiteLLM 미경유 직접 사용으로 별도 SSM 키 보존** | langchain-upstage 가 LiteLLM 프록시를 거치지 않고 Upstage Document Parse / Solar Embedding을 직접 호출하는 유일한 LLM SDK. SSM 키는 `/farmos/prod/llm/upstage_key`로 보존하되 환경 변수명은 `UPSTAGE_API_KEY`로 정정 (v0.2 `LLM_UPSTAGE_KEY` 폐기). | subsidy 모듈의 PDF→Markdown 변환 + 한국어 asymmetric embedding 작동 보장. |

### 신규 결정 (v0.4 — 2026-04-28 사용자 확정 — 단일 브랜치 정책)

| # | 결정 | 상세 | 함의 |
|---|---|---|---|
| **D10** | **단일 브랜치 정책 — dev = trunk = prod 트리거** | main 브랜치는 존재하지 않음. feature/* → PR → dev (CI 실행), push to dev → deploy.yml (prod 자동 배포). | 워크플로우 단순. IAM Trust Policy sub 조건 = `repo:OWNER/REPO:ref:refs/heads/dev`. v0.1~v0.3의 모든 "main" 표기가 "dev"로 정정됨. |

---

## 1. Overview

### 1.1 Purpose

FarmOS 풀스택을 AWS EC2 단일 인스턴스에 컨테이너화하여 배포하고, **AWS 네이티브 CI/CD 파이프라인** (GH Actions OIDC + S3 + CodeDeploy + Parameter Store) 으로 운영 부담과 보안 위험을 동시에 최소화한다.

### 1.2 Background

- 직전 PDCA 사이클(`iot-manual-control` 98% 완료)로 핵심 기능은 안정화됨.
- 베타 운영을 위해 외부 접근 가능한 안정적 배포가 필요.
- 팀 규모(1~2명) 대비 K8s/EKS는 과잉 — 단일 EC2 + docker-compose가 적정 수준.
- ML 모델(sentence-transformers, ChromaDB) 콜드스타트 비용을 빌드 타임에 흡수해야 함.
- v0.2 배경: 사용자가 SSH 키 + GH Secrets 단일 평문 보관 방식의 한계를 지적, AWS 네이티브 (CodeDeploy + Parameter Store + OIDC) 표준 패턴으로 전환 결정.

### 1.3 Related Documents

- Design: `docs/02-design/features/farmos-ec2-deploy.design.md` v0.2 (본 사이클 산출물)
- 직전 사이클: `docs/01-plan/features/iot-manual-control.plan.md`
- DB 마이그레이션: `docs/01-plan/features/iot-postgres-migration.plan.md`
- 백엔드 진입점: `backend/main.py` → `app.main:app` (uvicorn, port 8000)
- 프론트 빌드 산출물: `frontend/dist/`
- M1 산출물(완료): `backend/Dockerfile`, `.dockerignore`

---

## 2. Scope

### 2.1 In Scope

- [x] Backend Dockerfile (M1 완료)
- [x] `.dockerignore` (M1 완료)
- [ ] Frontend 빌드 산출물 처리 (GH Actions에서 `npm run build` → S3 번들에 포함)
- [ ] `nginx/nginx.conf` (정적 서빙 + /api 프록시 + gzip + 캐시 헤더 + /health + **CloudFlare Flexible 대응**)
- [ ] `docker-compose.yml` (services: nginx, api, postgres + bind-mount + healthcheck + restart: unless-stopped)
- [ ] `.env.tmpl` (Parameter Store에서 자동 생성될 .env의 키 템플릿)
- [ ] `.github/workflows/ci.yml` (PR/push to non-dev (feature/*): ruff + pytest + npm run build + 타입체크)
- [ ] `.github/workflows/deploy.yml` (dev only (D10): **OIDC AssumeRole** → GHCR 빌드/푸시 → S3 zip 업로드 → CodeDeploy `create-deployment` → 폴링)
- [ ] `appspec.yml` (CodeDeploy 라이프사이클 훅 정의)
- [ ] `scripts/application-stop.sh` (ApplicationStop 훅)
- [ ] `scripts/before-install.sh` (BeforeInstall 훅 — 직전 IMAGE_TAG 백업)
- [ ] `scripts/after-install.sh` (AfterInstall 훅 — Parameter Store → .env 생성)
- [ ] `scripts/application-start.sh` (ApplicationStart 훅 — `docker compose pull && up -d`)
- [ ] `scripts/validate-service.sh` (ValidateService 훅 — `/health` 30회 폴링)
- [ ] `scripts/bootstrap-ec2.sh` (Ubuntu 24.04 docker + **CodeDeploy agent 설치** + ufw + swap)
- [ ] `frontend/vercel.json` 제거
- [ ] **AWS 사전 준비**: IAM OIDC Provider, IAM Role(GH Actions용) , IAM Instance Profile(EC2용), S3 deploy 버킷, CodeDeploy Application/DeploymentGroup, Parameter Store 7개 키 시드
- [ ] CloudFlare DNS 추가 + Proxied + Flexible SSL + Always Use HTTPS

### 2.2 Out of Scope

- AWS RDS, ElastiCache, CloudFront 도입 (향후 phase)
- Kubernetes / EKS / ECS
- Multi-AZ, ALB, Auto Scaling Group
- Staging 환경 (사용자 결정 D4)
- Blue/Green 배포 (CodeDeploy In-Place + ValidateService 자동 롤백으로 충분)
- Let's Encrypt / Certbot (D6 결정 — CloudFlare Flexible로 대체)
- APM/Sentry 통합 (별도 PDCA 사이클)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | dev 브랜치 push/머지 시 GHCR 이미지 빌드·푸시 + S3 번들 업로드 + CodeDeploy 트리거가 자동 실행되어야 한다. | High | Pending |
| FR-02 | PR(dev/feature 브랜치) 시 lint·test·build 검증이 자동 실행되며, 실패 시 머지 차단된다. | High | Pending |
| FR-03 | EC2에서 `nginx` 컨테이너가 `/`로 정적 React를 서빙하고 `/api/*`를 FastAPI(8000)로 프록시한다. | High | Pending |
| FR-04 | Postgres 컨테이너는 호스트 볼륨 `./data/postgres`로 영속화되며, 컨테이너 재기동 후 데이터가 보존된다. | High | Pending |
| FR-05 | 배포 직후 `curl http://{host}/health` 가 200을 반환해야 하며, 실패 시 **CodeDeploy ValidateService 훅이 비-0 종료 → 자동 롤백** 된다. | High | Pending |
| FR-06 | 모든 시크릿은 **AWS Parameter Store (SecureString)** 에 저장되고, GH Actions는 **OIDC로 IAM Role assume** 후 배포만 수행한다. EC2는 IAM Instance Profile로 Parameter Store를 조회한다. **v0.3: SSM 키 트리는 28개 (db/jwt/cors/litellm/llm/groq/iot_relay/external/image/ghcr 카테고리)** 로 확장되며, 코드/이미지/GH Secrets에 평문 노출 0건. | High | Pending |
| FR-07 | `scripts/bootstrap-ec2.sh` 1회 실행으로 신규 EC2(Ubuntu 24.04)가 배포 준비 완료 상태(Docker + CodeDeploy agent + IAM 프로필 검증)가 된다. | Medium | Pending |
| FR-08 | ChromaDB 인덱스 디렉터리는 별도 볼륨(`./data/chroma`)으로 분리되어 컨테이너 재배포 시 재인덱싱이 불필요하다. | Medium | Pending |
| FR-09 | GH Actions가 GHCR 푸시 시 이미지 태그를 `sha-{git-sha}` + `latest` 두 가지로 푸시한다. | Medium | Pending |
| FR-10 | `frontend/vercel.json`은 저장소에서 제거된다. | Low | Pending |
| **FR-11** | **GH Actions OIDC로 AWS IAM Role을 assume하며 장기 액세스 키 미사용**. trust policy의 sub 조건은 `repo:OWNER/REPO:ref:refs/heads/dev` 으로 제한. | High | Pending |
| **FR-12** | **CodeDeploy In-Place 배포로 dev push 시 ApplicationStop → BeforeInstall → Install → AfterInstall → ApplicationStart → ValidateService 라이프사이클이 자동 실행** 된다. | High | Pending |
| **FR-13** | **EC2 IAM Instance Profile이 `/farmos/prod/*` 경로의 Parameter Store 조회 + KMS Decrypt + S3 deploy 버킷 GetObject 권한을 보유** 한다. | High | Pending |
| **FR-14** | **CloudFlare Flexible SSL 모드를 통해 도메인 → CF → EC2:80 흐름으로 HTTPS가 제공** 된다. nginx는 80만 listen, X-Forwarded-Proto는 CF 헤더로 처리. | Medium | Pending |
| **FR-15** | **ValidateService 훅 헬스체크 실패 시 CodeDeploy가 직전 성공 리비전으로 자동 롤백** 한다 (DeploymentGroup 설정에서 `Rollback when deployment fails` ON). | High | Pending |
| **FR-16** | **LiteLLM Proxy를 모든 LLM 호출(diagnosis/subsidy/journal/review/ai_agent)의 단일 출구로 사용**. base_url은 `/farmos/prod/litellm/url` SSM에서 주입되고, 코드는 `ChatOpenAI(base_url=LITELLM_URL, api_key=LITELLM_API_KEY, model=LITELLM_MODEL)` 패턴으로만 호출. `OPENAI_API_KEY`는 환경에 존재하지 않는다. | High | Pending |
| **FR-17** | **모든 외부 SaaS API 키(KMA/NCPMS/Pesticide/FoodSafety/KAMIS/Kakao/Upstage)도 SSM SecureString으로 관리**. EC2 IAM Profile이 `/farmos/prod/external/*` 및 `/farmos/prod/llm/upstage_key` 경로를 조회. | High | Pending |
| **FR-18** | **IoT Relay Bridge가 N100 외부 호스트(`/farmos/prod/iot_relay/base_url`)에 IOT_RELAY_API_KEY로 인증 호출**. `AI_AGENT_BRIDGE_ENABLED="true"` SSM String 값으로 토글. 빈 키 시 안전 비활성화. | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| **배포 속도** | dev push → prod 반영 ≤ 5분 (이미지 빌드 캐시 hit 기준) | GH Actions Run + CodeDeploy 콘솔 |
| **롤백 속도** | ValidateService 실패 감지 → CodeDeploy 자동 롤백 완료 ≤ 1분 | CodeDeploy 콘솔 timeline |
| **다운타임** | 무중단 배포 시 API 응답 단절 ≤ 10초 (compose up -d 동안) | 배포 중 1초 간격 curl 모니터링 |
| **CI 속도** | PR CI 워크플로우 ≤ 8분 (캐시 hit 시 ≤ 4분) | GH Actions Run Time |
| **이미지 크기** | Backend Docker 이미지 ≤ 3GB (ML 의존성 포함, slim 베이스 + 멀티스테이지) | `docker images` |
| **비용** | 월 AWS 비용 ≤ $50 (실측 ~$39) | AWS Cost Explorer |
| **보안** | 시크릿 평문 노출 0건. 장기 AWS 액세스 키 0개. SSH 22 포트는 운영자 IP 한정 (CodeDeploy는 SSH 불요). | gitleaks + IAM Access Analyzer |
| **데이터 보존** | Postgres 컨테이너 재기동 후 데이터 100% 보존 | 재기동 전후 `SELECT count(*)` 비교 |
| **헬스체크** | `/health` 응답 ≤ 500ms, 5xx 0% | CodeDeploy ValidateService + 1분 간격 curl |

#### NFR-비용 분해표 (v0.2 재계산)

| 항목 | 월 비용 (USD) | 비고 |
|------|--------------:|------|
| EC2 t3.medium (730h) | ~$30.00 | ap-northeast-2 (Seoul) 온디맨드 |
| EBS gp3 50GB | ~$4.00 | IOPS 3000 기본 포함 |
| Elastic IP (인스턴스 attach 됨) | $0.00 | unused 시 $3.6/월 |
| 데이터 전송 (out 100GB) | ~$5.00 | CloudFlare Proxied로 대부분 캐시됨 → 실측 더 낮음 |
| S3 (deploy 번들 ~50MB × 30회) | ~$0.05 | 표준 + IA 라이프사이클 |
| Parameter Store (Standard, 7 keys) | $0.00 | 10,000 req/월 무료 |
| CodeDeploy (EC2 deploy) | $0.00 | EC2 배포는 무료 |
| KMS (`aws/ssm` 기본 키) | $0.00 | AWS 관리 키 무료 |
| CloudFlare DNS + Flexible SSL | $0.00 | Free 플랜 |
| **합계** | **~$39.05** | 목표 $50 이하 ✅ |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] **SC-1**: `git push origin dev` → 5분 내에 `https://{도메인}/` 에서 새 빌드 확인 (GH Actions 그린 + CodeDeploy Succeeded)
- [ ] **SC-2**: `curl https://{도메인}/health` → `"ok"` 200 응답 (CloudFlare 경유)
- [ ] **SC-3**: `docker compose down && docker compose up -d` 후 Postgres 데이터 보존 (rowcount 동일)
- [ ] **SC-4**: 의도적으로 헬스체크 실패하는 이미지 배포 → **CodeDeploy ValidateService 훅 실패 → 자동 롤백 ≤ 1분 → 직전 리비전으로 복귀**
- [ ] **SC-5**: PR 생성 → CI(lint+test+build) ≤ 8분 내 그린, 실패 시 머지 버튼 비활성화
- [ ] **SC-6**: AWS Cost Explorer 1주일 수치 → 월 환산 ≤ $50 (목표 ~$39)
- [ ] **SC-7**: `gitleaks detect` → 0 finding, **IAM Access Analyzer**가 와일드카드 trust policy 0건 보고
- [ ] **SC-8**: GH Actions가 OIDC로 AWS Role을 assume → S3에 번들 업로드 → CodeDeploy 트리거 → ValidateService 그린. (장기 액세스 키 0개)
- [ ] **SC-9**: EC2 부팅 시 IAM Instance Profile로 Parameter Store에서 `/farmos/prod/*` 7개 키 조회 → `.env` 생성 → 컨테이너 부팅 정상.

### 4.2 Quality Criteria

- [ ] Backend Docker 이미지 ≤ 3GB
- [ ] PR CI 캐시 hit 시 ≤ 4분
- [ ] `nginx -t` 통과 + CloudFlare Flexible 모드 무한 리다이렉트 검증 통과
- [ ] `docker compose config` 에러 0건
- [ ] 비루트 사용자(uid 1000)로 FastAPI 실행 확인 (`docker exec api id`)
- [ ] `aws codedeploy get-deployment` 응답이 `Succeeded`
- [ ] `aws ssm get-parameters-by-path --path /farmos/prod --recursive --with-decryption` 응답에 7개 키 모두 존재

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **R1: ML 의존성 도커 이미지 거대화** | High | High | (a) 멀티스테이지 빌드, (b) `python:3.12-slim` 베이스, (c) `.dockerignore`, (d) GH Actions buildx 캐시 (cache-from/to: type=gha,mode=max) |
| **R2: Postgres 동일 호스트 데이터 손실** | High | Medium | (a) bind-mount `./data/postgres`, (b) `cron`으로 일 1회 `pg_dump` → S3 (별도 Phase), (c) `down -v` 사용 금지 운영 룰 |
| **R3: 시크릿 노출** | Critical | Low (v0.2에서 감소) | **v0.2: 모든 시크릿이 Parameter Store SecureString**. GH Secrets는 OIDC role ARN 등 비-기밀만 보관. gitleaks PR 훅. |
| **R4: EC2 단일 인스턴스 SPOF** | High | Low | (a) 본 사이클은 SPOF 수용, (b) Elastic IP, (c) AMI 스냅샷 주 1회 |
| **R5: 첫 배포 콜드스타트 5분+** | Medium | High | (a) 빌드 타임에 sentence-transformers 모델 사전 다운로드 (M1 Dockerfile에 이미 적용), (b) ChromaDB 호스트 볼륨 영속화 |
| **R6: Docker Hub Rate Limit** | Medium | Low | GHCR 사용으로 회피 |
| **R7: Disk Full** | Medium | Medium | (a) `docker system prune` 주 1회 cron, (b) json-file driver 10m×3, (c) EBS 50GB |
| **R8: 배포 중 in-flight 요청 단절** | Medium | Medium | (a) docker-compose healthcheck 통과 후 nginx upstream 활성, (b) 본 사이클은 10초 단절 허용 |
| **R9: IAM OIDC trust policy의 sub 조건 오작성 → AssumeRole 실패** | High | Medium | (a) 최초 워크플로우는 `repo:OWNER/REPO:*` 로 시작 후 dev 한정으로 좁히기, (b) `aws sts assume-role-with-web-identity` 로 로컬 검증, (c) IAM Access Analyzer로 와일드카드 검사 |
| **R10: EC2 IAM 프로필의 `ssm:GetParametersByPath` 권한 누락 → 컨테이너 부팅 실패** | High | Medium | (a) bootstrap 시 `aws ssm get-parameters-by-path /farmos/prod --recursive --with-decryption` 검증 단계 추가, (b) AfterInstall 훅 첫 줄에서 권한 검증 후 즉시 fail-fast, (c) IAM 정책에 `kms:Decrypt` 누락 빈번 — 명시적으로 KMS 키 ARN 포함 |
| **R11: appspec lifecycle 훅 timeout 초과** | Medium | Medium | (a) validate-service.sh 헬스체크는 30회×2초=60초 안에 완료, (b) appspec timeout은 90초로 여유, (c) start-period (Dockerfile HEALTHCHECK)는 60초로 모델 로딩 흡수 |
| **R12: CloudFlare Flexible 모드에서 nginx 무한 리다이렉트** | High | Medium | (a) nginx 설정에 `return 301 https://` 코드 절대 금지 (운영 룰 명문화), (b) X-Forwarded-Proto 기반 분기만 허용, (c) CF 대시보드 "Always Use HTTPS"는 ON 유지 (CF 단에서 처리), (d) 배포 후 `curl -I http://{도메인}` 으로 무한 리다이렉트 부재 확인 |
| **R13: v0.2 변수명 불일치로 컨테이너 부팅 시 ENV 빈 문자열 → LLM/DB 401·연결 실패** | High | Medium (v0.3에서 차단) | (a) v0.3에서 backend/.env.example을 단일 진실 소스로 채택 (D9-A), (b) M2 검증 단계에서 `docker compose config | grep -E '(LITELLM_URL|JWT_SECRET_KEY|DATABASE_URL|UPSTAGE_API_KEY)'` 사전 확인, (c) AfterInstall 훅이 jq 변환 후 `wc -l` ≥ 28 확인, (d) FastAPI 부팅 시 빈 LITELLM_URL 감지 → fail-fast |
| **R14: 외부 SaaS API 키 회전 누락 (KMA/NCPMS/Pesticide/FoodSafety/KAMIS/Kakao/Upstage)** | Medium | Medium | (a) SSM 파라미터에 `--description "rotate by 2026-Qx"` 명시, (b) 분기별 운영 체크리스트 (M6 운영자 가이드), (c) CloudTrail이 GetParameter 자동 기록 — 미사용 키 식별, (d) `aws ssm put-parameter --overwrite` 후 CodeDeploy 재배포로 즉시 반영 |
| **R15: LiteLLM Proxy(N100 호스트) 다운 시 모든 LLM 기능 마비 (SPOF)** | High | Low-Medium | (a) v0.3에서 LITELLM_URL을 SSM String으로 외부화하여 fallback URL 즉시 교체 가능, (b) 향후 PDCA 사이클에서 `LLM_PROVIDER` switch + OpenAI direct fallback 별도 검토, (c) LiteLLM 측 자체 모니터링 (본 사이클 외 범위), (d) 본 사이클은 SPOF 수용 — 비용·운영 단순성 우선. |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `frontend/vercel.json` | Config | **삭제** (D1 결정) |
| `backend/main.py` 진입점 | Runtime | uvicorn 호스트/포트는 동일하지만 컨테이너 내부에서 실행 |
| Frontend API base URL | Config | `VITE_API_BASE_URL` 환경변수 제거 또는 빈 문자열 → 동일 오리진 `/api` |
| Backend CORS 설정 | Code | 동일 오리진이므로 `allow_origins` 단순화 가능 (선택) |
| `backend/Dockerfile` | Image | **M1 완료** (변경 없음) |
| `.dockerignore` | Image | **M1 완료** (변경 없음) |
| 신규 파일 (M2~M6) | Infra | `docker-compose.yml`, `nginx/nginx.conf`, `.env.tmpl`, `appspec.yml`, `scripts/{application-stop,before-install,after-install,application-start,validate-service,bootstrap-ec2}.sh`, `.github/workflows/{ci,deploy}.yml` |
| **v0.3: .env.tmpl + docker-compose.yml 변수명 정정** | Config | `DB_URL`→`DATABASE_URL`, `JWT_SECRET`→`JWT_SECRET_KEY`, `CORS_ALLOW_ORIGINS`→`CORS_ORIGINS`(list[str]), `LLM_OPENAI_KEY`→**삭제**, `LLM_UPSTAGE_KEY`→`UPSTAGE_API_KEY` (총 5건). 신규 추가: `LITELLM_URL`/`LITELLM_API_KEY`/`LITELLM_MODEL`/`GROQ_*`/`IOT_RELAY_*`/`KMA_DECODING_KEY`/`NCPMS_API_KEY`/`PESTICIDE_API_KEY`/`FOOD_SAFETY_API_KEY`/`KAMIS_API_KEY`/`KAMIS_CERT_ID`/`KAKAO_REST_API_KEY`/`LLM_PROVIDER`/`LLM_MODEL`/`LLM_REASONING_EFFORT`/`EMBED_MODEL`/`AI_AGENT_MODEL`. |
| AWS 리소스 신설 | Cloud | IAM OIDC Provider, IAM Role (GH Actions용), IAM Instance Profile (EC2용), S3 deploy 버킷, CodeDeploy Application/DeploymentGroup, **Parameter Store 28개 키 (v0.3 — 7→28 확장)** |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `frontend/vercel.json` | Vercel SPA fallback | Vercel 빌드 시스템 | None (Vercel 미사용 전환) |
| Frontend `fetch('/api/...')` | Runtime API call | `frontend/src/**/*.ts` | Needs verification — 상대경로 사용 확인 필요 |
| `backend/app/main.py` CORS | Server middleware | `app.main` | Needs verification — 동일 오리진이므로 origins 좁힘 가능 |
| Postgres 연결 문자열 | DB connection | `app/db.py` 또는 settings | Needs verification — `DATABASE_URL=postgresql+asyncpg://farmos:***@postgres:5432/farmos` |
| ChromaDB persist directory | Vector store | `app/services/**/chroma*.py` | Needs verification — `/app/chroma_data` 절대경로 사용 확인 |

### 6.3 Verification

- [ ] Frontend의 모든 fetch 호출이 상대경로(`/api/...`)임을 grep 확인
- [ ] Backend `DATABASE_URL`을 환경변수에서 읽는지 확인 (`pydantic_settings` 사용 시 OK)
- [ ] ChromaDB persist path가 환경변수 또는 절대경로 `/app/chroma_data`인지 확인
- [ ] 환경변수 누락 시 부팅 실패하도록 `pydantic_settings` 필수 지정 확인
- [ ] `aws iam list-open-id-connect-providers` 응답에 `token.actions.githubusercontent.com` 존재
- [ ] `aws codedeploy get-application --application-name farmos` 응답 정상

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | 단순 정적 사이트 | 포트폴리오, 랜딩 | ☐ |
| **Dynamic** | feature 모듈 + BaaS/Custom 백엔드 | SaaS MVP, 풀스택 1~2인 팀 | ☑ |
| **Enterprise** | 마이크로서비스, K8s | 고트래픽, 다인 팀 | ☐ |

→ **Dynamic 확정** (1~2인 팀, 단일 EC2, 모놀리식 백엔드, AWS 네이티브 CI/CD)

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| 배포 토폴로지 | EC2 단일 / EC2 + RDS / EKS | **EC2 단일 (D3, D5)** | 1~2인 팀, 비용 ≤ $50/월 |
| 컨테이너 레지스트리 | Docker Hub / ECR / GHCR | **GHCR (D2)** | GH 토큰 단일 자격증명 |
| 프론트 호스팅 | Vercel / S3+CF / EC2 nginx | **EC2 nginx (D1)** | 동일 오리진 → CORS 제거 |
| CI/CD 트리거 | dev=staging,main=prod / main only / **dev only (D10)** | **dev only (D4 + D10)** | staging 환경 미운영, main 브랜치 부재 |
| 시크릿 관리 | GH Secrets / **Parameter Store** / Vault | **Parameter Store (D8)** | AWS 네이티브, KMS 무료, CloudTrail 감사 |
| 배포 오케스트레이터 | SSH 직접 / **CodeDeploy** / Ansible | **CodeDeploy (D8)** | 라이프사이클 훅, 자동 롤백, EC2 무료 |
| GH Actions → AWS 인증 | 장기 키 / **OIDC** | **OIDC (D8)** | 보안 모범사례, 장기 키 0개 |
| DB | RDS / 컨테이너 / SQLite | **컨테이너 Postgres (D3)** | 비용 최소 |
| OS | Amazon Linux 2023 / Ubuntu 22.04 / **Ubuntu 24.04** | **Ubuntu 24.04 LTS** | 최신 LTS, CodeDeploy agent 호환 확인 |
| HTTPS | Let's Encrypt / **CloudFlare Flexible** | **CloudFlare Flexible (D6)** | 인증서 갱신 cron 불필요, 무료 |

### 7.3 Clean Architecture Approach

```
Selected Level: Dynamic (Single-EC2 monolith with AWS-native deploy)

Repository Structure (deploy artifact 기준, v0.2):
┌────────────────────────────────────────────────────┐
│ /                                                  │
│ ├── backend/             (existing)                │
│ │   ├── Dockerfile       (M1 ✅완료)                │
│ │   └── ...                                        │
│ ├── frontend/            (existing)                │
│ │   └── (vercel.json 삭제)                          │
│ ├── nginx/                                         │
│ │   └── nginx.conf       (M2, CF Flexible 대응)    │
│ ├── scripts/                                       │
│ │   ├── application-stop.sh      (M4-B)            │
│ │   ├── before-install.sh        (M4-B)            │
│ │   ├── after-install.sh         (M4-B, SSM→.env)  │
│ │   ├── application-start.sh     (M4-B)            │
│ │   ├── validate-service.sh      (M4-B)            │
│ │   └── bootstrap-ec2.sh         (M5)              │
│ ├── .github/workflows/                             │
│ │   ├── ci.yml           (M3)                      │
│ │   └── deploy.yml       (M4-C, OIDC+S3+CD)        │
│ ├── appspec.yml          (M4-B)                    │
│ ├── docker-compose.yml   (M2)                     │
│ ├── .env.tmpl            (M2)                     │
│ └── .dockerignore        (M1 ✅완료)                │
└────────────────────────────────────────────────────┘
```

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [x] `CLAUDE.md` 존재 — Dynamic Level 명시 가정
- [x] Backend: `ruff` 컨벤션
- [x] Frontend: ESLint + Prettier 설정
- [x] TypeScript: `tsconfig.json`
- [x] `.dockerignore` (M1 완료)
- [ ] `gitleaks` 설정 — 신규 생성 권장

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| 이미지 태그 규칙 | missing | `ghcr.io/{org}/farmos-api:sha-{git-sha}` + `:latest` | High |
| 컨테이너 이름 | missing | `farmos-nginx`, `farmos-api`, `farmos-postgres` | High |
| 볼륨 경로 | missing | 호스트 `/opt/farmos/data/{postgres,chroma}` (CodeDeploy `/opt/farmos` 표준 경로) | High |
| Parameter Store 경로 | missing | `/farmos/prod/{db,jwt,llm,image,ghcr}/*` | High |
| 환경변수 변환 규칙 | missing | `/farmos/prod/db/password` → `DB_PASSWORD` (path → UPPER_SNAKE_CASE) | High |
| 로그 정책 | missing | docker `json-file` driver, 10m×3 | Medium |
| Healthcheck 경로 | missing | nginx `/health` 직접 응답 + FastAPI `/api/health` (DB ping) | High |
| AWS 리전 | missing | `ap-northeast-2` (Seoul) | High |
| EC2 태그 (CodeDeploy 발견용) | missing | `App=farmos`, `Environment=prod` | High |

### 8.3 Environment Variables (Parameter Store 매핑 — v0.3 28키 확장)

> **v0.3 변경**: backend/.env.example (121 lines) + backend/app/core/config.py (137 lines)와 1:1 정합. 카테고리: db(2) / jwt(1) / cors(1) / litellm(3) / llm(6) / groq(3) / iot_relay(3) / external(7) / image(1) / ghcr(1) = **28개**.

| Parameter Store Key | .env 변수명 (config.py) | Type | 비밀? | Purpose |
|---------------------|------------------------|------|:----:|---------|
| `/farmos/prod/db/url` | `DATABASE_URL` | SecureString | ✅ | `postgresql+asyncpg://farmos:***@postgres:5432/farmos` (v0.2 `DB_URL` 정정) |
| `/farmos/prod/db/password` | `POSTGRES_PASSWORD` | SecureString | ✅ | docker-compose Postgres password |
| `/farmos/prod/jwt/secret_key` | `JWT_SECRET_KEY` | SecureString | ✅ | FastAPI JWT 서명 (v0.2 `JWT_SECRET` 정정) |
| `/farmos/prod/cors/origins` | `CORS_ORIGINS` | String | ❌ | `list[str]` JSON 배열 (v0.2 `CORS_ALLOW_ORIGINS` 정정 + 타입 명시) |
| `/farmos/prod/litellm/url` | `LITELLM_URL` | String | ❌ | `https://litellm.lilpa.moe/v1` (D9-B 단일 출구) |
| `/farmos/prod/litellm/api_key` | `LITELLM_API_KEY` | SecureString | ✅ | LiteLLM 인증 |
| `/farmos/prod/litellm/model` | `LITELLM_MODEL` | String | ❌ | `gpt-oss-20b` 등 |
| `/farmos/prod/llm/upstage_key` | `UPSTAGE_API_KEY` | SecureString | ✅ | langchain-upstage 직접 호출 (D9-D 보존, v0.2 `LLM_UPSTAGE_KEY` 정정) |
| `/farmos/prod/llm/reasoning_effort` | `LLM_REASONING_EFFORT` | String | ❌ | minimal/low/medium/high/none |
| `/farmos/prod/llm/provider` | `LLM_PROVIDER` | String | ❌ | `litellm` |
| `/farmos/prod/llm/model` | `LLM_MODEL` | String | ❌ | 리뷰 분석용 모델 |
| `/farmos/prod/llm/embed_model` | `EMBED_MODEL` | String | ❌ | `voyage-3.5` |
| `/farmos/prod/llm/ai_agent_model` | `AI_AGENT_MODEL` | String | ❌ | `openai/gpt-5-mini` |
| `/farmos/prod/groq/api_key` | `GROQ_API_KEY` | SecureString | ✅ | Whisper STT (영농일지) |
| `/farmos/prod/groq/stt_url` | `GROQ_STT_URL` | String | ❌ | `https://api.groq.com/openai/v1/audio/transcriptions` |
| `/farmos/prod/groq/stt_model` | `GROQ_STT_MODEL` | String | ❌ | `whisper-large-v3` |
| `/farmos/prod/iot_relay/base_url` | `IOT_RELAY_BASE_URL` | String | ❌ | N100 외부 호스트 URL |
| `/farmos/prod/iot_relay/api_key` | `IOT_RELAY_API_KEY` | SecureString | ✅ | Relay 공유 시크릿 |
| `/farmos/prod/iot_relay/bridge_enabled` | `AI_AGENT_BRIDGE_ENABLED` | String | ❌ | "true"/"false" 문자열 |
| `/farmos/prod/external/kma_decoding_key` | `KMA_DECODING_KEY` | SecureString | ✅ | 기상청 단기예보 |
| `/farmos/prod/external/ncpms_key` | `NCPMS_API_KEY` | SecureString | ✅ | 농작물병해충관리시스템 |
| `/farmos/prod/external/pesticide_key` | `PESTICIDE_API_KEY` | SecureString | ✅ | 농약안전정보시스템 |
| `/farmos/prod/external/food_safety_key` | `FOOD_SAFETY_API_KEY` | SecureString | ✅ | 식품안전나라 |
| `/farmos/prod/external/kamis_key` | `KAMIS_API_KEY` | SecureString | ✅ | 농산물유통정보 |
| `/farmos/prod/external/kamis_cert_id` | `KAMIS_CERT_ID` | SecureString | ✅ | KAMIS 인증 ID |
| `/farmos/prod/external/kakao_rest_key` | `KAKAO_REST_API_KEY` | SecureString | ✅ | Kakao 좌표 변환 |
| `/farmos/prod/image/tag` | `IMAGE_TAG` | String | ❌ | GH Actions가 PutParameter로 갱신 |
| `/farmos/prod/ghcr/owner` | `GHCR_OWNER` | String | ❌ | GHCR organization/user |

> **제거 (v0.3)**: `/farmos/prod/llm/openai_key` — D9-B에 따라 LiteLLM 단일 출구로 OPENAI_API_KEY 폐기.
> **추가 비밀값 미포함**: `PROJECT_NAME`/`API_V1_PREFIX`/`APP_TIMEZONE`/`DB_POOL_*`/`CHROMA_DB_PATH`/`EMBED_DIM`/`OLLAMA_*`/`REVIEW_ANALYSIS_*`/`AI_AGENT_LLM_INTERVAL`/`AI_AGENT_RULE_INTERVAL`/`AI_AGENT_MIRROR_TTL_DAYS`/`AI_AGENT_BACKFILL_PAGE_SIZE`/`SOIL_MOISTURE_*`/`UPLOAD_BASE_DIR`/`FARM_NX`/`FARM_NY`/`FONT_*`/`SUBSIDY_*` 는 모두 비밀이 아니거나 코드 default가 충분하므로 **.env.tmpl에 평문 또는 default로 박힌다** (SSM 저장 안 함).

GH Secrets (잔존, 비-기밀만):
| Secret | Value | Purpose |
|--------|-------|---------|
| `AWS_ROLE_ARN` | `arn:aws:iam::{ACCOUNT}:role/farmos-gh-actions-deploy` | OIDC AssumeRole 대상 |
| `AWS_REGION` | `ap-northeast-2` | AWS 리전 |
| `S3_DEPLOY_BUCKET` | `farmos-codedeploy-{ACCOUNT}-ap-northeast-2` | CodeDeploy 번들 버킷 |
| `CODEDEPLOY_APP` | `farmos` | CodeDeploy Application 이름 |
| `CODEDEPLOY_GROUP` | `farmos-prod` | DeploymentGroup 이름 |
| `GHCR_OWNER` | GitHub username/org | GHCR push 대상 |

### 8.4 Pipeline Integration

본 사이클은 9-phase 코드 파이프라인이 아닌 **인프라 PDCA**로 분류된다.

---

## 9. Next Steps

1. [ ] Design v0.2 사용자 승인
2. [x] `/pdca do farmos-ec2-deploy --scope module-1` (Backend Dockerfile + .dockerignore — **완료**)
3. [ ] `/pdca do farmos-ec2-deploy --scope module-2` (docker-compose + nginx CF Flexible + .env.tmpl)
4. [ ] `/pdca do farmos-ec2-deploy --scope module-3` (CI workflow)
5. [ ] `/pdca do farmos-ec2-deploy --scope module-4a` (AWS 사전 준비 — IAM OIDC + S3 + CodeDeploy + Parameter Store)
6. [ ] `/pdca do farmos-ec2-deploy --scope module-4b` (appspec.yml + lifecycle 5종 .sh)
7. [ ] `/pdca do farmos-ec2-deploy --scope module-4c` (deploy.yml — OIDC + GHCR + S3 + CodeDeploy)
8. [ ] `/pdca do farmos-ec2-deploy --scope module-5` (EC2 부트스트랩 — Ubuntu 24.04 + CodeDeploy agent)
9. [ ] `/pdca do farmos-ec2-deploy --scope module-6` (CloudFlare DNS + Flexible SSL + 백업 cron)
10. [ ] `/pdca analyze farmos-ec2-deploy` (헬스체크 + CodeDeploy 자동 롤백 + Parameter Store 조회 검증)
11. [ ] `/pdca report farmos-ec2-deploy`

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-28 | Initial draft (PDCA Team Mode: cto-lead 오케스트레이션) | cto-lead |
| 0.2 | 2026-04-28 | **D5~D8 결정 반영** — AWS 네이티브 스택 채택 (CodeDeploy + Parameter Store + IAM OIDC + CloudFlare Flexible). FR-11~15 신설, R9~R12 신설, SC-8/SC-9 신설, NFR 비용 분해표 ~$39 갱신. M1 산출물(Dockerfile, .dockerignore) 보존. M4를 M4-A/B/C로 분할. | cto-lead (infra-architect + bkend-expert + qa-strategist) |
| **0.4** | **2026-04-28** | **D10 결정 — `dev` 단일 브랜치(trunk) 채택**. main 부재 사실 반영. 모든 'main 머지'/'refs/heads/main' 표기를 'dev push'/'refs/heads/dev' 로 정정. ci.yml/deploy.yml/§12.7 IAM Trust Policy 영향. 인프라 토폴로지·SSM 키·환경변수 변동 0건. | cto-lead (infra-architect) |
| **0.3** | **2026-04-28** | **D9 결정 반영 — backend/.env.example을 prod 배포 .env의 단일 진실 소스로 채택**. SSM 키 트리 7→28 확장. LiteLLM Proxy 단일 출구 확정 — `OPENAI_API_KEY` 완전 제거. `UPSTAGE_API_KEY`는 langchain-upstage 직접 사용으로 보존. 변수명 5건 정정 (DB_URL→DATABASE_URL, JWT_SECRET→JWT_SECRET_KEY, CORS_ALLOW_ORIGINS→CORS_ORIGINS list[str], LLM_OPENAI_KEY→삭제, LLM_UPSTAGE_KEY→UPSTAGE_API_KEY). FR-16/17/18 신설, R13/R14/R15 신설, §6 Impact·§8.3 SSM 매핑표 전면 재작성 (28키). NFR 비용 영향 없음 (SSM Standard 무료 한도 내). frontend grep 결과: 모든 fetch가 `/api/v1/...` 호출 → `API_V1_PREFIX=/api/v1` + nginx `proxy_pass http://farmos_api;` (slash 없음 유지) 정합 확인. | cto-lead (infra-architect + bkend-expert) |

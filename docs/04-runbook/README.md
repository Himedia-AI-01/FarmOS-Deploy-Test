# FarmOS 배포 운영자 가이드 — 인덱스

> **Design Ref**: `docs/02-design/features/farmos-ec2-deploy.design.md` v0.4
> **Plan Ref**: `docs/01-plan/farmos-ec2-deploy.plan.md`
> **Trunk Branch**: `dev` (D10 결정)
> **Target Region**: `ap-northeast-2` (서울)
> **Target Account**: `242201280878`

본 디렉터리는 `dev` 브랜치 push 1회 → CodeDeploy 자동 배포 → CloudFlare 프록시 통과 → `https://{도메인}/health` 200 응답까지의 **end-to-end 운영 절차**를 모듈별로 분할한 5개 런북입니다. 각 런북은 m4a-aws-setup.md v2와 동일한 5블록 패턴(진입경로 / 화면 ASCII / 입력값 표 / 검증 / 함정)으로 작성되었습니다.

---

## §1. 5개 런북 인덱스

| 모듈 | 파일 | 분량 | 사용자 조작 영역 | 산출물 |
|---|---|---|---|---|
| **M4-A** | [`m4a-aws-setup.md`](./m4a-aws-setup.md) | 1749 라인 | AWS Console (IAM/S3/CodeDeploy/SSM) | OIDC + Role 4종 + S3 버킷 + CodeDeploy App/DG + SSM 28키 |
| **M4-B** | [`m4b-codedeploy-lifecycle.md`](./m4b-codedeploy-lifecycle.md) | 코드 검증 위주 | 로컬 (bash 검증, 권한 비트) | `appspec.yml` + 5개 lifecycle .sh 검증 통과 |
| **M4-C** | [`m4c-gh-secrets-deploy.md`](./m4c-gh-secrets-deploy.md) | — | GitHub (Secrets 등록 + Actions 모니터링) | GH Secrets 6개 + 첫 dev push 배포 성공 + 자동 롤백 검증 |
| **M5** | [`m5-ec2-bootstrap.md`](./m5-ec2-bootstrap.md) | — | AWS Console (EC2) + SSH | EC2 1대 + Elastic IP + Instance Profile + bootstrap 완료 |
| **M6** | [`m6-cloudflare-dns.md`](./m6-cloudflare-dns.md) | — | CloudFlare 대시보드 + 도메인 등록기관 | DNS A 레코드(Proxied) + Flexible SSL + Always Use HTTPS |

---

## §2. 모듈 의존 관계 그래프

```
            [git push origin dev]                 ← 사용자 트리거
                       │
                       ▼
                 GH Actions
                       │
   ┌───────────────────┼───────────────────┐
   │                   │                   │
   ▼                   ▼                   ▼
 build-and-push    build-frontend       deploy
 (GHCR)            (vercel.json X)      (OIDC → S3 → CodeDeploy)
                                            │
                                            ▼
                                  ┌─────────────────────┐
                                  │  EC2: farmos-prod-1 │
                                  │  /opt/farmos/       │
                                  │  appspec.yml + .sh  │
                                  └─────────────────────┘

 ┌─ 의존성 흐름 (선행 → 후행) ────────────────────────────────────┐
 │                                                                  │
 │  M4-A (AWS 사전 준비)                                            │
 │     │                                                            │
 │     ├─→ M4-B (Lifecycle scripts) ───┐                            │
 │     │     ※ M4-A 의 ARN 캐치 필요     │                          │
 │     │       (캐치 #4 svc-role)        │                          │
 │     │                                  ▼                         │
 │     ├─→ M4-C (GH Secrets + 첫 배포) ─→ 배포 1회 성공 가능        │
 │     │     ※ M4-A #2/#5/#6/#7,         ▲                          │
 │     │       M4-B 파일 존재 필요        │                          │
 │     │                                  │                         │
 │     └─→ M5 (EC2 부트스트랩) ───────────┘                         │
 │            ※ M4-A #3 InstanceProfile,                            │
 │              SSM 28키 시드 필요                                  │
 │                                                                  │
 │  M5 완료 ─→ M6 (CloudFlare DNS) — 후속, 배포 자체와는 독립        │
 │                                                                  │
 └──────────────────────────────────────────────────────────────────┘
```

> **읽는 법**: M4-A 가 모든 모듈의 사전 조건. M4-B/M4-C/M5 는 **첫 배포** 직전 모두 완료되어야 함. M6 는 첫 배포 검증 후 도메인 연결을 위한 **후속 작업**.

---

## §3. 진행 체크리스트 (28-step 통합)

### Phase 1 — IAM 사전 준비 (M4-A §2, 8단계)

- [ ] 1. OIDC Provider 등록 (`token.actions.githubusercontent.com`)
- [ ] 2. GH Actions Role 생성 (`farmos-gh-actions-deploy`) — Trust: Web identity
- [ ] 3. Trust Policy `sub` 조건 (`repo:Himedia-AI-01/FarmOS-Deploy-Test:ref:refs/heads/dev`)
- [ ] 4. Permissions Policy 첨부 (Inline: S3 + CodeDeploy + SSM PutParameter + KMS Encrypt)
- [ ] 5. EC2 Instance Profile Role 생성 (`farmos-ec2-instance`)
- [ ] 6. 관리형 정책 첨부 (`AmazonSSMManagedInstanceCore`, `AmazonEC2RoleforAWSCodeDeploy`)
- [ ] 7. Inline Policy 첨부 (`farmos-ec2-runtime` — SSM Get*, KMS Decrypt, S3 GetObject)
- [ ] 8. CodeDeploy Service Role 생성 (`farmos-codedeploy-svc`)

### Phase 2 — Infra 리소스 (M4-A §3, 6단계)

- [ ] 9. S3 deploy 버킷 생성 (`farmos-codedeploy-242201280878-ap-northeast-2`)
- [ ] 10. 버전 관리 ON, 퍼블릭 차단 ON
- [ ] 11. Lifecycle (30일 IA, 90일 삭제)
- [ ] 12. CodeDeploy Application 생성 (`farmos`, EC2/On-premises)
- [ ] 13. DeploymentGroup (`farmos-prod`, In-place, AND 태그, Auto-Rollback)
- [ ] 14. Parameter Store 28개 키 시드 (검증: `length(Parameters) = 28`)

### Phase 3 — 코드 산출물 검증 (M4-B, 1단계)

- [ ] 15. M4-B 파일 6종 확인 (`appspec.yml` + `scripts/*.sh` 5개) — bash 신택스 + LF + +x 비트

### Phase 4 — EC2 + 첫 배포 (M5 + M4-C, 9단계)

- [ ] 16. EC2 인스턴스 시작 (Ubuntu 24.04, t3.medium, gp3 50GB, 태그 `App=farmos`/`Environment=prod`)
- [ ] 17. Elastic IP 할당 + attach
- [ ] 18. IAM Instance Profile attach (`farmos-ec2-instance`)
- [ ] 19. SSH 접속 + `bootstrap-ec2.sh` 실행
- [ ] 20. bootstrap 검증 (Docker / CodeDeploy agent / IAM / SSM 28키)
- [ ] 21. GH Secrets 6개 등록 (`AWS_REGION`, `AWS_ROLE_ARN`, `S3_DEPLOY_BUCKET`, `CODEDEPLOY_APP`, `CODEDEPLOY_GROUP`, `GHCR_OWNER`)
- [ ] 22. 첫 dev push (`git push origin dev`)
- [ ] 23. GH Actions 모니터링 (3 jobs: build-and-push / build-frontend / deploy)
- [ ] 24. CodeDeploy 콘솔에서 lifecycle 진행 + Succeeded 확인

### Phase 5 — CloudFlare DNS (M6, 4단계)

- [ ] 25. CloudFlare에 도메인 추가 + Nameserver 전환
- [ ] 26. DNS A 레코드 추가 (Proxied, value=Elastic IP)
- [ ] 27. SSL/TLS = `Flexible` + Always Use HTTPS = ON
- [ ] 28. `curl -I https://{도메인}/health` → 200 OK

---

## §4. 예상 소요 시간 (총 ~4시간)

| 모듈 | 작업 | 예상 시간 |
|---|---|---|
| M4-A | AWS Console 14단계 (IAM 8 + Infra 6) | **60~90분** (첫 시도) / 30분 (반복) |
| M4-B | 로컬 bash 검증 (코드 이미 작성됨) | **5~10분** |
| M4-C | GH Secrets 6개 + 첫 push 모니터링 | **30~60분** (첫 배포는 디버깅 포함) |
| M5 | EC2 launch + bootstrap 실행 + 검증 | **40~60분** (apt-get + Docker 이미지 풀에 시간) |
| M6 | CloudFlare 도메인 전환 (Nameserver 전파 별도) | **20~30분 작업 + 1~24시간 NS 전파 대기** |
| **합계** | (NS 전파 제외) | **약 3~4시간** |

> Nameserver 전파 대기는 작업 시간 아님 — M6 §2 단계에서 시작해두고 다른 검증 작업과 병행.

---

## §5. 비용 안내 (월간, 단일 호스트 기준)

| 리소스 | 월 비용 (USD) | 비고 |
|---|---|---|
| EC2 t3.medium (730h, on-demand) | ~$30 | 가장 큰 비용 |
| EBS gp3 50GB | ~$4.0 | $0.08/GB/월 |
| Elastic IP (attached) | $0 | attached 상태에서 무료 |
| Elastic IP (unattached) | $3.6 | $0.005/h × 730 — 인스턴스 중지 후 미해제 시 발생 |
| S3 deploy 버킷 (Standard, ~1GB) | <$0.05 | Lifecycle 90일 삭제 |
| CodeDeploy (EC2/On-premises) | $0 | 무료 (인스턴스 비용에 포함) |
| Parameter Store (Standard tier, 28키) | $0 | Standard tier 무료 |
| KMS (`alias/aws/ssm`) | $0 | AWS 관리 키 무료 |
| Data transfer out (10GB) | ~$0.9 | $0.09/GB |
| CloudFlare Free | $0 | DNS + Flexible SSL 무료 |
| **합계** | **~$35/월** | EC2 단일 호스트 단순 운영 시 |

> ⚠️ **Elastic IP unattached 비용 주의**: 인스턴스를 중지하더라도 EIP 가 release 되지 않으면 시간당 $0.005 (월 ~$3.6) 과금됩니다. 폐기 시 EIP → Release Elastic IP 까지 수행.

---

## §6. 다음 액션 (어디서 시작할지)

상태별로 다음 모듈을 따라가면 됩니다.

| 현재 상태 | 시작 모듈 |
|---|---|
| AWS 콘솔에 OIDC/Role 등 아무것도 없음 | **M4-A** 부터 (1~14 단계) |
| M4-A 완료, 캐치 테이블 8건 채워짐 | **M4-B**(파일 검증) → **M4-C**(GH Secrets 등록) → **M5**(EC2 시작) |
| 위 3개 모두 완료, 첫 배포 직전 | M4-C §2 (첫 dev push) — 트리거 1회 |
| 첫 배포 Succeeded, 도메인 연결 필요 | **M6** |
| 모든 단계 완료, 운영 모드 | M6 §6 검증 + 모니터링 (`docker logs`, CloudWatch, CodeDeploy console) |

---

## §7. 5블록 패턴 안내

본 런북 시리즈의 모든 step 은 다음 5블록을 갖습니다.

| 블록 | 라벨 | 내용 |
|---|---|---|
| **A** | 화면 진입 경로 / 명령 진입점 | AWS Console 클릭 경로 또는 CLI 명령 |
| **B** | 화면 레이아웃 (ASCII) | 실제 콘솔 페이지/모달의 박스/버튼/탭 ASCII art |
| **C** | 입력값 표 | 필드명(콘솔 표기 그대로) → 입력값 → 비고 |
| **D** | 검증 명령 + 기대 출력 | 실행 가능한 CLI + 정확한 응답 1줄 |
| **E** | 공통 함정 | 증상 → 원인 → 즉시 해결 (5건 이내) |

ASCII 박스의 `★` 표시 = 클릭/입력해야 하는 위치, `⚠️` = 자주 틀리는 함정, `☑` = 체크박스, `⦿` = 라디오(선택됨), `◯` = 라디오(미선택).

---

## §8. 트러블슈팅 진입점

| 증상 | 첫 점검 위치 |
|---|---|
| GH Actions Run 이 `Configure AWS credentials` 에서 실패 | M4-A §2 Step 3 (Trust Policy `sub` 조건) → M4-C §1 (`AWS_ROLE_ARN` Secret 값) |
| GH Actions가 S3 cp 에서 403 | M4-A §2 Step 4 (Permissions Policy Resource ARN) → M4-C §1 (`S3_DEPLOY_BUCKET` Secret 값) |
| CodeDeploy 가 "No instances" 로 즉시 실패 | M5 Step 9 (EC2 태그 2개) + M4-A Step 13 (Tag group 1개에 AND) |
| AfterInstall 훅 에서 `length(Parameters) = 0` | M4-A §3 Step 7 (`farmos-ec2-instance` Inline Policy) + M5 Step 4 (IAM Profile attach) |
| ValidateService 가 60초 타임아웃 | EC2에서 `docker logs farmos-api`, `curl http://localhost/health`, `journalctl -u codedeploy-agent` |
| 도메인 접속 시 무한 리다이렉트 | M6 §4 (SSL = Flexible 확인) + nginx.conf 에 `return 301 https` 없는지 확인 (Plan R12) |
| `aws ssm get-parameter` 401/403 (EC2 내부) | M5 Step 4 (IAM Profile attach 확인) + M4-A §3 Step 7 (KMSDecryptForSSM Sid) |

---

## §9. 변경 이력

| 버전 | 날짜 | 변경 사항 |
|---|---|---|
| v1 | 2026-04-28 | 5개 런북 인덱스 + 의존성 그래프 + 28-step 체크리스트 + 비용 안내 |

# 분류 E — `API_BASE` 하드코딩 분산 (Critical, 18 파일)

> **영향도**: 🔴 Critical — 운영 배포 시 로그인/회원가입/일지/IoT 등 **모든 API 호출이 사용자 PC localhost 로 향해 즉시 실패**.
> **검출 시점**: 2026-05-01 (deploy-test 저장소 EC2 첫 배포 검증 중)
> **본 deploy-test 저장소 임시 조치**: `.github/workflows/deploy.yml` 의 `Patch hardcoded localhost URLs in dist (temporary)` step 으로 빌드된 dist 의 문자열을 sed 치환. 메인 정정 후 본 step 제거 예정.

---

## 1. 증상

EC2 배포 후 브라우저에서 로그인 시도 시:

```
POST http://localhost:8000/api/v1/auth/login net::ERR_CONNECTION_REFUSED
```

브라우저가 본인 PC 의 `localhost:8000` 으로 요청을 쏘기 때문에 실패. EC2 의 backend 로그에는 요청이 도달하지 않음.

---

## 2. 근본 원인

`utils/api.ts` 한 군데만 `VITE_API_BASE` 환경변수를 참조하고, **나머지 17개 파일은 모두 `http://localhost:8000/api/v1` 를 모듈 상수로 직접 선언** 하고 있음.

### 영향 받는 파일 (총 18개)

```
src/utils/api.ts                                  ← 유일하게 VITE_ 사용 (정상)
src/context/AuthContext.tsx                       ★ 로그인 — 가장 심각
src/modules/auth/SignupPage.tsx
src/modules/auth/FindIdPage.tsx
src/modules/auth/FindPasswordPage.tsx
src/modules/auth/OnboardingPage.tsx
src/modules/profile/ProfilePage.tsx
src/modules/journal/JournalPage.tsx
src/modules/journal/DailyJournalPanel.tsx
src/modules/diagnosis/DiagnosisPage.tsx
src/modules/diagnosis/chat/DiagnosisChatPage.tsx
src/modules/iot/IoTDashboardPage.tsx
src/modules/subsidy/api.ts
src/hooks/useAIAgent.ts
src/hooks/useDailyJournal.ts
src/hooks/useManualControl.ts                     ← `https://iot.lilpa.moe/api/v1` 별도 도메인
src/hooks/useSensorData.ts
src/hooks/useMarketData.ts
```

### 대표 패턴

```typescript
// AuthContext.tsx (line 3)
const API_BASE = 'http://localhost:8000/api/v1';   // ❌ 하드코딩

const res = await fetch(`${API_BASE}/auth/login`, {  // → http://localhost:8000/api/v1/auth/login
  method: 'POST',
  ...
});
```

```typescript
// utils/api.ts (line 7~9) — 유일한 정상
export const API_BASE: string =
  ((import.meta.env as Record<string, string | undefined>).VITE_API_BASE) ??
  "http://localhost:8000/api/v1";   // VITE_API_BASE 미주입 시 fallback
```

---

## 3. 권장 수정안 (메인 저장소 적용)

### 3.1 옵션 A — `utils/api.ts` 의 `API_BASE` 를 SoT 로 통일 (★ 권장)

**Step 1**: `utils/api.ts` 를 단일 진실 소스(SoT) 로 강화.

```typescript
// frontend/src/utils/api.ts
/**
 * 백엔드 API 베이스 URL.
 *
 * 우선순위:
 *  1. VITE_API_BASE (빌드 타임 주입)
 *  2. 같은 호스트 + /api/v1 (배포 환경 자동 탐지)
 *  3. 'http://localhost:8000/api/v1' (로컬 개발)
 */
const fromEnv = (import.meta.env as Record<string, string | undefined>).VITE_API_BASE;

const fromHost =
  typeof window !== 'undefined' && window.location?.origin
    ? `${window.location.origin}/api/v1`
    : null;

export const API_BASE: string =
  fromEnv ?? fromHost ?? 'http://localhost:8000/api/v1';
```

**Step 2**: 17개 하드코딩 파일에서 `const API_BASE = '...'` 라인을 제거하고 import.

```typescript
// AuthContext.tsx
- const API_BASE = 'http://localhost:8000/api/v1';
+ import { API_BASE } from '@/utils/api';
```

(Vite alias `@` 가 `src/` 를 가리키도록 이미 vite.config.ts 에 설정됨)

### 3.2 옵션 B — 파일 단위 sed 일괄 정정 (단순)

```bash
cd frontend
grep -rn "const API_BASE = ['\"]http://localhost:8000/api/v1['\"]" src/ | cut -d: -f1 | sort -u | \
  xargs sed -i "s|const API_BASE = ['\"]http://localhost:8000/api/v1['\"]|import { API_BASE } from '@/utils/api'|g"
```

> 주의: 일괄 치환 후 import 중복/충돌 검사 필요. 일부 파일은 default import 와 named import 충돌 가능.

### 3.3 옵션 C — 프록시 dev server 만 활용 (비권장)

`vite.config.ts` 의 dev server proxy 는 이미 `/api → http://localhost:8000` 으로 설정되어 있음. **dev 환경에선 상대 경로 (`/api/v1/auth/login`) 사용 가능**. 하드코딩된 `http://localhost:8000` 을 모두 빈 문자열로 바꿔도 dev 가 정상 동작:

```typescript
- const API_BASE = 'http://localhost:8000/api/v1';
+ const API_BASE = '/api/v1';
```

장점: 변경 1곳/파일 (import 추가 불필요).
단점: 향후 다른 staging 환경에서 다른 prefix 가 필요하면 다시 18곳 수정.

---

## 4. IoT 도메인 (`useManualControl.ts`)

```typescript
const API_BASE = 'https://iot.lilpa.moe/api/v1';
```

**별도 도메인** (외부 IoT relay 서버). farmos backend 와 분리되어 있으므로 본 정정 대상 **아님**. 메인 저장소에서도 그대로 유지 권장.

만약 IoT relay 도 farmos backend 와 통합하려는 계획이면 별도 결정 필요.

---

## 5. 본 deploy-test 저장소 임시 조치

`.github/workflows/deploy.yml` 의 `build-frontend` 잡에 추가된 step:

```yaml
- name: Patch hardcoded localhost URLs in dist (temporary)
  run: |
    find frontend/dist -type f \( -name "*.js" -o -name "*.html" \) \
      -exec sed -i 's|http://localhost:8000/api/v1|/api/v1|g' {} \;
```

이 step 은:
- 빌드된 dist 의 JS/HTML 파일 안에 박힌 `http://localhost:8000/api/v1` 문자열을 `/api/v1` (상대경로) 로 일괄 치환
- IoT 도메인 (`https://iot.lilpa.moe`) 은 그대로 유지
- **메인 저장소가 위 §3 옵션 A/B 로 정정되면 본 step 은 제거**

---

## 6. 검증

본 임시 패치 적용 후:

```bash
# EC2 SSH
grep -l "http://localhost:8000" /opt/farmos/dist/assets/*.js
# → (출력 없음) = 정상

curl -X POST http://127.0.0.1/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}'
# → 401 (잘못된 자격증명) 또는 200 (성공) — 어느 쪽이든 backend 도달
```

브라우저 DevTools Network 탭:
```
POST http://13.x.x.x/api/v1/auth/login   ← localhost 가 아닌 EC2 IP
```

---

## 7. 우선순위

🔴 **Critical** — 본 18 파일 중 다음 4개는 즉시 수정 필요 (운영 차단):
- `AuthContext.tsx` (로그인/로그아웃/세션 갱신)
- `SignupPage.tsx` (회원가입)
- `FindIdPage.tsx`, `FindPasswordPage.tsx` (계정 복구)

나머지는 부속 모듈 (일지/진단/IoT) — 1주 내 수정 권장. 단, 본 deploy-test 의 sed 패치로 임시 동작은 가능.

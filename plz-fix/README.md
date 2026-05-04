# plz-fix — Frontend TS 빌드 에러 보고서

> **출처**: GitHub Actions `Deploy (prod)` → `build-frontend` 잡 (run on commit `<sha>` of branch `dev`)
> **검출 도구**: `npm run build` → `tsc -b && vite build`
> **검출 시점**: 2026-04-28
> **저장소**: `Himedia-AI-01/FarmOS-Deploy-Test` (배포 테스트용)
> **권장 적용 위치**: 본 에러들은 **메인 FarmOS 저장소의 frontend 코드**에 존재하며, 본 배포 테스트 저장소가 아닌 **상류(upstream) 저장소에서 정정 후 동기화**되는 것이 바람직합니다.

---

## 1. 요약

총 **18건의 TypeScript 컴파일 에러**가 frontend 빌드를 막고 있습니다.

| # | 분류 | 건수 | 영향도 | 카테고리 문서 |
|---|------|:---:|:---:|---|
| A | **실 버그 — Discriminated Union 내로잉 누락** | 6 | 🔴 Critical | [01-useManualControl-narrowing.md](./01-useManualControl-narrowing.md) |
| B | **미사용 import/변수 (TS6133)** | 9 | 🟢 Minor | [02-unused-imports.md](./02-unused-imports.md) |
| C | **Recharts v3 타입 호환 (TS2322)** | 2 | 🟡 Major | [03-recharts-v3-formatter.md](./03-recharts-v3-formatter.md) |
| D | **Literal Union 캐스팅 (TS2345)** | 1 | 🟢 Minor | [04-journal-literal-union.md](./04-journal-literal-union.md) |
| **TS 빌드 합계** | | **18** | | |
| E | **API_BASE 하드코딩 분산** (런타임 검증 중 발견) | 17 파일 | 🔴 Critical | [05-hardcoded-api-base.md](./05-hardcoded-api-base.md) |

---

## 2. 영향도 기준

| 영향도 | 의미 | 권장 처리 |
|---|---|---|
| 🔴 **Critical** | 런타임 동작에 직접 영향. 잘못된 속성 접근으로 `undefined` 또는 NaN 발생 가능 | 즉시 수정 |
| 🟡 **Major** | 빌드 차단 + 라이브러리 업그레이드/마이그레이션 필요. 런타임 동작은 정상 가능성 큼 | 1주 내 수정 |
| 🟢 **Minor** | 코드 품질(데드 코드/lint) 이슈. 런타임 무영향 | 다음 리팩토링 사이클 |

---

## 3. 18건 인덱스 (파일/라인 → 카테고리)

| # | 파일 | 라인 | TS 코드 | 분류 | 영향도 |
|--:|---|--:|---|---|:---:|
| 1 | `src/hooks/useManualControl.ts` | 140 | TS2339 | A | 🔴 |
| 2 | `src/hooks/useManualControl.ts` | 141 | TS2339 | A | 🔴 |
| 3 | `src/hooks/useManualControl.ts` | 160 | TS2339 | A | 🔴 |
| 4 | `src/hooks/useManualControl.ts` | 161 | TS2339 | A | 🔴 |
| 5 | `src/hooks/useManualControl.ts` | 274 | TS2352 | A | 🔴 |
| 6 | `src/hooks/useManualControl.ts` | 282 | TS2352 | A | 🔴 |
| 7 | `src/modules/diagnosis/DiagnosisPage.tsx` | 11 | TS6133 | B | 🟢 |
| 8 | `src/modules/diagnosis/DiagnosisPage.tsx` | 367 | TS6133 | B | 🟢 |
| 9 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 2 | TS6133 | B | 🟢 |
| 10 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 2 | TS6133 | B | 🟢 |
| 11 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 17 | TS6133 | B | 🟢 |
| 12 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 43 | TS6133 | B | 🟢 |
| 13 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 261 | TS6133 | B | 🟢 |
| 14 | `src/modules/iot/IoTDashboardPage.tsx` | 216 | TS2322 | C | 🟡 |
| 15 | `src/modules/iot/IoTDashboardPage.tsx` | 220 | TS2322 | C | 🟡 |
| 16 | `src/modules/journal/JournalEntryForm.tsx` | 151 | TS2345 | D | 🟢 |
| 17 | `src/modules/journal/JournalPage.tsx` | 18 | TS6133 | B | 🟢 |
| 18 | `src/modules/journal/JournalPage.tsx` | 53 | TS6133 | B | 🟢 |
| 19 | `src/modules/journal/STTInput.tsx` | 8 | TS6133 | B | 🟢 |
| 20 | `src/modules/journal/STTInput.tsx` | 57 | TS6133 | B | 🟢 |
| 21 | `src/modules/reviews/RAGSearchPanel.tsx` | 92 | TS6133 | B | 🟢 |
| 22 | `src/modules/reviews/ReviewsPage.tsx` | 20 | TS6133 | B | 🟢 |

> **참고**: 위 표의 #는 단순 행 번호이며 실제 컴파일러 출력 순서와 동일하지 않을 수 있습니다.
> "총 18건"은 컴파일러가 출력한 에러 수 기준 — 동일 라인의 다중 항목(예: import 1줄에 2개 미사용 식별자)은 별도 카운트.

---

## 4. 원본 컴파일러 출력 (참고용)

```
Error: src/hooks/useManualControl.ts(140,38): error TS2339: Property 'window_open_pct' does not exist on type 'VentilationState | IrrigationControlState | LightingState | ShadingState'.
  Property 'window_open_pct' does not exist on type 'IrrigationControlState'.
Error: src/hooks/useManualControl.ts(141,32): error TS2339: Property 'fan_speed' does not exist on type 'VentilationState | IrrigationControlState | LightingState | ShadingState'.
  Property 'fan_speed' does not exist on type 'IrrigationControlState'.
Error: src/hooks/useManualControl.ts(160,32): error TS2339: Property 'shade_pct' does not exist on type 'VentilationState | IrrigationControlState | LightingState | ShadingState'.
  Property 'shade_pct' does not exist on type 'VentilationState'.
Error: src/hooks/useManualControl.ts(161,37): error TS2339: Property 'insulation_pct' does not exist on type 'VentilationState | IrrigationControlState | LightingState | ShadingState'.
  Property 'insulation_pct' does not exist on type 'VentilationState'.
Error: src/hooks/useManualControl.ts(274,27): error TS2352: Conversion of type 'VentilationState | LightingState | ShadingState' to type 'Record<string, unknown>' may be a mistake because neither type sufficiently overlaps with the other. If this was intentional, convert the expression to 'unknown' first.
  Type 'ShadingState' is not comparable to type 'Record<string, unknown>'.
    Index signature for type 'string' is missing in type 'ShadingState'.
Error: src/hooks/useManualControl.ts(282,31): error TS2352: Conversion of type 'Record<string, unknown>' to type 'VentilationState | IrrigationControlState | LightingState | ShadingState' may be a mistake because neither type sufficiently overlaps with the other. If this was intentional, convert the expression to 'unknown' first.
  Type 'Record<string, unknown>' is missing the following properties from type 'IrrigationControlState': valve_open, daily_total_L, last_watered, nutrient, and 5 more.
Error: src/modules/diagnosis/DiagnosisPage.tsx(11,7): error TS6133: 'REGIONS' is declared but its value is never read.
Error: src/modules/diagnosis/DiagnosisPage.tsx(367,9): error TS6133: 'customInputProps' is declared but its value is never read.
Error: src/modules/diagnosis/chat/DiagnosisChatPage.tsx(2,36): error TS6133: 'useParams' is declared but its value is never read.
Error: src/modules/diagnosis/chat/DiagnosisChatPage.tsx(2,60): error TS6133: 'useReactRouterParams' is declared but its value is never read.
Error: src/modules/diagnosis/chat/DiagnosisChatPage.tsx(17,38): error TS6133: 'isUser' is declared but its value is never read.
Error: src/modules/diagnosis/chat/DiagnosisChatPage.tsx(43,68): error TS6133: 'match' is declared but its value is never read.
Error: src/modules/diagnosis/chat/DiagnosisChatPage.tsx(261,9): error TS6133: 'isHistory' is declared but its value is never read.
Error: src/modules/iot/IoTDashboardPage.tsx(216,21): error TS2322: Type '(value: number, _name: string, props: any) => [string, string]' is not assignable to type 'Formatter<ValueType, NameType> & ((value: ValueType, name: NameType, item: TooltipPayloadEntry, index: number, payload: TooltipPayload) => ReactNode | [...])'.
Error: src/modules/iot/IoTDashboardPage.tsx(220,21): error TS2322: Type '(label: string) => string' is not assignable to type '((label: ReactNode, payload: readonly Payload<ValueType, NameType>[]) => ReactNode) & ((label: any, payload: TooltipPayload) => ReactNode)'.
Error: src/modules/journal/JournalEntryForm.tsx(151,45): error TS2345: Argument of type 'string' is not assignable to parameter of type 'SetStateAction<"수확" | "사전준비" | "경운" | "파종" | "정식" | "작물관리">'.
Error: src/modules/journal/JournalPage.tsx(18,1): error TS6133: 'DailySummaryCard' is declared but its value is never read.
Error: src/modules/journal/JournalPage.tsx(53,5): error TS6133: 'fetchDailySummary' is declared but its value is never read.
Error: src/modules/journal/STTInput.tsx(8,25): error TS6133: 'MdAutorenew' is declared but its value is never read.
Error: src/modules/journal/STTInput.tsx(57,10): error TS6133: 'progress' is declared but its value is never read.
Error: src/modules/reviews/RAGSearchPanel.tsx(92,28): error TS6133: 'i' is declared but its value is never read.
Error: src/modules/reviews/ReviewsPage.tsx(20,15): error TS6133: 'isLoading' is declared but its value is never read.
Error: Process completed with exit code 2.
```

---

## 5. tsconfig 컨텍스트 (수정 결정 시 참고)

`frontend/tsconfig.app.json` 의 다음 strict 옵션이 유효한 경우에만 본 에러들이 발생합니다:

| 옵션 | 영향 |
|---|---|
| `noUnusedLocals` | TS6133 (분류 B 9건) 발생 트리거 |
| `noUnusedParameters` | TS6133 일부 (분류 B 의 parameter) 발생 트리거 |
| `strict` 또는 `strictNullChecks` | TS2339/TS2352/TS2322/TS2345 발생 트리거 |

**옵션 1 — 코드 정정 (권장)**: 본 문서의 카테고리별 .md 의 권장 패치를 적용. 데드 코드 제거 + 타입 정확도 개선.
**옵션 2 — strict 완화 (비권장)**: `noUnusedLocals: false` 등으로 우회. 향후 실 버그 검출 능력 약화.

---

## 6. 우선순위 권고

| 순서 | 작업 | 사유 |
|--:|---|---|
| 1 | **분류 A** (useManualControl 6건) | 🔴 Critical — 런타임 `undefined` 위험. discriminated union 미사용으로 IoT 시뮬 토글 버그 잠재. |
| 2 | **분류 C** (Recharts v3 2건) | 🟡 Major — Recharts 메이저 업그레이드 호환성. 추후 차트 추가 시에도 동일 문제 재발 가능. |
| 3 | **분류 D** (JournalEntryForm 1건) | 🟢 Minor — 단순 캐스트로 즉시 해결. |
| 4 | **분류 B** (미사용 9건) | 🟢 Minor — 리팩토링 사이클에 일괄 정리. ESLint `no-unused-vars` 규칙으로도 해결 가능. |

---

## 7. 본 배포 테스트 저장소(`FarmOS-Deploy-Test`)에서의 처리 정책

> **본 저장소는 배포 파이프라인 검증 전용입니다 — frontend 코드 수정은 직접 적용하지 않습니다.**

- 본 저장소의 `frontend/src/**` 는 **메인 FarmOS 저장소 snapshot 그대로 유지**됩니다 (HEAD 무수정).
- 18건 에러는 **본 문서(`plz-fix/`)로 정리하여 메인 저장소 팀에 전달**합니다.
- 메인 저장소에서 PR 머지 후 본 저장소로 동기화되면 자동으로 `build-frontend` CI 그린.
- 따라서 본 저장소의 CI `build-frontend` 잡은 **메인에서 수정이 머지될 때까지 의도적으로 빨간불** 상태입니다 — 배포 인프라(M1~M4-C, M5, M6)와는 독립적으로 검증 가능.

---

## 8. 작성/검증 정보

- 본 문서 작성: 2026-04-28
- 컴파일러 버전: `typescript ^5.9.3` (frontend/package.json devDependency)
- 빌드 스크립트: `npm run build` = `tsc -b && vite build` (frontend/package.json:8)
- 트리거 워크플로우: `.github/workflows/deploy.yml` → `build-frontend` 잡

---

## 9. 카테고리 문서 링크

- [01-useManualControl-narrowing.md](./01-useManualControl-narrowing.md) — 🔴 분류 A (6건)
- [02-unused-imports.md](./02-unused-imports.md) — 🟢 분류 B (9건)
- [03-recharts-v3-formatter.md](./03-recharts-v3-formatter.md) — 🟡 분류 C (2건)
- [04-journal-literal-union.md](./04-journal-literal-union.md) — 🟢 분류 D (1건)

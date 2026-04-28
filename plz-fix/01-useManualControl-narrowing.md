# 분류 A — `useManualControl.ts` Discriminated Union 내로잉 누락 (Critical, 6건)

> **영향도**: 🔴 Critical — 런타임 시 IoT 시뮬레이터 토글에서 `undefined` 접근 가능성. AI 규칙 vs 수동 조작 race condition 보호 로직(SSE 처리)이 잘못된 타입으로 컴파일러 검증 우회.
> **파일**: `frontend/src/hooks/useManualControl.ts`
> **에러 수**: 6건 (TS2339 × 4, TS2352 × 2)

---

## 1. 에러 원본

```
src/hooks/useManualControl.ts(140,38): error TS2339: Property 'window_open_pct' does not exist on type 'VentilationState | IrrigationControlState | LightingState | ShadingState'.
  Property 'window_open_pct' does not exist on type 'IrrigationControlState'.

src/hooks/useManualControl.ts(141,32): error TS2339: Property 'fan_speed' does not exist on type ...
  Property 'fan_speed' does not exist on type 'IrrigationControlState'.

src/hooks/useManualControl.ts(160,32): error TS2339: Property 'shade_pct' does not exist on type ...
  Property 'shade_pct' does not exist on type 'VentilationState'.

src/hooks/useManualControl.ts(161,37): error TS2339: Property 'insulation_pct' does not exist on type ...
  Property 'insulation_pct' does not exist on type 'VentilationState'.

src/hooks/useManualControl.ts(274,27): error TS2352: Conversion of type 'VentilationState | LightingState | ShadingState' to type 'Record<string, unknown>' may be a mistake because neither type sufficiently overlaps with the other.
  Type 'ShadingState' is not comparable to type 'Record<string, unknown>'.
    Index signature for type 'string' is missing in type 'ShadingState'.

src/hooks/useManualControl.ts(282,31): error TS2352: Conversion of type 'Record<string, unknown>' to type 'VentilationState | IrrigationControlState | LightingState | ShadingState' may be a mistake because neither type sufficiently overlaps with the other.
  Type 'Record<string, unknown>' is missing the following properties from type 'IrrigationControlState': valve_open, daily_total_L, last_watered, nutrient, and 5 more.
```

---

## 2. 근본 원인

### 2.1 TS2339 (lines 140, 141, 160, 161)

`simulateButton` 함수 안에서:

```typescript
const simulateButton = useCallback((controlType: ControlCommand['control_type']) => {
  if (!controlState) return;

  const current = controlState[controlType];   // ← union 타입
  const newActive = !current.active;

  switch (controlType) {
    case 'ventilation':
      if (!newActive) {
        lastKnownValuesRef.current.ventilation = {
          window_open_pct: current.window_open_pct,  // ❌ TS2339
          fan_speed: current.fan_speed,                // ❌ TS2339
        };
      }
      // ...
      break;
    case 'shading':
      if (!newActive) {
        lastKnownValuesRef.current.shading = {
          shade_pct: current.shade_pct,            // ❌ TS2339
          insulation_pct: current.insulation_pct,  // ❌ TS2339
        };
      }
      // ...
      break;
  }
}, [controlState, fetchState]);
```

**문제**: `controlType: 'ventilation' | 'irrigation' | 'lighting' | 'shading'` 의 인덱스 접근 결과 `controlState[controlType]` 은 4개 union 으로 추론되며, **switch case 안에서 `current` 변수는 타입 좁히기 대상이 아닙니다** (스위치 변수가 `controlType` 자체이므로 `current`는 좁혀지지 않음). 따라서 `window_open_pct`/`shade_pct` 같은 비공통 필드는 union 의 모든 멤버에 존재하지 않아 TS2339.

### 2.2 TS2352 (lines 274, 282)

`handleControlEvent` 함수 안에서:

```typescript
const prevState = prev[ct] as Record<string, unknown>;           // ❌ TS2352 (line 274)
// ...
return { ...prev, [ct]: merged as (typeof prev)[typeof ct] };     // ❌ TS2352 (line 282)
```

**문제**: `VentilationState | IrrigationControlState | LightingState | ShadingState` 타입은 인덱스 시그니처(`[key: string]: unknown`)를 갖지 않으므로 직접 `as Record<string, unknown>` 캐스트가 거부됩니다. TS 4.x 부터 충분히 겹치지 않는 타입 간 단일 `as` 캐스트는 안전하지 않다고 판단해 차단됩니다.

---

## 3. 권장 수정안 (메인 저장소 적용)

### 3.1 simulateButton — case 별 직접 참조로 narrowing

**Before**:
```typescript
const current = controlState[controlType];
const newActive = !current.active;

switch (controlType) {
  case 'ventilation':
    if (!newActive) {
      lastKnownValuesRef.current.ventilation = {
        window_open_pct: current.window_open_pct,
        fan_speed: current.fan_speed,
      };
    }
    // ...
}
```

**After**:
```typescript
const newActive = !controlState[controlType].active;

// Discriminated narrowing per case — controlState[controlType] 는 union 이라 직접 속성 접근 시
// 비공통 필드(window_open_pct/shade_pct 등)에서 TS2339. case 별로 구체 키로 좁혀 접근.
switch (controlType) {
  case 'ventilation': {
    const current = controlState.ventilation;   // ← VentilationState 로 좁혀짐
    if (!newActive) {
      lastKnownValuesRef.current.ventilation = {
        window_open_pct: current.window_open_pct,  // ✅
        fan_speed: current.fan_speed,                // ✅
      };
    }
    // ...
    break;
  }
  case 'shading': {
    const current = controlState.shading;        // ← ShadingState 로 좁혀짐
    if (!newActive) {
      lastKnownValuesRef.current.shading = {
        shade_pct: current.shade_pct,            // ✅
        insulation_pct: current.insulation_pct,  // ✅
      };
    }
    // ...
    break;
  }
  // irrigation / lighting case 동일
}
```

**핵심**: switch case 안에서 `controlState.ventilation` (또는 `.shading`) 처럼 **리터럴 키로 직접 접근**하면 해당 케이스 안에서만 구체 타입(`VentilationState`/`ShadingState`)으로 좁혀집니다. 이는 TypeScript 의 Control Flow Analysis 가 키 접근에 대해 동작하는 표준 패턴입니다.

### 3.2 handleControlEvent — `unknown` 경유 캐스트

**Before**:
```typescript
const prevState = prev[ct] as Record<string, unknown>;
// ...
return { ...prev, [ct]: merged as (typeof prev)[typeof ct] };
```

**After**:
```typescript
// 인덱스 시그니처 부재 union 이라 직접 cast 시 TS2352. unknown 경유로 안전 캐스트.
const prevState = prev[ct] as unknown as Record<string, unknown>;
// ...
return { ...prev, [ct]: merged as unknown as (typeof prev)[typeof ct] };
```

**핵심**: TypeScript 는 두 타입 간 충분한 겹침이 없을 때 `as` 단일 캐스트를 거부하지만, `unknown`으로 한 번 거치면 안전(unsafe) 캐스트임을 명시적으로 표현해 통과시킵니다. 본 케이스는 SSE 이벤트의 동적 payload 처리이므로 unknown 경유가 의도와 부합.

### 3.3 (개선 권장 — 선택) ControlItemState 인덱스 시그니처

위 3.2 의 unknown 경유 캐스트 대신, 더 깔끔한 해결책은 `ControlItemState` 또는 4개 하위 인터페이스에 인덱스 시그니처를 추가하는 것입니다:

```typescript
// frontend/src/types/index.ts
export interface ControlItemState {
  active: boolean;
  led_on: boolean;
  locked: boolean;
  source: "manual" | "button" | "ai" | "rule" | "tool";
  updated_at: string | null;
  [key: string]: unknown;  // ← 추가
}
```

단점: 모든 멤버가 `unknown` 으로도 접근 가능해져 타입 안전성 약화. **3.2 의 unknown 경유가 더 안전.**

---

## 4. 적용 대상 파일

```
frontend/src/hooks/useManualControl.ts   (lines 130~285)
```

영향 받는 함수 2개:
- `simulateButton` (lines 126~205) — TS2339 4건
- `handleControlEvent` (lines 241~284) — TS2352 2건

---

## 5. 검증 명령

```bash
cd frontend
npm ci
npx tsc --noEmit
# 기대: useManualControl.ts 관련 6건 에러 모두 해결
```

또는 `npm run build` 통과.

---

## 6. 영향도 상세

| 항목 | 위험 |
|---|---|
| **런타임 동작** | `current.window_open_pct` 같은 접근이 union 의 일부 멤버에서 `undefined` 반환 가능. 시뮬 OFF→ON 토글 시 `lastKnownValuesRef` 에 `undefined` 저장. |
| **시뮬 복원 실패** | 마지막 값 저장이 깨지면 시뮬 ON 토글 시 0/기본값으로 복원되어 사용자가 "이전 값 복원이 동작하지 않는다"고 인식. |
| **SSE race guard** | `handleControlEvent` 의 `as Record<string, unknown>` 가 컴파일러 검증을 우회하지만 런타임은 정상. 다만 향후 ControlItemState 변경 시 타입 안전성 부재로 깨질 가능성. |
| **테스트 커버리지** | 본 hook 의 unit/integration 테스트가 없거나 부족할 가능성. 수정 시 `simulateButton('ventilation', false→true)` 시나리오 테스트 추가 권장. |

---

## 7. 관련 Design 참조 (`docs/02-design/features/iot-manual-control.design.md`)

- **§3.1**: `on` 마스터 스위치 (ESP8266 active 와 별개)
- **§5.2**: 시뮬 OFF 시 현재값 저장 → 수동 ON 토글 시 복원
- **§3.3.3 (fix-toggle-shade-heat)**: ESP8266 버튼 SSoT race guard

위 디자인 의도를 보존하면서 수정해야 하므로, 단순 `as any` 캐스트 회피는 권장하지 않습니다.

---

## 8. 본 배포 테스트 저장소 처리 — `FarmOS-Deploy-Test`

본 저장소(`Himedia-AI-01/FarmOS-Deploy-Test`)에는 **임시 패치를 적용하지 않습니다.** `frontend/src/hooks/useManualControl.ts` 는 메인 저장소 snapshot 그대로 HEAD 유지(미수정).

**수정 권장 위치**: 메인 FarmOS 저장소 → `feat/fix-useManualControl-narrowing` (또는 적절한 브랜치) → PR.

**적용 후 동기화**: 메인 머지 → 본 저장소로 동기화(merge/rebase/cherry-pick) → CI `build-frontend` 잡 자동 그린.

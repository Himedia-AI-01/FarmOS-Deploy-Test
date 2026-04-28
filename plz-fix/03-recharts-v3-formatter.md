# 분류 C — Recharts v3 Tooltip Formatter 타입 호환 (Major, 2건)

> **영향도**: 🟡 Major — Recharts 메이저 업그레이드(v2 → v3) 호환성. 런타임 동작은 정상이지만 strict TS 빌드 차단.
> **파일**: `frontend/src/modules/iot/IoTDashboardPage.tsx`
> **에러 수**: 2건 (TS2322 × 2)
> **관련 패키지**: `recharts ^3.8.1` (frontend/package.json:25)

---

## 1. 에러 원본

```
src/modules/iot/IoTDashboardPage.tsx(216,21): error TS2322:
  Type '(value: number, _name: string, props: any) => [string, string]'
    is not assignable to type 'Formatter<ValueType, NameType> & ((value: ValueType, name: NameType, item: TooltipPayloadEntry, index: number, payload: TooltipPayload) => ReactNode | [...])'.
  Type '(value: number, _name: string, props: any) => [string, string]' is not assignable to type 'Formatter<ValueType, NameType>'.
    Types of parameters 'value' and 'value' are incompatible.
      Type 'ValueType | undefined' is not assignable to type 'number'.
        Type 'undefined' is not assignable to type 'number'.

src/modules/iot/IoTDashboardPage.tsx(220,21): error TS2322:
  Type '(label: string) => string'
    is not assignable to type '((label: ReactNode, payload: readonly Payload<ValueType, NameType>[]) => ReactNode) & ((label: any, payload: TooltipPayload) => ReactNode)'.
  Type '(label: string) => string' is not assignable to type '(label: ReactNode, payload: readonly Payload<ValueType, NameType>[]) => ReactNode'.
    Types of parameters 'label' and 'label' are incompatible.
      Type 'ReactNode' is not assignable to type 'string'.
        Type 'undefined' is not assignable to type 'string'.
```

---

## 2. 근본 원인

### 2.1 Recharts v2 → v3 시그니처 변화

`recharts@2.x` 에서 `Tooltip` 의 `formatter` 와 `labelFormatter` 시그니처는 다음과 같았습니다:

```typescript
// v2 (구)
formatter?: (value: number | string, name: string, props: any) => [string, string] | string;
labelFormatter?: (label: string) => React.ReactNode;
```

`recharts@3.x` 에서는 다음과 같이 강화되었습니다:

```typescript
// v3 (현재 farmos-poc 가 사용 중)
formatter?: Formatter<ValueType, NameType>;
// ↳ Formatter<V, N> = (
//     value: V | undefined,        // ← undefined 가능
//     name: N,
//     item: TooltipPayloadEntry,
//     index: number,
//     payload: TooltipPayload
//   ) => React.ReactNode | [React.ReactNode, React.ReactNode];

labelFormatter?: (
  label: React.ReactNode,                            // ← ReactNode | undefined 가능
  payload: readonly Payload<ValueType, NameType>[],
) => React.ReactNode;
```

핵심 변경 2가지:
1. **`value` 파라미터가 `undefined` 가능** — `Formatter<ValueType>` 의 ValueType 자체가 `undefined` 포함 union.
2. **`label` 파라미터가 `ReactNode`** — `string` 이 아닌 `ReactNode | undefined`.

### 2.2 현재 코드

```typescript
// IoTDashboardPage.tsx (lines 215~221)
<Tooltip
  formatter={(value: number, _name: string, props: any) => [   // ❌ value: number 는 undefined 비호환
    `${value}분`,
    `밸브 ${props.payload.valveAction}`,
  ]}
  labelFormatter={(label: string) => label}                     // ❌ label: string 은 ReactNode 비호환
  contentStyle={{ fontSize: 12, borderRadius: 8 }}
/>
```

`value: number` 와 `label: string` 으로 타입을 좁혔는데, v3 의 ValueType 은 `string | number | (string | number)[] | undefined` 이고 label 은 `React.ReactNode` 이므로 호환되지 않습니다.

---

## 3. 권장 수정안 (메인 저장소 적용)

### 3.1 옵션 A — 시그니처를 v3 호환으로 정정 (★ 권장)

```typescript
<Tooltip
  formatter={(value, _name, item) => {
    // v3: value 는 ValueType | undefined, item.payload 에서 valveAction 접근.
    if (value == null) return ['-', ''];
    const valveAction = item?.payload?.valveAction ?? '-';
    return [`${value}분`, `밸브 ${valveAction}`];
  }}
  labelFormatter={(label) => String(label ?? '')}
  contentStyle={{ fontSize: 12, borderRadius: 8 }}
/>
```

**핵심**:
- 인라인 타입 어노테이션 제거 — Recharts 가 자동 추론
- `value == null` 가드로 `undefined` 처리
- `item?.payload?.valveAction` 옵셔널 체이닝
- `String(label ?? '')` 로 ReactNode → string 변환

### 3.2 옵션 B — `as any` 캐스트 우회 (Quick fix, 비권장)

```typescript
<Tooltip
  formatter={((value: any, _name: any, props: any) => [
    `${value}분`,
    `밸브 ${props?.payload?.valveAction ?? '-'}`,
  ]) as any}
  labelFormatter={((label: any) => String(label ?? '')) as any}
  contentStyle={{ fontSize: 12, borderRadius: 8 }}
/>
```

타입 안전성 약화. 다른 차트 컴포넌트에서 동일 문제 재발 시 매번 `as any` 추가 → 부채 누적.

### 3.3 옵션 C — `recharts` 다운그레이드 (가장 비권장)

`package.json` 의 `recharts: ^3.8.1` 을 `^2.x` 로 다운그레이드하면 기존 시그니처와 호환되지만:
- 보안/버그 수정 누락
- 향후 React 19 호환성 등 v3 가 받는 혜택 포기
- 다른 모듈이 v3 고유 기능 사용 시 충돌

---

## 4. 검증 명령

```bash
cd frontend
npm ci
npx tsc --noEmit
# 기대: IoTDashboardPage.tsx 의 TS2322 2건 해결
```

또는 `npm run build` 통과.

브라우저 검증 (선택):
1. `npm run dev` → /iot/dashboard
2. Bar 차트 hover → Tooltip 표시 확인
3. label / value / valveAction 모두 정상 출력

---

## 5. 영향도 상세

| 항목 | 위험 |
|---|---|
| **런타임 동작** | 현재 코드도 정상 작동 가능 (TS 컴파일 시점만 차단). v3 의 더 엄격한 타입은 안전성 향상이 목적. |
| **다른 Recharts 사용처 영향** | grep 결과 `recharts` import 위치 추가 점검 필요. 다른 차트(영농일지 통계, 리뷰 분석 등)에서도 v2 시그니처 잔재 가능. |
| **장기 유지보수** | 옵션 A 적용 시 향후 차트 추가도 동일 패턴으로 안전. 옵션 B 는 부채 누적. |

---

## 6. 추가 점검 권장 (메인 저장소 작업 시)

```bash
cd frontend
grep -rn "formatter=\|labelFormatter=" src/ --include="*.tsx" --include="*.ts"
# Tooltip / Legend / Label 의 formatter prop 모두 추출
# v3 시그니처로 일괄 정정
```

---

## 7. 본 배포 테스트 저장소 처리

본 저장소(`Himedia-AI-01/FarmOS-Deploy-Test`)에는 **임시 패치를 적용하지 않습니다.** `frontend/src/modules/iot/IoTDashboardPage.tsx` 는 메인 snapshot 그대로 HEAD 유지.

**메인 저장소 권장**: 옵션 A (시그니처 v3 호환 정정). 옵션 B (`as any`) 는 부채 누적 위험.

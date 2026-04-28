# 분류 D — `JournalEntryForm.tsx` Literal Union 캐스팅 (Minor, 1건)

> **영향도**: 🟢 Minor — 단순 캐스트로 즉시 해결. 런타임 무영향.
> **파일**: `frontend/src/modules/journal/JournalEntryForm.tsx`
> **에러 수**: 1건 (TS2345 × 1)

---

## 1. 에러 원본

```
src/modules/journal/JournalEntryForm.tsx(151,45): error TS2345:
  Argument of type 'string' is not assignable to parameter of type
  'SetStateAction<"수확" | "사전준비" | "경운" | "파종" | "정식" | "작물관리">'.
```

---

## 2. 근본 원인

### 2.1 현재 코드

```typescript
// JournalEntryForm.tsx (lines 149~159)
<select
  value={workStage}
  onChange={(e) => setWorkStage(e.target.value)}   // ❌ e.target.value 는 string
>
  {WORK_STAGES.map((s) => (
    <option key={s} value={s}>
      {s}
    </option>
  ))}
</select>
```

`workStage` 의 useState 타입이 literal union 이라고 추정됩니다 (어딘가의 `WORK_STAGES` 배열에서 `as const` 또는 `typeof WORK_STAGES[number]` 식으로 좁힘). 그러나 HTML `<select>` 의 `e.target.value` 는 항상 `string` 으로 추론되므로 setter 호출 시 타입 불일치.

### 2.2 `WORK_STAGES` 정의 추정

```typescript
const WORK_STAGES = ['사전준비', '경운', '파종', '정식', '작물관리', '수확'] as const;
type WorkStage = typeof WORK_STAGES[number];
const [workStage, setWorkStage] = useState<WorkStage>('사전준비');
```

또는:
```typescript
const [workStage, setWorkStage] = useState<'수확' | '사전준비' | '경운' | '파종' | '정식' | '작물관리'>('사전준비');
```

`<option>` 들이 `WORK_STAGES.map()` 으로 생성되므로 **실제 e.target.value 는 항상 6개 중 하나**이지만, TypeScript 는 그것을 정적으로 알 수 없음.

---

## 3. 권장 수정안 (메인 저장소 적용)

### 3.1 옵션 A — 인라인 캐스트 (★ Quick & 권장)

**Before**:
```typescript
<select
  value={workStage}
  onChange={(e) => setWorkStage(e.target.value)}
>
```

**After**:
```typescript
<select
  value={workStage}
  onChange={(e) => setWorkStage(e.target.value as typeof workStage)}
>
```

**근거**: `<option>` 들이 `WORK_STAGES` 배열에서 생성되므로 값이 union 멤버임이 보장됨. `typeof workStage` 캐스트는 안전한 좁히기.

### 3.2 옵션 B — 헬퍼 함수로 검증 (보수적)

```typescript
const WORK_STAGES = ['사전준비', '경운', '파종', '정식', '작물관리', '수확'] as const;
type WorkStage = typeof WORK_STAGES[number];

function isWorkStage(v: string): v is WorkStage {
  return (WORK_STAGES as readonly string[]).includes(v);
}

// ...

<select
  value={workStage}
  onChange={(e) => {
    if (isWorkStage(e.target.value)) {
      setWorkStage(e.target.value);
    }
  }}
>
```

장점: 런타임 검증 추가. 단점: 코드 양 증가.

### 3.3 옵션 C — useState 타입 완화 (비권장)

```typescript
const [workStage, setWorkStage] = useState<string>('사전준비');
```

literal union 의 타입 안전성을 포기. 다른 곳에서 `workStage` 가 union 멤버임을 의존하면 깨짐.

---

## 4. 검증 명령

```bash
cd frontend
npm ci
npx tsc --noEmit
# 기대: JournalEntryForm.tsx 의 TS2345 1건 해결
```

브라우저 검증 (선택):
1. `npm run dev` → /journal → "새 항목" 버튼
2. 작업단계 select 박스 → 6개 옵션 표시 + 선택 시 폼 값 갱신 확인

---

## 5. 영향도 상세

🟢 **Minor** — 옵션 A 인라인 캐스트로 1줄 수정. 런타임 영향 0.

옵션 A 가 안전한 이유:
- `<option value={s}>` 의 `s` 는 `WORK_STAGES.map((s) => ...)` 에서 나옴
- `WORK_STAGES` 가 literal union 의 source of truth 이므로 사용자가 select 로 선택 가능한 값은 항상 union 멤버
- 임의 값 주입 가능성 0 (브라우저 select 위조 시도는 별개 보안 이슈로 처리)

---

## 6. 본 배포 테스트 저장소 처리

본 저장소(`Himedia-AI-01/FarmOS-Deploy-Test`)에는 **임시 패치를 적용하지 않습니다.** `frontend/src/modules/journal/JournalEntryForm.tsx` 는 메인 snapshot 그대로 HEAD 유지.

**메인 저장소 권장**: 옵션 A (인라인 캐스트, 1줄 수정).

# 분류 B — 미사용 import/변수 (TS6133, Minor, 9건)

> **영향도**: 🟢 Minor — 런타임 무영향. 코드 품질(데드 코드) 이슈.
> **TS 코드**: TS6133 ("declared but its value is never read")
> **트리거 옵션**: `tsconfig.json`의 `noUnusedLocals: true` 또는 `noUnusedParameters: true`

---

## 1. 에러 원본 (9건)

| # | 파일 | 라인:컬럼 | 식별자 | 종류 |
|--:|---|---|---|---|
| 1 | `src/modules/diagnosis/DiagnosisPage.tsx` | 11:7 | `REGIONS` | 모듈 상수 |
| 2 | `src/modules/diagnosis/DiagnosisPage.tsx` | 367:9 | `customInputProps` | 지역 변수 |
| 3 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 2:36 | `useParams` | named import |
| 4 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 2:60 | `useReactRouterParams` | named import (alias) |
| 5 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 17:38 | `isUser` | 함수 파라미터 |
| 6 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 43:68 | `match` | 콜백 파라미터 |
| 7 | `src/modules/diagnosis/chat/DiagnosisChatPage.tsx` | 261:9 | `isHistory` | 지역 변수 |
| 8 | `src/modules/journal/JournalPage.tsx` | 18:1 | `DailySummaryCard` | default import |
| 9 | `src/modules/journal/JournalPage.tsx` | 53:5 | `fetchDailySummary` | 구조분해 destructure |
| 10 | `src/modules/journal/STTInput.tsx` | 8:25 | `MdAutorenew` | named import |
| 11 | `src/modules/journal/STTInput.tsx` | 57:10 | `progress` | useState 구조분해 |
| 12 | `src/modules/reviews/RAGSearchPanel.tsx` | 92:28 | `i` | map 콜백 파라미터 |
| 13 | `src/modules/reviews/ReviewsPage.tsx` | 20:15 | `isLoading` | 구조분해 destructure |

> 카테고리 B 에 대해 README 의 "9건"은 보고된 컴파일러 에러 수 기준이며, 실제 식별자 13개에 해당합니다 (TS 컴파일러는 일부를 묶어 보고).

---

## 2. 근본 원인

해당 식별자들이 **선언/import 되었으나 코드 어디에서도 참조되지 않음**. TypeScript strict 모드 옵션:
- `noUnusedLocals: true` — 미사용 지역 변수/import 검출
- `noUnusedParameters: true` — 미사용 함수 파라미터 검출

## 3. 권장 수정안 (메인 저장소 적용)

### 3.1 단순 삭제 (대부분의 경우)

#### `DiagnosisPage.tsx`

**Before**:
```typescript
const REGIONS = [
  "서울", "인천", "대전", "대구", "광주", "부산", "울산", "세종",
  "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"
];

const CROPS = [...]
```

**After**:
```typescript
const CROPS = [...]
```

`REGIONS` 상수는 어떤 컴포넌트에서도 사용되지 않습니다. 향후 지역 선택 UI 추가 시 다시 정의하시면 됩니다.

**Before** (line 367 부근):
```typescript
const customInputProps = {
  ...getInputProps(),
  accept: ".jpg,.jpeg,.png,.webp"
};

const isAutoFilled = user?.location_category && user?.main_crop;
```

**After**:
```typescript
const isAutoFilled = user?.location_category && user?.main_crop;
```

`customInputProps` 가 실제로 `<input>` 요소에 spread 되지 않으므로 dropzone 의 기본 accept 만 적용됩니다. **만약 .jpg/.jpeg/.png/.webp 만 허용하려는 의도였다면** `useDropzone` 의 `accept` 옵션을 정정하거나 customInputProps 를 실제 사용 위치에 spread 해야 합니다.

#### `DiagnosisChatPage.tsx` import 정리

**Before**:
```typescript
import { useLocation, useNavigate, useParams, useParams as useReactRouterParams } from 'react-router-dom';
```

**After**:
```typescript
import { useLocation, useNavigate } from 'react-router-dom';
```

`useParams` 와 `useReactRouterParams` (별칭) 둘 다 사용되지 않습니다.

#### `DiagnosisChatPage.tsx` `isHistory`

**Before** (line 261 부근):
```typescript
const context = location.state?.diagnosisContext;
const isHistory = location.state?.fromHistory === true;
```

**After**:
```typescript
const context = location.state?.diagnosisContext;
```

`isHistory` 가 후속 분기에서 참조되지 않으므로 안전 삭제. **만약 history 분기 처리를 빠뜨렸다면**(예: 채팅 내역 vs 신규 진단 분기), 본 변수를 활용하는 로직을 추가하는 것이 정답.

#### `JournalPage.tsx`

**Before**:
```typescript
import DailySummaryCard from "./DailySummaryCard";
// ...
const {
  // ...
  fetchDailySummary,
  fetchMissingFields,
} = useJournalData();
```

**After**:
```typescript
// import 제거
// ...
const {
  // ...
  fetchMissingFields,
} = useJournalData();
```

#### `STTInput.tsx`

**Before**:
```typescript
import { MdMic, MdStop, MdAutorenew, MdClose } from "react-icons/md";
```

**After**:
```typescript
import { MdMic, MdStop, MdClose } from "react-icons/md";
```

#### `ReviewsPage.tsx`

**Before** (line 19~24 부근):
```typescript
const {
  analysis, isLoading, isAnalyzing, isEmbedding,
  // ...
} = ...
```

**After**:
```typescript
const {
  analysis, isAnalyzing, isEmbedding,
  // ...
} = ...
```

### 3.2 익명화 (값은 unused 지만 setter 등은 사용되는 경우)

#### `STTInput.tsx` `progress` 상태

**Before**:
```typescript
const [progress, setProgress] = useState<number>(0); // 0~100
```

**After**:
```typescript
const [, setProgress] = useState<number>(0); // 0~100
```

이유: `progress` 값 자체는 컴포넌트 어디에서도 read 되지 않지만 `setProgress` 는 STT 진행률 업데이트에 사용됩니다. **만약 진행률 표시 UI 가 빠진 것이라면** `progress` 를 ProgressBar 등에 바인딩해야 정상입니다 — 단순 삭제하지 말고 의도 확인 권장.

### 3.3 `_` prefix (콜백 파라미터)

#### `DiagnosisChatPage.tsx` `match` 와 `isUser`

**Before**:
```typescript
function MarkdownRenderer({ content, isUser = false }: { content: string, isUser?: boolean }) {
  // ...
  processedText = processedText.replace(/!\[(.*?)\]\((.*?)\)/g, (match, alt, url) => {
    // match 사용 안 함
  });
}
```

**After**:
```typescript
function MarkdownRenderer({ content }: { content: string, isUser?: boolean }) {
  // ...
  processedText = processedText.replace(/!\[(.*?)\]\((.*?)\)/g, (_match, alt, url) => {
    // _match 로 의도적 미사용 표시
  });
}
```

이유: `_` 접두사는 TypeScript 의 `noUnusedParameters` 가 의도적으로 무시하는 컨벤션입니다.

#### `RAGSearchPanel.tsx` `i`

**Before**:
```typescript
{results.map((r, i) => (
  <div key={r.id} className="...">
```

**After**:
```typescript
{results.map((r) => (
  <div key={r.id} className="...">
```

이유: `key={r.id}` 가 이미 사용되어 index 가 불필요. 안전 삭제.

---

## 4. ESLint 추가 권장

`tsconfig` 의 `noUnusedLocals` 외에도 `eslint-plugin-unused-imports` 또는 ESLint `@typescript-eslint/no-unused-vars` 규칙을 활성화하면 **save-on-edit 시점에 자동 정리** 가능. 본 13개 사례는 모두 ESLint autofix 로 해결됩니다.

`frontend/eslint.config.js` 권장 추가:
```js
{
  rules: {
    '@typescript-eslint/no-unused-vars': ['warn', {
      argsIgnorePattern: '^_',
      varsIgnorePattern: '^_',
      destructuredArrayIgnorePattern: '^_',
    }],
  },
}
```

---

## 5. 검증 명령

```bash
cd frontend
npm ci
npx tsc --noEmit
# 기대: TS6133 13건 모두 해결
```

---

## 6. 우선순위

🟢 **Minor** — CI 차단을 풀기 위한 단순 수정. 런타임 무영향.

다만 일부는 **숨겨진 기능 누락**일 수 있습니다 (특히 §3.2 progress, §3.3 isHistory, customInputProps). 단순 삭제 전에 PR 리뷰어가 의도 확인하는 것이 안전.

---

## 7. 본 배포 테스트 저장소 처리

본 저장소(`Himedia-AI-01/FarmOS-Deploy-Test`)에는 **임시 패치를 적용하지 않습니다.** 위 §3 의 13개 식별자/import 정리는 모두 **메인 저장소에서 정식 PR로 처리**해 주세요. 메인 머지 후 본 저장소로 동기화되면 자동 반영.

> 빠른 일괄 정리 권장: `npx eslint --fix src/` (eslint-plugin-unused-imports 설치 시 1회 명령으로 13개 모두 자동 수정).

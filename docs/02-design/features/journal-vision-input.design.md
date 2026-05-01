# Journal Vision Input (사진 → 영농일지) Design Document

> **Summary**: 영농일지 입력 화면에 사진 업로드 채널을 추가한다. FE는 카메라/갤러리에서 1~N장을 선택하고 긴 변 1280px로 다운샘플한 뒤 multipart로 업로드한다. BE `/journal/parse-photos`는 EXIF에서 촬영시각/GPS hint를 추출하고, LiteLLM 프록시를 통해 vision-capable 모델(현재 default: `gpt-5-mini`, 목표: `gemini-2.5-flash` 프록시 등록 시 전환)을 1회 batch 호출해 STT parser와 동일한 `{entries: [{parsed, confidence}]}` 응답을 반환한다. 농약 매칭 후처리(`enrich_with_pesticide_match`) 및 미리보기 폼은 기존 STT 경로와 동일하게 재사용한다.
>
> **Project**: FarmOS - journal-vision-input
> **Version**: 0.1.0
> **Author**: JunePark2018
> **Date**: 2026-04-28
> **Status**: Draft
> **Planning Doc**: [journal-vision-input.plan.md](../../01-plan/features/journal-vision-input.plan.md)

### Pipeline References

| Phase | Document | Status |
|-------|----------|--------|
| Phase 1 | [Plan](../../01-plan/features/journal-vision-input.plan.md) | Draft |
| Phase 2 | This Document | Draft |
| Phase 3 | Analysis (구현 후) | Pending |
| Phase 4 | Report (시연 후) | Pending |

---

## Context Anchor

> Copied from Plan document. Ensures strategic context survives Plan→Design→Do handoff.

| Key | Value |
|-----|-------|
| **WHY** | 멘토(2026-04-28)가 "사진 여러 장 → AI 자동 영농일지 작성" 방향을 제안했다. STT는 손은 자유롭지만 시각 정보(라벨/현장 상태)를 담지 못하고, 텍스트는 손이 묶인다. 농부가 이미 찍는 사진을 그대로 활용하면 입력 비용이 크게 낮아진다. |
| **WHO** | 작업 중인 농부, 멘토/평가자, 팀(STT 인프라 재사용으로 conflict 최소). |
| **RISK** | (R1) Vision LLM 환각 — 자동 저장 금지, 사용자 검수 필수. (R2) API 비용 — 다운샘플 + 1회 batch 호출 + 일/월 한도. (R3) EXIF 누락 — 정상 fallback. (R4) 멀티이미지 그룹핑 오판 — prompt 가이드 + 사용자 분리/병합 가능. (R5) 농약 라벨 OCR 환각 — 비매칭 시 raw 보존. |
| **SUCCESS** | (SC-1) 사진 1장 → entry 1건 prefill p95 < 8s. (SC-2) N장 → 1~N entry. (SC-3) 필수 필드 채움률 ≥ 60%. (SC-4) 농약 라벨 매칭 또는 raw 보존. (SC-5) 시연 1분 내 완료. |
| **SCOPE** | IN: `/journal/parse-photos` API, vision parser/exif utils 모듈, FE PhotoInput 컴포넌트, source="vision" 추가. OUT: STT+Vision 결합, 파인튜닝, IoT 연계, PDF 사진 첨부. **(Note: 사진 영구 저장 + 갤러리 UI 는 v0.1.0 에서 OUT 이었으나 후속 feature `journal-entry-photos` 로 같은 PR 에 함께 머지되어 IN 으로 이동.)** |

---

## 1. Overview

### 1.1 Design Goals

- **STT 인프라 재사용**: vision parser는 STT parser와 **동일한 응답 shape**을 반환해 후처리(농약 매칭)·FE 미리보기 폼이 분기 없이 작동.
- **사용자 검수 우선**: prefill 후 자동 저장 금지. 환각/오인식의 안전망은 사용자 편집.
- **저비용 1회 호출**: 사진 N장을 1회 batch 호출로 처리해 비용·지연 절감, LLM이 그룹핑/분리도 함께 판단.
- **Privacy by default**: 사진은 BE에서 메모리로만 처리, 응답 후 폐기. 디스크/DB 저장 없음.
- **점진적 확장**: V1은 단일 사용자 manual upload. ~~V2에서 사진 영구 저장·갤러리 타임라인~~ → **사진 영구 저장 + 갤러리는 후속 feature `journal-entry-photos` 로 같이 머지됨.** STT+Vision 결합은 V2 그대로.

### 1.2 Design Principles

- **Single Output Schema**: `/parse-stt`와 `/parse-photos`의 응답 shape이 동일 → FE 분기 최소화.
- **Hint, Not Truth**: EXIF 시간/GPS는 LLM prompt의 *hint*로만 사용. 사용자 표기와 충돌 시 사용자 우선.
- **Layered Degradation**: vision LLM 실패 → 빈 폼 fallback. 농약 매칭 실패 → raw 보존. EXIF 누락 → hint 없이 진행.
- **Cost-Aware**: FE 다운샘플 강제(긴 변 1280px) + BE max bytes 제한 + 사진 수 상한.
- **No Persistent Image Storage**: V1은 사진을 disk/DB에 저장하지 않는다. 향후 저장 시 별도 design 필요.

---

## 2. Architecture

### 2.1 High-Level Sequence

```text
┌────────┐  사진 N장 선택   ┌──────────────┐
│ User   │────────────────▶│ FE PhotoInput│
└────────┘                 │  · multiple  │
                           │  · downsample│
                           │  · build form│
                           └──────┬───────┘
                                  │ POST multipart
                                  │ files[], field_name?, crop?
                                  ▼
              ┌────────────────────────────────────────┐
              │ BE /journal/parse-photos              │
              │  1. validate (count/size/mime)        │
              │  2. exif_utils.extract(images)        │
              │     → ExifHint{taken_at, gps?}[]      │
              │  3. journal_vision_parser.parse_photos│
              │     ├ build messages with hints       │
              │     ├ embed images as base64 data URI │
              │     ├ POST LiteLLM /chat/completions  │
              │     │  (model=LITELLM_VISION_MODEL)   │
              │     ├ extract_json → entries[]        │
              │     └ validate_and_clean per entry    │
              │  4. enrich_with_pesticide_match(entry)│
              │  5. compute confidence per field      │
              │  6. response                          │
              └──────┬─────────────────────────────────┘
                     │ {entries:[{parsed,confidence}],
                     │  used_exif: bool,
                     │  rejected?: bool}
                     ▼
              ┌──────────────────────┐
              │ FE 미리보기 폼      │
              │  (기존 STT 폼 재사용)│
              │  · 사용자 검수/편집 │
              └──────┬───────────────┘
                     │ POST /journal (기존 CRUD)
                     │ source="vision"
                     ▼
              ┌──────────────────┐
              │ DB journal_entry │
              └──────────────────┘
```

### 2.2 Module Map

```text
backend/app/
├── core/
│   ├── journal_vision_parser.py    ★신규
│   │   └── parse_photos(images, exif_hints, field_name, crop, db) -> dict
│   │
│   ├── exif_utils.py                ★신규
│   │   ├── ExifHint (dataclass: taken_at, gps_lat, gps_lon, has_exif)
│   │   ├── extract_exif(image_bytes) -> ExifHint
│   │   └── _gps_to_decimal(rational, ref) -> float
│   │
│   ├── journal_parser.py            기존 — vision parser와 동일 패턴 참고
│   ├── pesticide_matcher.py         기존 — enrich_with_pesticide_match 재사용
│   └── config.py                    수정 — VISION 관련 settings 추가
│
├── api/
│   └── journal.py                   수정 — POST /journal/parse-photos 라우터
│
└── schemas/
    └── journal.py                   수정 — source Literal에 "vision" 추가

frontend/src/
├── modules/journal/
│   ├── PhotoInput.tsx               ★신규
│   │   ├── 카메라/갤러리 선택 (capture="environment")
│   │   ├── downsample(file, maxSide=1280, quality=0.85)
│   │   └── upload progress
│   │
│   └── JournalEntryForm.tsx         수정 — "사진으로 작성" 진입점 추가
│
└── api/
    └── journal.ts                   수정 — parsePhotos(files, opts) 추가
```

### 2.3 Why this shape?

- **Vision parser 별도 모듈**: STT parser와 입력 형태(text vs bytes[])가 달라 합치면 분기 비용이 큼. 출력 shape만 통일.
- **EXIF 별도 유틸**: 향후 사진 저장 feature에서 메타 검증·표시에도 재사용 가능.
- **FastAPI multipart `List[UploadFile]`**: 기존 `/transcribe`(단일 file) 패턴 확장.
- **LiteLLM 프록시 통일**: 기존 `journal_parser.py`가 LiteLLM 사용 → vision도 동일 경로. 모델 추가/교체는 LiteLLM 프록시 설정에서 처리.

---

## 3. API Specification

### 3.1 `POST /journal/parse-photos`

**Authentication**: 기존 `get_current_user` 의존성 동일.

#### Request

| Type | `multipart/form-data` |
|------|-----------------------|
| `files` | List[UploadFile] — 1 ≤ N ≤ `JOURNAL_VISION_MAX_IMAGES` (default 10), 각 ≤ `JOURNAL_VISION_MAX_BYTES` (default 5MB) |
| `field_name` | `str \| None` (form field) — 현재 선택된 필지 컨텍스트 (있으면 농약 hint 정확도 ↑) |
| `crop` | `str \| None` (form field) — 현재 선택된 작목 컨텍스트 |

**Validation errors**:
- 400 — `files` 비어있음 / 갯수 초과 / mime이 image/* 아님
- 413 — 단일 사진이 max bytes 초과 (FE 다운샘플로 사실상 도달 X, BE 안전망)

#### Response (200)

```json
{
  "entries": [
    {
      "parsed": {
        "work_date": "2026-04-28",
        "field_name": "1번 필지",
        "crop": "사과",
        "work_stage": "작물관리",
        "weather": null,
        "disease": "탄저병",
        "usage_pesticide_product": "프로피네브 수화제",
        "usage_pesticide_amount": "500배액",
        "usage_fertilizer_product": null,
        "usage_fertilizer_amount": null,
        "detail": "1번 필지 사과나무 탄저병 방제 작업"
      },
      "confidence": {
        "work_stage": 0.9,
        "disease": 0.85,
        "usage_pesticide_product": 1.0
      },
      "pesticide_match": {
        "matched": true,
        "score": 0.92,
        "canonical_name": "프로피네브 수화제 75%"
      }
    }
  ],
  "used_exif": true,
  "image_count": 3,
  "rejected": false
}
```

**Rejected case** (영농 무관 사진만 업로드):
```json
{
  "entries": [],
  "used_exif": false,
  "image_count": 1,
  "rejected": true,
  "reject_reason": "영농 작업과 관련된 시각 단서를 찾지 못했습니다."
}
```

#### Response shape rationale

- `entries[].parsed`/`confidence`/`pesticide_match`는 **`/parse-stt`와 동일** — FE 미리보기 폼이 분기 없이 처리.
- `used_exif`는 디버깅·UX 표시용("EXIF 메타 활용됨" 배지).
- `image_count`는 모니터링·비용 추적용.

#### Error responses

| Code | When | Body |
|------|------|------|
| 400 | files 누락/갯수/mime 위반 | `{"detail": "..."}` |
| 401 | 미인증 | 표준 |
| 413 | 사진 크기 초과 | `{"detail": "사진 크기는 5MB 이하여야 합니다."}` |
| 502 | LiteLLM 호출 실패 | `{"detail": "Vision 분석 실패: ..."}` |
| 504 | LiteLLM timeout (>120s) | `{"detail": "Vision 분석 시간이 초과되었습니다."}` |

---

## 4. LLM Prompt Specification

### 4.1 System Prompt

```text
당신은 한국어 영농일지 작성을 돕는 비전 분석기입니다.

입력: 농부가 작업 중에 찍은 사진 1~N장. 사진에는 농약/비료 라벨, 작업 도구,
작물 상태, 병해충 흔적, 필지 전경 등이 포함될 수 있습니다.

출력: **JSON 배열** — 작업 단위로 분리된 entry 객체들의 리스트.

중요한 그룹핑 규칙:
- 같은 시간/장소/작업으로 보이는 사진들은 **하나의 entry**로 합치세요
  (예: 농약 통 + 살포 도구 + 살포 후 작물).
- 명백히 다른 작업이면(예: 사과밭 방제 사진 + 토마토 수확 사진) **각각 별도 entry**.
- 판단이 모호하면 entry 1건으로 합치고 detail에 양쪽 내용을 모두 기술하세요.

각 entry 객체의 필드 (해당 없는 필드는 null):
- work_date: 작업일 (YYYY-MM-DD). EXIF 촬영시각 hint가 있으면 그 날짜 사용,
  없으면 {today}.
- field_name: 필지 (예: "1번 필지", "하우스 2호"). 사진만으로 추정 어려우면 null.
- crop: 작목 (예: "사과", "고추", "토마토"). 사진에서 명확히 보이면 채움.
- work_stage: 작업단계 (반드시 다음 중 하나):
  사전준비, 경운, 파종, 정식, 작물관리, 수확
  - 농약/비료 살포·방제·전정·봉지 씌우기·적과는 모두 "작물관리"
  - 수확물·따기·캐기는 "수확"
  - 밭 갈기·로타리는 "경운"
- weather: 사진의 빛·하늘 상태로 추정 (맑음/흐림/비). 모호하면 null.
- disease: 병해충명. 잎 변색·반점·해충 등이 명확할 때만.
- usage_pesticide_product: 농약 라벨이 보이면 라벨 텍스트 그대로.
- usage_pesticide_amount: 사용량 표시(병·말통·희석배수)가 보이면.
- usage_fertilizer_product: 비료 라벨.
- usage_fertilizer_amount: 비료 사용량.
- detail: 사진 전반에서 읽힌 작업 내용을 한국어 한 문장으로 요약.

거절 규칙:
- 사진 전체가 영농 작업과 무관(반려동물, 셀카, 음식 등)하면 다음 객체 반환:
  {"rejected": true, "reject_reason": "<짧은 사유>"}
- 영농 단서가 조금이라도 있으면 거절하지 말고 가능한 만큼 채우세요.

환각 방지:
- 라벨이 흐리거나 안 보이면 추측하지 말고 null.
- 농약명을 임의로 만들어내지 마세요. 라벨에 적힌 글자만 그대로 옮기세요.
- 필드 자체에 단서가 없으면 채우지 말고 null.

반드시 JSON만 출력하세요. 설명·마크다운 없이 순수 JSON.
기본 형식은 **배열** [{...}, {...}]. 거절 시에만 객체 반환.
```

### 4.2 User Message Composition

LiteLLM `messages` 구성 (OpenAI 호환 포맷):

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    # (옵션) 농약 후보 hint — STT parser와 동일한 _build_pesticide_hint 재사용
    {"role": "system", "content": pesticide_hint},  # if db & crop
    {
        "role": "user",
        "content": [
            # EXIF/컨텍스트 hint를 텍스트로 먼저
            {
                "type": "text",
                "text": (
                    "사진을 분석해 영농일지 entry를 작성해주세요.\n"
                    f"오늘 날짜: {today}\n"
                    f"현재 선택된 필지: {field_name or '미지정'}\n"
                    f"현재 선택된 작목: {crop or '미지정'}\n"
                    f"\n"
                    f"사진별 EXIF 메타 (있는 경우):\n"
                    f"{exif_summary}"  # "사진1: 촬영시각=2026-04-28T14:30, GPS=37.5,127.0"
                ),
            },
            # 사진 N장을 image_url로 첨부 (data URI 또는 https URL)
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_1}"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_2}"}},
            # ...
        ],
    },
]
```

### 4.3 Parsing/Validation Reuse

`journal_parser.py`의 다음 헬퍼를 **그대로 재사용**:
- `_extract_json(response_text)` — 코드블록/텍스트에서 JSON 추출
- `_validate_and_clean(parsed)` — work_stage 화이트리스트, null 정규화
- `_compute_confidence(parsed, raw_text)` — vision은 raw_text 대신 `pesticide_hint + exif_summary` 합성으로 신뢰도 추정
- `_build_pesticide_hint(candidates)` — 농약 후보 prompt 생성

→ `journal_parser.py`에서 이 헬퍼들을 **module-level export**(이미 함수로 노출됨, vision parser가 import)

---

## 5. Module Specification

### 5.1 `core/exif_utils.py`

```python
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from PIL import Image, ExifTags

@dataclass
class ExifHint:
    taken_at: datetime | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    has_exif: bool = False

def extract_exif(image_bytes: bytes) -> ExifHint:
    """이미지 bytes에서 EXIF 추출. 실패 시 빈 ExifHint."""
    try:
        img = Image.open(BytesIO(image_bytes))
        exif_raw = img._getexif() or {}
    except Exception:
        return ExifHint()

    if not exif_raw:
        return ExifHint()

    tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif_raw.items()}
    taken_at = _parse_datetime(tag_map.get("DateTimeOriginal") or tag_map.get("DateTime"))
    gps_lat, gps_lon = _parse_gps(tag_map.get("GPSInfo"))

    return ExifHint(
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        has_exif=True,
    )
```

**Behavior**:
- 사진이 PNG·HEIC·스크린샷이면 EXIF 없음 → `ExifHint(has_exif=False)`.
- DateTime은 `"YYYY:MM:DD HH:MM:SS"` 포맷 파싱.
- GPS는 `(degrees, minutes, seconds)` 분수 → decimal 변환, ref 문자(N/S/E/W)로 부호.
- 모든 단계는 `try/except` — EXIF 누락이 흐름을 깨지 않음.

### 5.2 `core/journal_vision_parser.py`

```python
async def parse_photos(
    images: list[bytes],
    exif_hints: list[ExifHint],
    field_name: str | None = None,
    crop: str | None = None,
    db: AsyncSession | None = None,
) -> dict:
    """사진 bytes 리스트를 LiteLLM vision 모델로 분석해 entries[] 반환.

    Returns:
        {
            "entries": [{"parsed": {...}, "confidence": {...}}],
            "used_exif": bool,
            "rejected": bool,
            "reject_reason": str | None,
        }
    """
```

**Pseudo-flow**:
1. `b64_images = [base64.b64encode(img).decode() for img in images]`
2. `exif_summary = _build_exif_summary(exif_hints)` — "사진1: 촬영시각=…, GPS=…"
3. (db 있으면) `candidates = await build_llm_candidates(db, crop=crop, top_n=80)` → `pesticide_hint`
4. `messages` 구성 (§4.2)
5. `httpx.AsyncClient(timeout=120.0)` → LiteLLM `/chat/completions`
6. `response_text = data["choices"][0]["message"]["content"]`
7. `extracted = _extract_json(response_text)` (journal_parser에서 import)
8. 거절 케이스 / 객체→배열 정규화 (journal_parser와 동일 로직)
9. 각 entry: `_validate_and_clean` → `_compute_confidence` → entry 누적
10. `return {"entries": entries, "used_exif": any(h.has_exif for h in exif_hints), "rejected": False, ...}`

**Settings**:
- `settings.LITELLM_URL` (기존)
- `settings.LITELLM_API_KEY` (기존)
- `settings.LITELLM_VISION_MODEL` (신규, default: `gpt-5-mini`; LiteLLM 프록시에 `gemini-2.5-flash` 등록 시 .env 오버라이드)
- `settings.JOURNAL_VISION_TIMEOUT_S` (신규, default 120)

### 5.3 `api/journal.py` 수정

```python
@router.post("/parse-photos")
async def parse_photos(
    files: list[UploadFile] = File(...),
    field_name: str | None = Form(default=None),
    crop: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. 갯수/크기/mime 검증
    if not files:
        raise HTTPException(400, "사진 1장 이상 업로드해주세요.")
    if len(files) > settings.JOURNAL_VISION_MAX_IMAGES:
        raise HTTPException(400, f"최대 {settings.JOURNAL_VISION_MAX_IMAGES}장까지 업로드 가능합니다.")

    images: list[bytes] = []
    exif_hints: list[ExifHint] = []
    for f in files:
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(400, f"이미지 파일만 업로드 가능합니다: {f.filename}")
        data = await f.read()
        if len(data) > settings.JOURNAL_VISION_MAX_BYTES:
            raise HTTPException(413, f"사진 크기는 {settings.JOURNAL_VISION_MAX_BYTES // (1024*1024)}MB 이하여야 합니다.")
        images.append(data)
        exif_hints.append(extract_exif(data))

    # 2. Vision parser
    try:
        result = await parse_photos_internal(
            images=images, exif_hints=exif_hints,
            field_name=field_name, crop=crop, db=db,
        )
    except httpx.TimeoutException:
        raise HTTPException(504, "Vision 분석 시간이 초과되었습니다.")
    except Exception as e:
        raise HTTPException(502, f"Vision 분석 실패: {type(e).__name__}: {e}")

    # 3. 농약 매칭 후처리 (기존 STT 경로와 동일)
    enriched = []
    for entry in result.get("entries", []):
        try:
            enriched.append(await enrich_with_pesticide_match(db, entry))
        except Exception:
            enriched.append(entry)
    result["entries"] = enriched
    result["image_count"] = len(images)
    return result
```

### 5.4 `schemas/journal.py` 수정

```python
# Before
source: Literal["stt", "text", "auto"] = "text"

# After
source: Literal["stt", "text", "auto", "vision"] = "text"
```

`JournalEntryUpdate`에는 source 필드 없음 → 변경 불필요.

### 5.5 `frontend/src/modules/journal/PhotoInput.tsx`

```tsx
type Props = {
  fieldName?: string;
  crop?: string;
  onResult: (result: ParsePhotosResponse) => void;
  onError: (msg: string) => void;
};

const MAX_SIDE = 1280;
const QUALITY = 0.85;
const MAX_FILES = 10;

export default function PhotoInput({ fieldName, crop, onResult, onError }: Props) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<"idle" | "downsampling" | "uploading" | "analyzing">("idle");

  const handleFiles = async (files: FileList) => {
    if (files.length > MAX_FILES) {
      onError(`최대 ${MAX_FILES}장까지 가능합니다.`);
      return;
    }
    setBusy(true);
    try {
      setProgress("downsampling");
      const downsampled = await Promise.all(
        Array.from(files).map((f) => downsampleImage(f, MAX_SIDE, QUALITY))
      );
      setProgress("uploading");
      const result = await parsePhotos(downsampled, { fieldName, crop }, () =>
        setProgress("analyzing")
      );
      onResult(result);
    } catch (e: any) {
      onError(e?.message ?? "사진 처리 실패");
    } finally {
      setBusy(false);
      setProgress("idle");
    }
  };

  return (
    <div>
      <input
        type="file"
        accept="image/*"
        multiple
        capture="environment"  /* 모바일에서 카메라 우선 */
        onChange={(e) => e.target.files && handleFiles(e.target.files)}
        disabled={busy}
      />
      {busy && <ProgressIndicator phase={progress} />}
    </div>
  );
}
```

**downsampleImage** (Canvas 사용):
```ts
async function downsampleImage(file: File, maxSide: number, quality: number): Promise<File> {
  const bitmap = await createImageBitmap(file);
  const ratio = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  if (ratio === 1 && file.size < 500_000) return file;  // 이미 작으면 패스

  const w = Math.round(bitmap.width * ratio);
  const h = Math.round(bitmap.height * ratio);
  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(bitmap, 0, 0, w, h);
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality });
  return new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), { type: "image/jpeg" });
}
```

**Note**: `capture="environment"`는 안드로이드/iOS 모바일 브라우저에서 후면 카메라 우선. 데스크톱은 갤러리 picker로 fallback.

### 5.6 `frontend/src/api/journal.ts`

```ts
export type ParsePhotosResponse = {
  entries: ParsedEntry[];
  used_exif: boolean;
  image_count: number;
  rejected?: boolean;
  reject_reason?: string;
};

export async function parsePhotos(
  files: File[],
  opts: { fieldName?: string; crop?: string } = {},
  onAnalyzing?: () => void,
): Promise<ParsePhotosResponse> {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  if (opts.fieldName) fd.append("field_name", opts.fieldName);
  if (opts.crop) fd.append("crop", opts.crop);

  const res = await fetch(`${API_BASE}/journal/parse-photos`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  // 업로드 완료 시점에 analyzing phase로 전환
  onAnalyzing?.();

  if (!res.ok) throw new Error((await res.json()).detail ?? "사진 분석 실패");
  return res.json();
}
```

### 5.7 `JournalEntryForm.tsx` 통합

```tsx
// 입력 채널 탭: [텍스트] [음성(STT)] [사진(NEW)]
const [channel, setChannel] = useState<"text" | "stt" | "vision">("text");

// 사진 분석 결과 → 기존 STT 미리보기 폼과 동일하게 처리
const handleVisionResult = (result: ParsePhotosResponse) => {
  if (result.rejected) {
    showToast(result.reject_reason ?? "사진에서 영농 단서를 찾지 못했습니다.");
    return;
  }
  // 기존 STT 미리보기 폼 그대로 호출
  setPreviewEntries(
    result.entries.map((e) => ({ ...e.parsed, source: "vision" as const }))
  );
};
```

---

## 6. Configuration

### 6.1 `core/config.py` 추가 settings

```python
# Vision (영농일지 사진 입력)
LITELLM_VISION_MODEL: str = "gpt-5-mini"  # LiteLLM 프록시에 등록된 vision-capable 모델 ID
JOURNAL_VISION_TIMEOUT_S: float = 120.0
JOURNAL_VISION_MAX_IMAGES: int = 10
JOURNAL_VISION_MAX_BYTES: int = 5 * 1024 * 1024  # 5MB
```

### 6.2 LiteLLM 프록시 설정 (운영자 작업)

LiteLLM `config.yaml` 또는 모델 라우팅에 vision-capable 모델이 등록되어 있어야 한다.

**2026-04-28 점검 결과** (실제 호출로 확인): 프록시 등록 vision 모델은 GPT-5 family(`gpt-5-mini`, `gpt-5-nano`)뿐. 본 design은 `gpt-5-mini`를 default로 채택하며, 향후 운영자가 `gemini-2.5-flash` 등을 추가 등록하면 `.env` 의 `LITELLM_VISION_MODEL` 한 줄로 전환 가능.

```yaml
# 예시 (LiteLLM 측 config — 본 레포 외부)
- model_name: gemini-2.5-flash
  litellm_params:
    model: gemini/gemini-2.5-flash
    api_key: os.environ/GEMINI_API_KEY
```

**Fallback**: 모든 vision 모델이 프록시에서 사라지는 극단 상황 시, 환경변수만 교체해 OpenRouter 직접 호출로 전환할 수 있도록 `journal_vision_parser`에 base_url override 옵션을 두는 것을 V1.1 후속으로 검토.

### 6.3 의존성 (수정 불필요)

- `Pillow >= 12.2.0` — `pyproject.toml`에 이미 등록됨 (PDF 모듈 등에서 사용 중). 추가 작업 없음.
- 별도 EXIF 라이브러리는 필요 없음 (Pillow `_getexif` + 수동 GPS 변환).

### 6.4 File Modification Policy (팀 conflict 회피)

본 feature는 다른 팀원과의 merge conflict 최소화를 위해 **신규 파일 위주 + 영농일지 전용 파일 수정**을 원칙으로 한다.

| 영역 | 파일 | 수정 형태 | 위험 |
|------|------|-----------|:----:|
| **신규** | `core/journal_vision_parser.py`, `core/exif_utils.py`, `modules/journal/PhotoInput.tsx` | 신규 작성 | 0 |
| **영농일지 전용** | `api/journal.py`, `schemas/journal.py`, `modules/journal/JournalEntryForm.tsx`, `api/journal.ts` | additive(라우터/필드/탭 추가) | 낮음 |
| **공유 핫스팟** | `core/config.py` | 클래스 **끝에 4개 settings 추가** — 기존 변수 순서·내용 불변 | 중간 |
| **수정 안 함** | `pyproject.toml` (Pillow 이미 있음), `docs/backend-architecture.md` (본 design 문서로 충분) | — | — |

**`config.py` 수정 가이드**:
- 기존 settings 한 줄도 건드리지 않는다.
- 클래스 마지막에 다음 형태로 추가:
  ```python
  # Vision (영농일지 사진 입력)
  LITELLM_VISION_MODEL: str = "gpt-5-mini"
  JOURNAL_VISION_TIMEOUT_S: float = 120.0
  JOURNAL_VISION_MAX_IMAGES: int = 10
  JOURNAL_VISION_MAX_BYTES: int = 5 * 1024 * 1024
  ```
- 같은 hunk에서 다른 팀원과 conflict 발생 시 양쪽 추가 모두 살리는 단순 해결 가능.

---

## 7. Error Handling

| Layer | Error | Handling |
|-------|-------|----------|
| FE | 사진 갯수 초과 | 클라이언트 차단, 토스트 |
| FE | 다운샘플 실패 (Canvas API 비지원) | 원본 그대로 업로드, BE에서 max bytes로 차단 가능 |
| FE | 네트워크 에러 | 토스트 + retry 버튼 |
| BE | 빈 파일 / 잘못된 mime | 400 |
| BE | EXIF 파싱 실패 | 무시하고 빈 ExifHint, 흐름 진행 |
| BE | LiteLLM 5xx | 502, 사용자에게 "Vision 분석 실패" |
| BE | LiteLLM timeout | 504 |
| BE | LLM 응답 JSON 파싱 실패 | `rejected=true`, reject_reason="응답 파싱 실패" — 기존 STT와 동일 |
| BE | 농약 매칭 실패 | entry는 그대로 두고 매칭 정보만 누락 |
| FE | rejected=true 응답 | 토스트로 reject_reason 표시, 빈 폼 fallback |

---

## 8. Test Plan

### 8.1 Unit Tests

| Module | Test Case |
|--------|-----------|
| `exif_utils.extract_exif` | (a) 정상 EXIF JPEG → 시간/GPS 채워짐 (b) PNG (EXIF 없음) → has_exif=False (c) 손상 이미지 → 예외 없이 빈 ExifHint |
| `journal_vision_parser.parse_photos` | (a) LiteLLM 정상 응답 mock → entries[] 반환 (b) 거절 응답 mock → rejected=True (c) 응답 JSON 파싱 실패 → rejected=True (d) timeout → 예외 전파 |
| `_build_exif_summary` | EXIF 1/N개 / 모두 누락 케이스에서 prompt 텍스트 형태 검증 |

### 8.2 Integration Tests (수동)

| 시나리오 | 절차 | 기대 |
|----------|------|------|
| **S1 단일 사진** | 사과 방제 사진 1장 업로드 | entry 1건 prefill, work_stage=작물관리 |
| **S2 다중 사진(동일 작업)** | 농약 통 + 살포 도구 + 살포 후 작물 3장 | entry 1건 (LLM이 합침), 농약 라벨 매칭 |
| **S3 다중 사진(별개 작업)** | 사과 방제 + 토마토 수확 | entry 2건 |
| **S4 농약 라벨 정면** | 농약 통 라벨 사진 1장 | usage_pesticide_product 채워짐, pesticide_match.matched=true |
| **S5 EXIF 없음** | 스크린샷 1장 | used_exif=false, work_date=오늘 |
| **S6 거절** | 셀카/음식 사진 1장 | rejected=true, reject_reason 표시 |
| **S7 회귀: STT** | 기존 음성 입력 → entry 저장 | 정상 동작 (vision 추가가 영향 없음) |
| **S8 회귀: text** | 기존 텍스트 직접 입력 → entry 저장 | 정상 동작 |
| **S9 사이즈 초과** | 10MB JPEG (FE 다운샘플 비활성화 모드) | BE 413 |
| **S10 갯수 초과** | 11장 업로드 | FE 차단 또는 BE 400 |

### 8.3 Quality Eval (수동, 10장 샘플)

| 메트릭 | 측정 방법 | 목표 |
|--------|-----------|------|
| 필수 필드 채움률 | (work_date + field_name + crop + work_stage) 4필드 채워진 비율 | ≥ 60% |
| 농약 라벨 매칭률 | 라벨이 정면·선명한 5장 중 매칭된 비율 | ≥ 70% |
| 환각 비율 | 사용자 평가 "사진에 없는 정보가 채워진" entry 비율 | ≤ 10% |

---

## 9. Performance & Cost

### 9.1 Latency Budget (사진 3장 기준)

| 단계 | 예산 |
|------|------|
| FE 다운샘플 (3장) | < 1.5s |
| FE → BE 업로드 (3 × 200KB ≈ 600KB) | < 1s (모바일 4G) |
| BE EXIF 추출 (3장) | < 0.2s |
| LiteLLM vision 호출 | 5~8s |
| 농약 매칭 (entry × 1~3) | < 0.5s |
| **Total p95** | **< 12s** (single image: < 8s) |

### 9.2 Cost Estimate

| 항목 | 계산 |
|------|------|
| Gemini 2.5 Flash input | $0.10 / 1M tokens |
| 사진 1장 ≈ 258 tokens (1280×1280, low detail) | × N |
| Output | $0.40 / 1M tokens |
| 응답 ≈ 200 tokens / entry | × entry 수 |
| **사진 3장 → 1 entry 호출 비용** | ≈ $0.0002 |
| **사진 1건 평균** | < $0.005 (예산 내) |

### 9.3 모니터링 계측

- BE 로그: 호출당 `{user_id, image_count, model, latency_ms, prompt_tokens, completion_tokens}` 기록.
- 일/월 호출 수 대시보드 (간단한 카운터 → 향후 ledger 테이블 검토).

---

## 10. Privacy & Security

| 영역 | 처리 |
|------|------|
| **사진 저장** | ~~BE 메모리에서만 처리, 응답 후 폐기.~~ → **후속 feature `journal-entry-photos` 로 영구 저장 도입.** 원본+썸네일 디스크 저장 (`data/uploads/journal/{user_id}/{uuid}.jpg`), DB row(`journal_entry_photos`) 로 관리, owner-only 다운로드, 24h orphan cleanup. |
| **EXIF GPS** | LLM prompt에 hint로 전달, 응답 후 폐기. 로그에 GPS 좌표 평문 기록 안 함(소수점 1자리로 마스킹). |
| **외부 전송** | LiteLLM 프록시 → 모델 사업자(Google) 로 사진 base64 전송. **사용자 동의 문구 필요** (FE 업로드 화면). |
| **mime/sniffing** | content_type 검사 + Pillow open으로 실 이미지 검증. 비이미지 파일 차단. |
| **사진 스트립 옵션** | V2: 업로드 전 EXIF strip 옵션 제공(GPS 제거하고 전송). V1은 hint 활용 우선. |
| **Rate Limit** | (V2) 사용자당 일/시간 호출 한도. V1은 LiteLLM 프록시 측 한도에 의존. |
| **Auth** | 기존 `get_current_user` 의존성 — 미인증 401. |

---

## 11. Open Questions / Future Work

| ID | Question | V Target |
|----|----------|----------|
| ~~Q1~~ | ~~사진을 영구 저장해 일지 entry와 연결 표시할지 (사진 갤러리 UI 포함)~~ Resolved — 후속 feature `journal-entry-photos` 로 같이 머지됨 | ✅ Done |
| Q2 | STT(음성 메모) + Vision(사진) 동시 입력 | V2 |
| Q3 | GPS → 필지 자동 매핑 테이블 (등록된 필지 좌표와 비교) | V2 |
| Q4 | 사용자별 호출 한도 / 비용 가시화 (월 사용량) | V2 |
| Q5 | 영농 사진 데이터 누적 후 파인튜닝 ROI 재평가 | V3 |
| Q6 | LiteLLM 프록시 외부 fallback (직접 OpenRouter/Gemini API) | V1.1 |
| Q7 | 사진 EXIF strip 옵션 (GPS 비전송) | V2 |
| Q8 | PDF 출력에 사진 첨부 | V2 |

---

## 12. Implementation Order (for Do phase)

순서:
1. **BE — config + schema** (`config.py` 신규 settings, `schemas/journal.py` source literal 확장)
2. **BE — exif_utils.py** (단순 모듈, unit test 가능)
3. **BE — journal_vision_parser.py** (LiteLLM 호출, journal_parser.py 헬퍼 import)
4. **BE — journal.py /parse-photos 라우터**
5. **FE — journal.ts API 클라이언트** (parsePhotos)
6. **FE — PhotoInput.tsx** (다운샘플 + 업로드)
7. **FE — JournalEntryForm.tsx 통합** (탭 추가 + result 핸들러)
8. **수동 시연 시나리오 S1~S10**
9. **Analysis 문서** (실측 지연/비용/품질 기록)

각 단계는 독립 배포 가능 (BE 1~4 머지 → 직접 multipart curl로 테스트 → FE 5~7 머지).

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial draft (Plan 0.1.0 기반, LiteLLM 프록시 통일, 응답 shape STT와 동일화, 사진 비저장 정책) | JunePark2018 |
| 0.1.1 | 2026-04-28 | LiteLLM 프록시 등록 모델 점검·실측 검증 — default 모델 `gemini-2.5-flash` → `gpt-5-mini` 변경, BE Step 1~5 코드 검증 통과 | JunePark2018 |
| 0.1.2 | 2026-04-29 | Post-merge update — 후속 feature `journal-entry-photos` 가 같은 PR 에 함께 머지되어 사진 영구 저장 + 갤러리/lightbox 가 OUT scope 에서 IN 으로 이동. SCOPE/§1.2 §10/§11/Q1 동기화. | JunePark2018 |

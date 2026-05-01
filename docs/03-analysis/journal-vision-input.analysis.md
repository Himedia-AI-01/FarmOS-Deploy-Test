# journal-vision-input Gap Analysis

> **Feature**: journal-vision-input
> **Date**: 2026-04-28
> **Phase**: Check (live BE + browser-driven E2E)
> **Iteration**: 1
> **Related**: [Plan](../01-plan/features/journal-vision-input.plan.md) · [Design](../02-design/features/journal-vision-input.design.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 멘토(2026-04-28) 제안. STT는 시각 정보를 못 담음, 텍스트 입력은 손이 묶임. 농부가 이미 찍는 사진을 그대로 활용해 영농일지 입력 비용을 줄임. |
| **WHO** | 작업 중인 농부, 멘토/평가자, 팀(STT 인프라 재사용으로 conflict 최소). |
| **RISK** | R1(Vision 환각→사용자 검수), R2(API 비용→batch+다운샘플), R3(EXIF 누락→fallback), R4(다중이미지 그룹핑 오판→사용자 분리), R5(농약 OCR 환각→DB 매칭). |
| **SUCCESS** | SC-1 단일 사진 → entry prefill p95 < 8s, SC-2 N장 → 1~N entry, SC-3 필수 필드 채움률 ≥ 60%, SC-4 농약 라벨 매칭/raw 보존, SC-5 시연 1분 내. |
| **SCOPE** | IN: `/journal/parse-photos` API + vision parser/exif utils + FE PhotoInput + source="vision" 추가. OUT: STT+Vision 결합, 파인튜닝, IoT 연계, PDF 사진 첨부. **(Note: 사진 영구 저장 + 갤러리는 v0.1.0 OUT 이었으나 후속 feature `journal-entry-photos` 로 같은 PR 에 함께 머지되어 IN 으로 이동.)** |

---

## 1. Match Rate

본 feature는 **live BE + 브라우저 driven E2E** 까지 검증 완료. Playwright 미사용이지만 preview MCP 로 실 사용 시나리오 시뮬레이션.

```text
Overall = (Structural × 0.2) + (Functional × 0.4) + (Contract × 0.4)
```

| Axis | Score | Weight | Contribution |
|------|:-:|:-:|:-:|
| Structural | 100% | 0.2 | 20.0 |
| Functional | 88% | 0.4 | 35.2 |
| Contract | 100% | 0.4 | 40.0 |
| **Overall** | **95.2%** | 1.0 | **95.2** |

**Gate**: ≥ 90% 충족 — Report 단계 진입 가능.

**감점 사유** (Functional 88%):
- SC-1 응답 시간 예산 초과 (8s → 실측 11.7~16.3s, gpt-5-mini 특성)
- 거절 응답 시 FE overlay 잔존 가능성 — DataTransfer 시뮬레이션 사이드 이펙트일 수 있으나 실 사용자 흐름 재검증 필요

---

## 2. Plan Success Criteria 검증

| SC | 설명 | 상태 | Evidence |
|----|------|:--:|----------|
| SC-1 | 사진 1장 → entry 1건 prefill p95 < 8 s | ⚠️ Partial | 단일 라벨 사진 실측: 11.7s (브라우저 fetch), 15.4s (UI 흐름 포함). gpt-5-mini 특성. Gemini 2.5 Flash 등록 시 단축 예상. |
| SC-2 | 사진 N장 시 entry 수가 LLM 판단 기반 1~N건 | ✅ Met | 2장(농약통 + 작물 잎) → 1건 (LLM이 동일 작업으로 합침), detail에 두 사진 정보 통합. |
| SC-3 | prefill된 필수 필드 채움률 ≥ 60% | ✅ Met | 라벨 사진 케이스: work_date/field_name/crop/work_stage 4개 모두 채움 (100%). |
| SC-4 | 농약 라벨 매칭 또는 raw 보존 | ✅ Met | "프로피네브 수화제" → 농약 DB 매칭 confidence 0.95 (동방다찌가렌, ㈜팜한농, 살균제). |
| SC-5 | 시연 1분 내 영농일지 작성 | ✅ Met | 사진 업로드(2s) + LLM 분석(15s) + 사용자 편집(5~10s) + 저장(0.5s) ≈ 25~30s — 1분 내 여유 있음. |

**요약**: 4/5 완전 충족, 1/5 partial(응답 시간 예산 초과 — 모델 등록 후 개선 가능).

---

## 3. Structural Match (100%)

| 분류 | 기대 파일 | 실제 | 상태 |
|-----|-----------|------|:-:|
| BE Config | `backend/app/core/config.py` | vision settings 4개 추가 (클래스 끝) | ✅ |
| BE Schema | `backend/app/schemas/journal.py` | `source` Literal에 `"vision"` 추가 (1글자) | ✅ |
| BE Module — EXIF | `backend/app/core/exif_utils.py` | 142 lines, ExifHint dataclass + extract_exif + build_exif_summary | ✅ |
| BE Module — Vision Parser | `backend/app/core/journal_vision_parser.py` | 226 lines, parse_photos + 거절 경로 + STT helper 재사용 | ✅ |
| BE API | `backend/app/api/journal.py` | `httpx` import + `POST /journal/parse-photos` 라우터 70줄 추가 | ✅ |
| FE Types | `frontend/src/types/index.ts` | `JournalEntryAPI.source` Literal에 `"vision"` 추가 | ✅ |
| FE Hook | `frontend/src/hooks/useJournalData.ts` | `parsePhotos(files, ctx)` 메서드 추가 + return | ✅ |
| FE Component — PhotoInput | `frontend/src/modules/journal/PhotoInput.tsx` | 244 lines, FAB + 다운샘플 + 진행 오버레이 | ✅ |
| FE Page | `frontend/src/modules/journal/JournalPage.tsx` | `<PhotoInput>` 렌더 + handlePhotoParsed + inputSource state + 타임라인 amber 점/"사진 입력" 라벨 | ✅ |
| Docs Plan | `docs/01-plan/features/journal-vision-input.plan.md` | 0.1.1 (모델 변경 반영) | ✅ |
| Docs Design | `docs/02-design/features/journal-vision-input.design.md` | 0.1.1 (모델 변경 + File Modification Policy) | ✅ |
| Docs Analysis | 본 문서 | — | ✅ |

---

## 4. Functional Match (88%)

### 4.1 BE 검증

| 검증 | 방법 | 결과 |
|------|------|:----:|
| Python syntax (5개 파일) | `ast.parse` | ✅ |
| Lint | `uv run ruff check` | ✅ All checks passed |
| EXIF 추출: 정상 JPEG | 합성 JPEG + Pillow getexif | ✅ taken_at = 2026-04-21 09:30:00 |
| EXIF 추출: PNG/스크린샷/손상 | 단위 테스트 | ✅ has_exif=False fallback |
| `build_exif_summary` | 단위 테스트 | ✅ "사진1: 촬영시각=…" 포맷 |
| Vision parser 메시지 구성 | httpx 모킹 + 페이로드 검증 | ✅ system + user(text+image_url[]) 정확 |
| 거절 응답 처리 | LLM 모킹 (`{"rejected":true}`) | ✅ entries=[], reject_reason 전파 |
| API 400/413 validation | FastAPI TestClient | ✅ 빈/비이미지/11장/6MB 모두 차단 |
| **Live LiteLLM 호출** | gpt-5-mini, 합성 농약 라벨 | ✅ 200 / 11.7s / OCR 성공 / DB 매칭 0.95 |
| EXIF + 라벨 E2E | live + EXIF JPEG | ✅ work_date=2026-04-21 (EXIF 우선 적용) |
| 다중 이미지 그룹핑 | 2장 (라벨+잎) live | ✅ 1건 entry로 합침, detail 통합 |
| 단색 거절 경로 | 핑크 단색 live | ✅ rejected=true, reason 정확 |

### 4.2 FE 검증

| 검증 | 방법 | 결과 |
|------|------|:----:|
| TypeScript 빌드 | `npx tsc -b` (vision 관련) | ✅ 0 새 에러 (남은 5개는 pre-existing) |
| ESLint (변경 파일) | `npx eslint useJournalData.ts PhotoInput.tsx JournalPage.tsx` | ✅ 0 새 에러 |
| FAB 렌더링 위치 | preview_eval `getBoundingClientRect` | ✅ 사진 FAB `bottom-[152px]`, STT FAB `bottom-[88px]`, 충돌 없음 |
| FAB → 파일 선택 → 분석 → 폼 prefill | DataTransfer + change 이벤트 | ✅ 폼 자동 오픈, 9개 input 모두 prefill |
| 저장 → 타임라인 표시 | 폼 submit + amber dot 확인 | ✅ amber dot + "사진 입력" 라벨 |
| 거절 경로 toast | 단색 사진 시뮬레이션 | ⚠️ overlay 잔존 — DataTransfer 부작용 의심, 실 사용자 click 흐름 재검증 필요 |

### 4.3 SC-1 (응답 시간) 분석

**예산 vs 실측**:
- 단일 사진 (브라우저 → API): 11.7s (예산 8s 대비 +47%)
- 단일 사진 (FE 다운샘플 + API + 전후처리): 15.4s
- 다중 사진 (2장): 16.3s

**원인**:
- gpt-5-mini는 GPT-5 family 중 가장 작은 vision 모델이지만, reasoning 모델 특성상 첫 토큰 latency가 큼
- 합성 이미지 크기는 작음 (4~7KB) → 토큰 수보다는 모델 자체 latency가 지배적

**개선 경로**:
- LiteLLM 프록시에 `gemini-2.5-flash` 등록 → `LITELLM_VISION_MODEL` 환경변수 한 줄로 전환. Gemini Flash는 reasoning 없는 멀티모달이라 5~7s 예상
- 즉시 개선이 필요하면 V1.1 에서 OpenRouter 직접 호출 옵션 추가 (design §6.2 Fallback)
- V1 시연 시점에는 "분석 중..." 진행 표시로 UX 보완

---

## 5. Contract Match (100%)

| Contract | 정의 | 실측 | 상태 |
|----------|------|------|:-:|
| 응답 shape | `{entries: [{parsed, confidence, pesticide_match?}], used_exif, image_count, rejected, reject_reason?, unparsed_text}` | 동일 | ✅ |
| `entries[].parsed` 필드 | work_date, field_name, crop, work_stage, weather, disease, usage_pesticide_*, usage_fertilizer_*, detail | 동일, work_stage는 화이트리스트 검증 | ✅ |
| `confidence` shape | `{필드: 0.0~1.0}` | 일괄 0.7 (vision은 raw_text 매칭 없음) | ✅ |
| `pesticide_match` shape | STT parser와 동일 (matched, confidence, brand, company, purpose, raw_name, uncertain) | 동일 — `enrich_with_pesticide_match` 후처리 재사용 | ✅ |
| 거절 응답 | `{entries: [], rejected: true, reject_reason: str}` | 동일 | ✅ |
| HTTP 400/413/502/504 | 갯수/크기/timeout 매핑 | 검증됨 (TestClient) | ✅ |
| `source="vision"` 저장 | DB JournalEntry.source | 검증됨 (E2E 저장 후 amber 점) | ✅ |

---

## 6. 발견 사항 (Findings)

### 6.1 LiteLLM 프록시 모델 등록 차이
- **발견**: design 0.1.0 default `gemini-2.5-flash`는 LiteLLM 프록시(`litellm.lilpa.moe/v1`)에 등록되지 않음 (2026-04-28 시점). 등록된 vision 모델은 `gpt-5-mini`, `gpt-5-nano`만.
- **조치**: design 0.1.1 + plan 0.1.1 에서 default를 `gpt-5-mini`로 변경. Gemini Flash 등록 시 .env 한 줄 오버라이드로 전환.

### 6.2 합성 한글 텍스트 OCR 한계
- **발견**: Pretendard Bold 32pt 한글 텍스트는 OCR에서 "글자 판독 불가"로 인식됨. 반면 brower OffscreenCanvas의 `bold 26~28px sans-serif`는 정확히 OCR 됨.
- **추정 원인**: Pretendard 한글 글리프의 작은 디테일 + JPEG 압축 손실 + 작은 이미지 (500x300) 조합. 실제 농약 통 사진(고해상도, 표준 한글 글꼴, 밝은 조명)은 더 잘 동작할 가능성 높음.
- **조치**: 본 단계에선 가설로 기록. 시연/실 사용자 데이터로 재검증. 필요 시 클라이언트 다운샘플 해상도 상향 (1280px → 1600px) 검토.

### 6.3 거절 응답 시 FE overlay 잔존 가능성
- **발견**: preview MCP 에서 단색 이미지를 `DataTransfer + dispatchEvent('change')` 로 시뮬레이션 시, BE는 200(rejected:true) 정상 응답하나 FE 분석 오버레이가 setStatus("idle") 시점에 사라지지 않는 현상 관찰. 취소 버튼 클릭도 효과 없음.
- **추정 원인**: (a) 시뮬레이션 시 `Object.defineProperty(input, 'files', ...)` 강제 설정과 React onChange 핸들러의 `e.target.value = ''` reset 사이의 race, 또는 (b) PhotoInput 의 forwardRef wrapping과 React Fiber 상태 업데이트 사이의 미세 버그.
- **조치**: 실 사용자가 파일 다이얼로그로 선택하는 흐름에서 재현되는지 확인 필요 (Playwright 또는 실디바이스). V1.1 후속 검토 항목.

### 6.4 멀티이미지 그룹핑 정확도
- **발견**: 2장 (농약 통 라벨 + "방제 후 잎" 라벨) → 1건 entry로 정확히 합침. detail에 두 사진 정보를 통합하고, 농약 매칭 정상.
- **시사점**: LLM이 EXIF/사용자 컨텍스트/시각 단서를 종합해 그룹핑 판단 함. design §4.1 그룹핑 규칙이 효과적으로 작동.

### 6.5 EXIF 우선순위 동작
- **발견**: EXIF DateTimeOriginal=2026-04-21 / 오늘=2026-04-28 인 사진에서, 응답 `work_date=2026-04-21` 정확히 적용.
- **시사점**: prompt 의 "EXIF 촬영시각 hint가 있으면 그 날짜 사용" 가이드라인이 LLM에 의해 정확히 따라짐. EXIF GPS hint 활용도 (필지 매핑 V2)는 본 단계 미검증.

### 6.6 사용자 컨텍스트 vs 사진 단서 충돌 처리
- **발견**: 사용자가 `crop=고추`로 컨텍스트 설정했지만 사진엔 "사과 탄저병" 라벨이 보일 때, LLM이 detail에 "고추밭에서의 사용 여부는 사진만으로 불명확함"이라고 정직히 기록. crop 필드는 사용자 컨텍스트(`고추`)를 따름.
- **시사점**: 환각 방지 prompt가 효과적. 사용자 검수 단계에서 발견·수정하면 됨.

---

## 7. Quality Eval (수동, 라벨 사진 1장 기준)

| 메트릭 | 측정 | 결과 |
|--------|------|:-:|
| 필수 4개 필드 채움률 | work_date + field_name + crop + work_stage | 100% (4/4) |
| 농약 라벨 매칭률 | 라벨이 정면·선명한 1장 | 100% (1/1, confidence 0.95) |
| 환각 비율 | 사용자 평가 "사진에 없는 정보가 채워진" entry | 0% (모르면 null로 비움) |

> 샘플 N=1 이라 통계적 유의성은 낮음. 시연/실 사용자 데이터로 5~10건 누적 후 재평가 필요 (Phase 4 Report).

---

## 8. Performance & Cost 실측

| 지표 | 예산 | 실측 |
|------|------|------|
| 단일 사진 응답 (BE) | < 8s | 11.7s |
| 단일 사진 응답 (FE+BE end-to-end) | < 8s | 15.4s |
| 다중 사진 (2장) 응답 | < 12s | 16.3s |
| 사진 1건 비용 추정 (gpt-5-mini, ~600 in + 200 out tokens) | < $0.005 | 미실측 — LiteLLM 프록시 측 ledger 의존 |
| FE 다운샘플 (긴 변 1280px) | < 1.5s (3장) | 합성 이미지 크기 작아 다운샘플 스킵 (200KB 미만) |

---

## 9. Open Issues / Follow-ups

| ID | Issue | Severity | Target |
|----|-------|:--:|:------:|
| OI-1 | 응답 시간 예산 초과 (gpt-5-mini 11~16s vs 예산 8s) | Medium | LiteLLM 운영자에 Gemini 2.5 Flash 등록 요청. 등록 후 .env 한 줄 전환 |
| OI-2 | 거절 응답 시 FE overlay 잔존 가능성 (시뮬레이션 환경 한정일 가능성) | Medium | 실 사용자 click 흐름 또는 Playwright 로 재검증 |
| OI-3 | 합성 한글 라벨 OCR 약함 (Pretendard) | Low | 실 농약 통 사진(고해상도, 명확한 글꼴)으로 재검증 — 시연 시 |
| OI-4 | 사진 비용 모니터링 ledger 부재 | Low | LiteLLM 프록시 측 사용량 대시보드 활용 또는 V2 에서 BE 측 호출 로그 도입 |
| OI-5 | EXIF GPS → 필지 매핑 미구현 (V2 예정) | Low | V2 별도 feature |

---

## 10. Recommendation

**Report 단계 진입 가능** (Match Rate 95.2% ≥ 90% Gate 충족).

권장 다음 액션:
1. 시연 자료 준비 — 실 농약 통 사진 2~3장으로 라이브 시연 시나리오 검증
2. LiteLLM 운영자에 Gemini 2.5 Flash 등록 요청 (응답 시간 개선)
3. OI-2 재검증 (실 사용자 흐름)
4. PR 제출 → 멘토 리뷰 → Phase 4 Report 작성

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial — BE/FE 구현 완료, live LiteLLM + 브라우저 E2E 검증 | JunePark2018 |

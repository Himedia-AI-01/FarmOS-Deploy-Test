# Journal Vision Input (사진 → 영농일지) Planning Document

> **Summary**: 사용자가 영농 작업 사진을 1~N장 업로드하면 LiteLLM 프록시 경유 vision LLM(현재 default: `gpt-5-mini`, 목표: `gemini-2.5-flash` 프록시 등록 시 전환)이 사진을 분석해 농업ON 포맷의 영농일지 entry N건을 prefill 한다. 사용자는 미리보기 화면에서 검수·편집한 뒤 저장한다. 기존 STT 파이프라인의 schema/store/농약 매칭 인프라를 재사용한다.
>
> **Project**: FarmOS - journal-vision-input
> **Version**: 0.1.0
> **Author**: JunePark2018
> **Date**: 2026-04-28
> **Status**: Draft
> **Prerequisites**: 영농일지 STT/CRUD/PDF 인프라(이미 구현됨), `pesticide_matcher.enrich_with_pesticide_match`, OpenRouter API 키 발급 환경

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 현재 영농일지 입력은 STT(음성)와 직접 텍스트 입력 두 채널뿐이다. 음성은 손이 자유롭지만 시각 정보(농약 라벨 사진, 작업 현장 모습, 병해충 상태 등)는 표현할 수 없다. 농부는 작업 도중 사진을 자주 찍지만 그 사진이 일지 작성과 연결되지 않는다. |
| **Solution** | 영농일지 입력 채널에 **사진 업로드 → Vision LLM 파싱 → entry prefill** 경로를 추가한다. 사진은 1~N장 자유 업로드(카메라 직촬/갤러리), Gemini 2.5 Flash가 사진을 분석해 STT parser와 동일한 JSON 스키마로 entry 배열을 반환하고, 기존 농약 매칭/저장 파이프라인을 그대로 통과시킨다. EXIF의 촬영시각·GPS 메타는 `work_date`/`field_name` 추정 hint로 사용한다. |
| **Function/UX Effect** | 영농일지 작성 화면에 "사진으로 작성" 버튼 추가 → 사진 N장 업로드 → 분석 진행 → entry N건 prefill 미리보기 → 사용자 검수/편집 → 저장. 기존 STT 입력은 그대로 유지된다. |
| **Core Value** | (1) 멘토 제안 직접 반영 — "저번보다 개선" 시연 임팩트. (2) 농약 라벨 OCR 기반 자동 채움으로 STT가 놓치는 제품명/사용량 정확도 보강. (3) 음성/사진/텍스트 3채널을 사용자가 상황 따라 선택. |

---

## Context Anchor

> Auto-generated from Executive Summary. Propagated to Design/Do documents for context continuity.

| Key | Value |
|-----|-------|
| **WHY** | 멘토(2026-04-28)가 "사진 여러 장 → AI 자동 영농일지 작성" 방향을 제안했다. STT는 손은 자유롭지만 시각 정보(라벨/현장 상태)를 담지 못하고, 텍스트 입력은 손이 묶인다. 농부가 이미 찍는 사진을 그대로 활용하면 입력 비용이 크게 낮아진다. |
| **WHO** | 작업 중인 농부(현장에서 사진을 빠르게 찍어 올리는 1차 사용자), 멘토/평가자(시연에서 "저번 대비 개선" 확인), 팀(STT 인프라 재사용으로 conflict 최소). |
| **RISK** | (R1) Vision LLM 환각 — 사용자 검수 단계 필수, 자동 저장 금지. (R2) Gemini API 비용/쿼터 — 1일 호출 한도 모니터링, 클라이언트 다운샘플(긴 변 1280px)로 토큰 절감. (R3) EXIF 누락(스크린샷·웹 갤러리) — 추정 실패 시 정상 fallback(현재 날짜/필지 미지정). (R4) 멀티이미지 그룹핑 — 한 번에 N장이 동일 작업인지 다른 작업인지 LLM 판단에 의존, 명확하지 않으면 entry 1건으로 합침. (R5) 농약 라벨 OCR이 잘못된 제품명을 만들어 약사법 이슈 — 기존 농약 DB 퍼지 매칭으로 비매칭 시 raw text 보존. |
| **SUCCESS** | (SC-1) 사진 1장 업로드 → entry 1건 prefill p95 < 8 s. (SC-2) 사진 N장 업로드 시 entry 수가 LLM 판단 기반 1~N건. (SC-3) prefill된 entry의 필수 필드(work_date/field_name/crop/work_stage) 채움률 ≥ 60% (미채움은 사용자 입력). (SC-4) 농약 라벨이 명확히 보이는 사진은 `usage_pesticide_product`가 기존 농약 DB에 매칭(또는 raw 보존). (SC-5) 시연 시연자가 영농일지 작성 화면에서 사진→entry 흐름을 1분 내에 보여줄 수 있다. |
| **SCOPE** | IN: BE `/journal/parse-photos` 신규 API, `journal_vision_parser` 모듈, EXIF 추출 유틸, FE 사진 업로드 컴포넌트 + prefill 미리보기 UI, `source` 필드에 `"vision"` 추가, 기존 STT parser와 동일한 entry 배열 응답. OUT: STT+Vision 동시 입력 결합(V2), 파인튜닝, ESP/IoT 연계, PDF에 사진 첨부. **(Note: 사진 영구 저장 + 갤러리/lightbox 는 본 feature 의 V1 OUT 이었으나, 후속 feature [`journal-entry-photos`](./journal-entry-photos.plan.md) 로 별도 작성되어 같은 PR 에 함께 머지되었음. 따라서 머지된 PR 기준으로는 영구 저장도 IN.)** |

---

## 1. Overview

### 1.1 Purpose

영농일지 입력 채널에 **사진 기반 자동 작성**을 추가한다. 사용자는 작업 사진을 1~N장 업로드하기만 하면 Vision LLM이 농업ON 포맷의 entry 배열을 prefill 하고, 사용자가 검수·편집 후 저장한다.

### 1.2 Background

- **기존 인프라(이미 구현됨)**:
  - `backend/app/api/journal.py` — `/transcribe`(audio→text), `/parse-stt`(text→entries[])
  - `backend/app/core/journal_parser.py` — OpenRouter + Gemma 4 31B로 비정형 텍스트 → 구조화 JSON
  - `backend/app/core/pesticide_matcher.py` — `enrich_with_pesticide_match` 후처리
  - `backend/app/schemas/journal.py` — `JournalEntryCreate.source: Literal["stt","text","auto"]`
  - `backend/app/core/journal_store.py` — CRUD/요약/누락체크
- **부족한 부분**: 사진 입력 채널 부재. 농약 라벨/현장 사진처럼 시각 정보가 풍부한 단서가 일지 작성에 반영되지 않는다.
- **멘토 피드백 (2026-04-28)**: "사진 여러 장 → AI가 영농일지 자동 작성" 방향 제안. 강제는 아니지만 "저번보다 개선" 충족 수단으로 가장 가시적.
- **선행 정리**: 이전 design notes(`docs/features/journal-vision-input.design.md`)는 git reset으로 폐기. 본 plan/design은 정식 파이프라인 위치(`docs/01-plan/features/`, `docs/02-design/features/`)에 작성.

### 1.3 Related Documents

- [Journal STT Parser](../../../backend/app/core/journal_parser.py) — vision parser가 따라야 할 출력 스키마 reference
- [Journal Schema](../../../backend/app/schemas/journal.py) — `JournalEntryCreate` (vision도 동일 shape 반환)
- [Journal Store](../../../backend/app/core/journal_store.py) — 저장 경로 재사용
- [Pesticide Matcher](../../../backend/app/core/pesticide_matcher.py) — 농약 라벨 OCR 결과 보정
- [Journal API](../../../backend/app/api/journal.py) — 신규 `/parse-photos` 라우터 추가 위치

---

## 2. Scope

### 2.1 In Scope

- [ ] **백엔드 — Vision parser 모듈** (`backend/app/core/journal_vision_parser.py` ★신규)
  - `parse_photos(images: list[bytes], exif_hints: list[ExifHint], field_name: str|None, crop: str|None) -> dict` — STT parser와 동일 shape `{entries: [...]}` 반환
  - Gemini 2.5 Flash via OpenRouter (또는 Google AI Studio 직호출 — Architecture Considerations에서 결정)
  - 멀티이미지 1회 호출(LLM이 그룹핑·분리 판단)
- [ ] **백엔드 — EXIF 추출 유틸** (`backend/app/core/exif_utils.py` ★신규)
  - 촬영시각 → `work_date` hint
  - GPS → `field_name` hint(좌표만, 필지 매핑은 V2)
- [ ] **백엔드 — API 라우터** (`backend/app/api/journal.py` 수정)
  - `POST /journal/parse-photos` (multipart, files[] 업로드, 옵션 form: field_name, crop)
  - 응답: `{entries: [...], used_exif: bool}` — 기존 `/parse-stt` 응답과 호환
  - `enrich_with_pesticide_match` 후처리 동일 적용
- [ ] **백엔드 — schema 확장** (`backend/app/schemas/journal.py`)
  - `source: Literal["stt","text","auto","vision"]` — `"vision"` 추가
- [ ] **프론트 — 사진 입력 컴포넌트** (`frontend/src/modules/journal/PhotoInput.tsx` ★신규)
  - 카메라 캡처 + 갤러리 선택 (multiple)
  - 클라이언트 다운샘플(긴 변 1280px, JPEG q=85) — 토큰/네트워크 절감
  - 업로드 → 진행 표시 → entry prefill 미리보기 전달
- [ ] **프론트 — prefill 미리보기 통합** (`frontend/src/modules/journal/JournalEntryForm.tsx` 수정)
  - 기존 STT 미리보기 경로(`/parse-stt` 응답 처리) 재사용
  - source 라벨에 "사진" 표시
- [ ] **환경변수 / 설정** (`backend/app/core/config.py` 수정)
  - 기존 `LITELLM_URL`, `LITELLM_API_KEY` 재사용 (vision 호출도 동일 LiteLLM 프록시)
  - 신규 추가:
    - `LITELLM_VISION_MODEL: str = "gpt-5-mini"` (현재 default; 프록시에 `gemini-2.5-flash` 등록 시 .env로 오버라이드)
    - `JOURNAL_VISION_TIMEOUT_S: float = 120.0`
    - `JOURNAL_VISION_MAX_IMAGES: int = 10`
    - `JOURNAL_VISION_MAX_BYTES: int = 5 * 1024 * 1024`
- [ ] **Docs 갱신**
  - `docs/02-design/features/journal-vision-input.design.md` (이번 작업)

### 2.2 Out of Scope

- ~~사진을 BE에 영구 저장 (이번 단계는 in-memory 처리 후 폐기, 영구 저장은 V2)~~ → **후속 feature [`journal-entry-photos`](./journal-entry-photos.plan.md) 로 분리 작성되어 같은 PR 에 함께 머지됨**
- ~~사진 갤러리/타임라인 UI (별도 feature)~~ → 위와 동일하게 머지됨
- STT + Vision 동시 입력 결합 (V2)
- Vision 파인튜닝 (zero-shot prompt engineering으로 충분, 데이터 누적 후 V3에서 재평가)
- IoT/ESP 연계
- PDF 출력에 사진 첨부
- 필지 좌표 → field_name 매핑 테이블 (GPS는 좌표만 hint로 전달)
- 사진 EXIF 기반 위변조 검증

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `POST /journal/parse-photos`가 multipart로 사진 1~N장을 받는다 | High | Pending |
| FR-02 | Vision parser가 사진을 분석해 STT parser와 동일한 `{entries: [...]}` shape을 반환한다 | High | Pending |
| FR-03 | LLM이 사진들을 보고 동일 작업이면 entry 1건, 별개 작업이면 N건으로 분리한다 | High | Pending |
| FR-04 | EXIF 촬영시각이 있으면 `work_date`의 hint로 LLM prompt에 주입된다 | High | Pending |
| FR-05 | EXIF GPS가 있으면 좌표가 LLM prompt에 hint로 주입된다 (필지 매핑은 미수행) | Medium | Pending |
| FR-06 | 농약 라벨이 명확한 사진의 `usage_pesticide_product`는 `enrich_with_pesticide_match`로 보정된다 | High | Pending |
| FR-07 | `JournalEntryCreate.source`가 `"vision"` 값을 허용한다 | High | Pending |
| FR-08 | 프론트 `PhotoInput` 컴포넌트가 카메라/갤러리에서 사진을 1~N장 선택할 수 있다 | High | Pending |
| FR-09 | 프론트가 업로드 전 사진을 긴 변 1280px로 다운샘플한다 | High | Pending |
| FR-10 | prefill된 entry는 기존 STT 미리보기 폼에 그대로 채워져 사용자가 편집·저장할 수 있다 | High | Pending |
| FR-11 | 사진 처리 중 진행 상태(업로드/분석)가 UI에 표시된다 | Medium | Pending |
| FR-12 | LLM 호출 실패 시 사용자에게 명확한 에러 메시지가 표시되고 사진 빈 폼으로 fallback 한다 | Medium | Pending |
| ~~FR-13~~ | ~~사진은 BE에서 메모리로 처리하고 응답 후 폐기한다 (영구 저장 X)~~ Superseded — 후속 feature `journal-entry-photos` 로 영구 저장 도입됨 | — | Superseded |
| FR-14 | EXIF의 GPS는 정확도 보장 없이 hint 용도로만 사용 — 사용자 위치정보 노출 우려 안내 문구 | Low | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Performance | 사진 1장 → entry 1건 prefill 응답 p95 < 8 s (다운샘플 후 200KB 기준) | 시연 시 stopwatch |
| Performance | 사진 5장 → 응답 p95 < 15 s | 동일 |
| Cost | 사진 1건 처리 비용 < $0.005 (Gemini 2.5 Flash 기준 $0.10/1M input tokens) | 호출 로그 + 토큰 카운트 |
| Reliability | LLM 5xx/timeout 시 사용자에게 "분석 실패" 토스트, 빈 폼 fallback | 코드 리뷰 + 강제 실패 테스트 |
| Privacy | 업로드된 사진은 BE에서 처리 후 즉시 폐기, 디스크/DB에 저장 X | 코드 리뷰 |
| Quality | prefill된 필수 필드 채움률 ≥ 60% (10장 샘플 평가) | 수동 평가 표 |
| UX | 모바일 기기에서 카메라 직촬 → 업로드까지 3 tap 이내 | 수동 시연 |
| Backward Compat | `source` literal 확장이 기존 entry 조회/저장에 영향 없음 | 기존 STT/text 입력 회귀 테스트 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] FR-01~FR-13 (High) 모두 구현 완료
- [ ] 사진 1장/3장/5장 시연 시나리오에서 prefill 결과를 사용자가 편집해 정상 저장
- [ ] 농약 라벨 사진이 기존 농약 DB에 매칭되는 케이스 1건 이상 시연
- [ ] 기존 STT/text 입력 회귀 없음 (수동 확인)
- [ ] design 문서(`docs/02-design/features/journal-vision-input.design.md`) 작성 완료
- [ ] BE/FE 빌드·린트 에러 0

### 4.2 Quality Criteria

- [ ] BE unit test: vision parser 응답 shape 검증, EXIF 파서 정상/누락 케이스
- [ ] 수동 시연: 카메라 직촬 → entry prefill → 편집 → 저장 1분 내 완료
- [ ] 농약 라벨 OCR 매칭률 ≥ 70% (라벨이 정면·선명한 5장 기준)

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **R1**: Vision LLM 환각으로 잘못된 농약/작업명 prefill | High | Medium | 자동 저장 금지, 사용자 검수 단계 필수. 농약명은 `enrich_with_pesticide_match` 퍼지 매칭으로 비매칭 시 raw 보존. |
| **R2**: Gemini API 비용 폭증 | Medium | Low | 클라이언트 다운샘플(긴 변 1280px), 사진당 호출 수 1회(멀티이미지 batch), 일/월 호출 한도 알림. |
| **R3**: EXIF 누락(스크린샷·소셜 업로드) | Low | High | 누락 시 hint 없이 진행, prompt에 "메타 없음" 명시, work_date 미채움 → 사용자 입력 유도. |
| **R4**: 멀티이미지 그룹핑 오판 (서로 다른 작업을 1건으로 합침 등) | Medium | Medium | prompt에 "각 사진 시간/장소가 다르면 별개 entry" 가이드. 사용자가 미리보기에서 entry 분리/병합 가능. |
| **R5**: 농약 라벨 OCR이 약사법 위반 제품명 생성 | Medium | Low | 비매칭 시 `usage_pesticide_product=null` + raw 보존, 사용자 확인 필수. |
| **R6**: 모바일 카메라에서 거대 사진(8000x6000) 직업로드로 메모리 OOM | Medium | Medium | FE 다운샘플 강제, BE 단에서도 max 5MB/장 제한. |
| **R7**: GPS 정보 유출 우려 | Low | Low | 사진 업로드 전 안내 문구, GPS는 LLM hint 용도로만 사용 후 폐기. EXIF strip 옵션 제공(V2). |
| **R8**: OpenRouter vs 직접 Gemini API 선택에 따른 가용성/요금 차이 | Low | Medium | Architecture §7.2에서 비교 결정, 환경변수로 backend 모드 토글 가능하게 설계. |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `backend/app/core/journal_vision_parser.py` | 신규 모듈 | Vision LLM 호출, multipart bytes → entries[] 변환 |
| `backend/app/core/exif_utils.py` | 신규 모듈 | Pillow로 EXIF 시간·GPS 추출 |
| `backend/app/api/journal.py` | API 라우터 수정 | `POST /journal/parse-photos` 엔드포인트 추가 (라우터 등록만) |
| `backend/app/schemas/journal.py` | Pydantic 수정 | `source` Literal에 `"vision"` 추가 (1글자 추가) |
| `backend/app/core/config.py` | 설정 수정 (공유 파일) | `LITELLM_VISION_MODEL`, `JOURNAL_VISION_TIMEOUT_S`, `JOURNAL_VISION_MAX_IMAGES`, `JOURNAL_VISION_MAX_BYTES` 추가 (existing settings 그대로 유지, 클래스 끝에 추가) |
| `frontend/src/modules/journal/PhotoInput.tsx` | 신규 컴포넌트 | 사진 선택 + 다운샘플 + 업로드 |
| `frontend/src/modules/journal/JournalEntryForm.tsx` | 컴포넌트 수정 | "사진으로 작성" 진입점 추가 |
| `frontend/src/api/journal.ts` (또는 동등) | API 클라이언트 | `parsePhotos(files)` 함수 추가 |
| `docs/02-design/features/journal-vision-input.design.md` | 신규 Doc | 본 plan의 후속 design |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `JournalEntryCreate.source` | WRITE | `backend/app/api/journal.py` POST/PATCH 경로, FE 폼 | **Compatible** — Literal 확장만, 기존 값 모두 유효 |
| `enrich_with_pesticide_match` | CALL | `/parse-stt` 후처리 | **None** — vision도 동일 함수 호출, 함수 시그니처 불변 |
| `journal_store.create_entry` | WRITE | 기존 CRUD | **None** — vision 결과도 동일 schema로 저장 |
| 기존 STT 미리보기 폼 | RENDER | `JournalEntryForm.tsx` | **Reused** — vision 응답 shape이 동일하므로 동일 폼이 처리 |
| OpenRouter API 키 | EXT CALL | `journal_parser.py` | **Shared** — vision도 동일 키 사용 가능. Gemini 직호출 선택 시 별도 키. |

### 6.3 Verification

- [ ] 기존 STT 입력 → entry 저장 회귀 테스트
- [ ] 기존 text 입력 → entry 저장 회귀 테스트
- [ ] 농약 매칭 후처리가 STT/vision 모두에서 동일하게 작동
- [ ] `source="vision"` 저장된 entry가 PDF 출력에 정상 포함
- [ ] vision 호출 실패 시에도 기존 입력 채널은 정상

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | Simple structure | Static sites | ☐ |
| **Dynamic** | Feature-based modules, React + FastAPI + Postgres | Web apps with backend | ☑ |
| **Enterprise** | Strict layer separation, DI, microservices | High-traffic systems | ☐ |

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Vision 모델 | LiteLLM 프록시 등록 vision 모델 중 선택 | **`gpt-5-mini` (현재) / `gemini-2.5-flash` (목표)** | 2026-04-28 LiteLLM 프록시 점검 결과 등록 vision 모델은 GPT-5 family(`gpt-5-mini`, `gpt-5-nano`)뿐이라 즉시 동작 가능한 default로 `gpt-5-mini` 채택. Gemini 2.5 Flash 가 프록시에 등록되면 `LITELLM_VISION_MODEL` 환경변수 한 줄로 전환. |
| 파인튜닝 | (a) zero/few-shot prompt engineering / (b) Gemma vision 파인튜닝 | **(a)** | 데이터 수집/라벨링/GPU 비용 대비 ROI 낮음, 멘토가 "강제 X"라 했으니 V1은 prompt만, V3에서 실데이터 누적 후 재평가 |
| 입력 채널 | (a) Vision 단독 / (b) STT+Vision 결합 / (c) 둘 독립 | **(c) 독립 채널** | 사용자가 상황 따라 선택, 결합은 V2에서 추가 |
| 사진 저장 정책 | (a) BE 영구 저장 / (b) 메모리 처리 후 폐기 / (c) 임시 디스크 캐시 | ~~**(b)**~~ → **(a)** | 초안엔 (b)였으나 후속 feature `journal-entry-photos` 로 (a) 로 변경: 사진 영구 저장 + 24h orphan cleanup + owner-only 권한 |
| 다운샘플 위치 | (a) FE에서만 / (b) BE에서만 / (c) 양쪽 | **(c)** | FE는 네트워크 절감(긴 변 1280px), BE는 안전망(max 5MB) |
| 멀티이미지 처리 | (a) 사진별 N회 호출 / (b) 1회 batch 호출 (LLM이 그룹핑 판단) | **(b) 1회 batch** | 비용·지연 절감, LLM이 사진 간 관계까지 파악 |
| EXIF 라이브러리 | (a) `piexif` / (b) `exifread` / (c) `Pillow` 내장 | **(c) Pillow + piexif fallback** | Pillow는 이미 다운샘플로도 필요, piexif는 GPS 변환 보조 |
| 사진 prefill 응답 shape | (a) STT parser와 별도 / (b) STT parser와 동일 | **(b)** | FE 미리보기 폼 재사용, 2개 입력 채널 분기 최소 |
| 테스트 | pytest + 수동 시연 | 동일 | 기존 표준 |

### 7.3 Clean Architecture Approach

```text
Dynamic Level — Feature-based:

backend/app/
├── core/
│   ├── journal_parser.py              기존 — STT 텍스트 파서
│   ├── journal_vision_parser.py       ★신규 — 사진 → entries[]
│   ├── exif_utils.py                  ★신규 — EXIF 시간/GPS 추출
│   ├── pesticide_matcher.py           기존 — 후처리 재사용
│   ├── journal_store.py               기존 — 저장 재사용
│   └── config.py                      수정 (VISION_MODEL_ID)
├── api/
│   └── journal.py                     수정 — /parse-photos 추가
└── schemas/
    └── journal.py                     수정 — source: "vision" 추가

frontend/src/
├── modules/journal/
│   ├── PhotoInput.tsx                 ★신규 — 카메라/갤러리 + 다운샘플
│   ├── JournalEntryForm.tsx           수정 — "사진으로 작성" 진입점
│   └── (기존 STT 미리보기 폼 재사용)
└── api/
    └── journal.ts                     parsePhotos(files) 추가
```

### 7.4 Data Flow

```text
┌──────────────┐
│ 사용자(농부) │
└──────┬───────┘
       │ 카메라/갤러리에서 사진 N장 선택
       ▼
┌─────────────────────┐
│ FE PhotoInput       │
│  · 다운샘플 1280px  │
│  · multipart 빌드   │
└──────┬──────────────┘
       │ POST /journal/parse-photos
       ▼
┌──────────────────────────────────────┐
│ BE /journal/parse-photos             │
│  ┌─────────────────────────────┐    │
│  │ exif_utils.extract(images)  │    │
│  │  → 촬영시각/GPS hint        │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │ journal_vision_parser       │    │
│  │  · Gemini 2.5 Flash 호출    │    │
│  │  · multi-image batch        │    │
│  │  → entries[] (STT 동일 shape│    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │ enrich_with_pesticide_match │    │
│  │  (entry별 농약 DB 퍼지매칭) │    │
│  └─────────────────────────────┘    │
└──────┬───────────────────────────────┘
       │ {entries: [...], used_exif: bool}
       ▼
┌─────────────────────┐
│ FE 미리보기 폼      │
│  · 기존 STT 폼 재사용│
│  · 사용자 검수/편집 │
└──────┬──────────────┘
       │ POST /journal (기존 CRUD)
       ▼
┌─────────────────┐
│ DB journal_entry│
│  source="vision"│
└─────────────────┘
```

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [x] OpenRouter API 키 통한 LLM 호출 (`journal_parser.py`)
- [x] FastAPI multipart 업로드 패턴 (`/transcribe` 엔드포인트)
- [x] STT entry shape `{entries: [...]}` 응답
- [x] 농약 매칭 후처리 (`enrich_with_pesticide_match`)
- [x] `source` Literal로 입력 채널 구분
- [ ] Multi-image LLM 호출 패턴 — 이번에 신설

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| **Vision LLM 호출 wrapper** | 미존재 | OpenRouter `messages[].content`에 `image_url`(base64 또는 URL) 배열 | High |
| **EXIF 추출** | 미존재 | Pillow `_getexif()` + GPS 변환 헬퍼 | High |
| **클라이언트 다운샘플** | 미존재 | Canvas API, 긴 변 1280px, JPEG q=85 | High |
| **Multipart files[] 처리** | `/transcribe`는 단일 file | FastAPI `List[UploadFile]` + 사이즈 검증 | Medium |
| **Vision 비용 로그** | 미존재 | 호출당 토큰/비용 로깅 (모니터링) | Low |

### 8.3 Environment Variables Needed

| Variable | Purpose | Scope | To Be Created |
|----------|---------|-------|:-------------:|
| `LITELLM_URL` | 기존 키 재사용 (vision 호출도 동일 LiteLLM 프록시) | Backend | 기존 |
| `LITELLM_API_KEY` | 동상 | Backend | 기존 |
| `LITELLM_VISION_MODEL` | LiteLLM 프록시에 등록된 vision-capable 모델 ID (default `gpt-5-mini`, 등록 현황 변동 시 .env 오버라이드) | Backend | ☑ |
| `JOURNAL_VISION_TIMEOUT_S` | LiteLLM 호출 timeout (default 120) | Backend | ☑ |
| `JOURNAL_VISION_MAX_IMAGES` | 한 요청 최대 사진 수 (default 10) | Backend | ☑ |
| `JOURNAL_VISION_MAX_BYTES` | 사진당 최대 크기 (default 5MB) | Backend | ☑ |

### 8.5 File Modification Policy (팀 conflict 회피)

본 feature는 팀 프로젝트에서 다른 팀원과의 merge conflict를 최소화하기 위해 **신규 파일 위주 + 영농일지 전용 파일 수정**을 원칙으로 한다.

#### 영역별 분류

| 영역 | 파일 | 수정 형태 | Conflict 위험 |
|------|------|-----------|:-------------:|
| **신규 파일** | `core/journal_vision_parser.py`, `core/exif_utils.py`, `modules/journal/PhotoInput.tsx` | 신규 작성 | 0 |
| **영농일지 전용 (개인 영역)** | `api/journal.py`, `schemas/journal.py`, `modules/journal/JournalEntryForm.tsx`, `api/journal.ts` | additive(라우터·필드 추가) | 낮음 |
| **공유 핫스팟** | `core/config.py` | 클래스 끝에 settings 4개 추가 | 중간 |

#### 공유 파일 처리 원칙

- **`config.py`**: 23개 파일이 import 중인 핫스팟. 본 feature는 **클래스 끝에 4개 변수 추가만** 수행하고, 기존 변수/순서는 절대 건드리지 않는다. 같은 hunk 충돌이 나도 conflict 해결이 단순(추가만 유지).
- **`pyproject.toml`**: 수정 불필요 (Pillow 12.2 이미 의존성에 있음).
- **`docs/backend-architecture.md`**: 수정하지 않음. 본 design 문서가 단일 reference 역할.

#### Why config.py를 직접 수정하기로 결정했나

대안으로 (1) `os.environ.get()` 직접 호출, (2) 별도 sub-config 파일이 검토되었으나, **중앙 settings 일원화가 프로젝트 표준**이며 4개 변수 추가는 복원 가능한 작은 hunk라 conflict 발생 시도 해결 비용이 낮다고 판단했다.

### 8.4 Pipeline Integration

| Phase | Status | Document Location | Command |
|-------|:------:|-------------------|---------|
| Phase 1 (Plan) | ☑ 본 문서 | `docs/01-plan/features/journal-vision-input.plan.md` | 수동 |
| Phase 2 (Design) | ☐ 대기 | `docs/02-design/features/journal-vision-input.design.md` | 다음 단계 |
| Phase 3 (Analysis) | ☐ 구현 후 | `docs/03-analysis/journal-vision-input.analysis.md` | 구현 후 |
| Phase 4 (Report) | ☐ 시연 후 | `docs/04-report/journal-vision-input.report.md` | 시연 후 |

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`docs/02-design/features/journal-vision-input.design.md`)
   - Vision LLM prompt 명세 (system prompt, output JSON schema)
   - `/journal/parse-photos` API 계약 (request/response 상세)
   - FE PhotoInput 컴포넌트 동작 명세
   - EXIF 추출 모듈 함수 시그니처
2. [ ] BE 모듈 구현 순서: `exif_utils.py` → `journal_vision_parser.py` → `/parse-photos` 라우터 → schema source 확장
3. [ ] FE 구현 순서: `PhotoInput.tsx` → `journal.ts` API 클라이언트 → `JournalEntryForm.tsx` 통합
4. [ ] 시연 시나리오 3건 (사진 1장 / 3장 / 농약 라벨 OCR) 수동 검증
5. [ ] Analysis 문서 (성능/비용/품질 측정 결과)
6. [ ] Report 문서 (시연 결과 + 다음 개선)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial draft (멘토 피드백 반영, STT 인프라 재사용 전제) | JunePark2018 |
| 0.1.1 | 2026-04-28 | LiteLLM 프록시 등록 모델 점검 결과 반영 — 현재 default `gpt-5-mini`, Gemini 2.5 Flash 는 프록시 등록 시 전환 (실측 검증 완료) | JunePark2018 |
| 0.1.2 | 2026-04-29 | Post-merge update — 후속 feature `journal-entry-photos` 가 같은 PR 에 함께 머지되어 사진 영구 저장 + 갤러리 + lightbox 가 OUT scope 에서 IN 으로 이동. SCOPE/Out-of-Scope/FR-13/§7.2 동기화. | JunePark2018 |

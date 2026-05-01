# Journal Entry Photos (사진 첨부 영구 저장) Planning Document

> **Summary**: 영농일지 entry 에 사진 첨부 영구 저장 기능 추가. `/journal/parse-photos` 가 분석과 동시에 사진을 디스크에 저장하고 photo_id 를 반환, 폼 저장 시 entry 와 연결한다. 기존 STT/text/vision 모든 source 가 사진을 첨부할 수 있고, 타임라인 카드에서 썸네일 갤러리 + lightbox 로 본다.
>
> **Project**: FarmOS - journal-entry-photos
> **Version**: 0.1.0
> **Author**: JunePark2018
> **Date**: 2026-04-28
> **Status**: Draft
> **Prerequisites**: journal-vision-input (사진 입력 채널 — `/parse-photos` 인메모리 분석)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | journal-vision-input(V1) 은 사진을 분석한 뒤 폐기해 사용자가 "이 일지가 어떤 사진에서 왔는가" 추적 불가. 또한 STT/text 로 작성한 일지에는 사진을 첨부할 길 없음. 농약 라벨/현장 사진은 사후 검증·증빙 자료로 가치가 큼. |
| **Solution** | (1) `/parse-photos` 호출 시 사진을 `data/uploads/journal/{user_id}/{uuid}.jpg` 로 저장, 썸네일(1280×720) 동시 생성. (2) 신규 `journal_entry_photos` 테이블이 entry 와 N:1 관계. (3) 폼/타임라인이 첨부 사진 썸네일과 lightbox 표시. (4) 모든 source(stt/text/vision/auto) 에서 사진 첨부 가능 — entry-level 첨부 기능으로 일반화. |
| **Function/UX Effect** | 일지 저장 시 첨부된 사진이 함께 보존됨. 타임라인 카드 펼치면 썸네일 갤러리, 클릭하면 원본 lightbox. 편집 폼에서 사진 추가/제거 가능. |
| **Core Value** | 영농 활동의 시각적 증빙 + 사후 검증 + 사용자 컨텍스트 이력 보존. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | (1) Vision parse 결과의 출처 투명성. (2) STT/text 일지에도 사진 첨부 수요. (3) 영농 증빙·민원·사고 시 사진 자료. |
| **WHO** | 농부(첨부·확인), 멘토/평가자(시연), 향후 RAG/검색용 데이터 보존. |
| **RISK** | (R1) 디스크 사용량 증가 — V1 무제한 후 quota V3. (R2) 사용자 사진 권한 노출 — owner-only 다운로드 검증 필수. (R3) 폼 닫혀도 업로드된 사진은 orphan — 24h cleanup. (R4) 편집 시 사진 삭제 누락 — 명시적 reconcile. (R5) 디스크 I/O 비용 — 사용자당 파일 시스템 I/O 작은 편이라 V1 허용. |
| **SUCCESS** | (SC-1) `/parse-photos` 응답에 photo_ids 포함, 디스크 저장 확인. (SC-2) entry 저장 시 photo_ids 가 entry 와 연결되어 timeline 에 표시. (SC-3) 다른 사용자의 photo 다운로드 시 403/404. (SC-4) DELETE entry 시 사진 파일도 함께 제거. (SC-5) 24h orphan 자동 정리. |
| **SCOPE** | IN: `journal_entry_photos` 모델, 디스크 저장 + 썸네일, 사진 4개 endpoint(POST/GET/DELETE 사진, parse-photos 수정), `/journal` POST/PATCH/DELETE 사진 reconcile, FE PhotoInput 응답 photo_ids 처리, 폼 썸네일/추가/제거, 타임라인 갤러리, lightbox. OUT: 사용자 quota, EXIF strip 옵션, 사진 회전/편집, 사진 OCR 검색, 사진 공유 링크. |

---

## 1. Scope

### 1.1 In Scope

- [ ] BE 모델 `JournalEntryPhoto` (entry FK nullable 로 orphan 표현, user_id, file_path, thumb_path, mime, size, width, height, created_at)
- [ ] BE 유틸 `core/photo_storage.py` (저장 경로 생성, 썸네일 생성, 삭제)
- [ ] BE API:
  - `POST /journal/parse-photos` 수정 — 분석 + 디스크 저장, 응답에 `photo_ids: list[int]` 포함
  - `POST /journal/photos` (신규) — 사진만 업로드 (분석 없음, 폼 "사진 추가"용)
  - `GET /journal/photos/{id}` (신규) — 다운로드, `?thumb=1` 쿼리, 인증된 소유자 검증
  - `DELETE /journal/photos/{id}` (신규) — 명시적 삭제 (× 버튼)
  - `POST /journal` 수정 — body 에 `photo_ids` 받아 entry 와 연결
  - `PATCH /journal/{id}` 수정 — `photo_ids` 변경 시 reconcile
  - `DELETE /journal/{id}` — cascade 삭제
- [ ] BE Orphan cleanup — `init_db()` 마지막 단계에서 24h 경과 entry_id=null 사진 제거 (boot-time 실행 + 가벼운 연산)
- [ ] FE `useJournalData`:
  - `parsePhotos` 응답 타입 확장 (`photo_ids`)
  - `uploadPhoto`, `deletePhoto` 메서드 추가
  - `createEntry` / `updateEntry` body 에 `photo_ids` 포함
- [ ] FE `JournalEntryForm` 수정 — 첨부 사진 섹션 (썸네일 그리드 + × 제거 + "사진 추가" 버튼)
- [ ] FE `JournalPage` 타임라인 카드 펼침 시 썸네일 갤러리 + lightbox 모달
- [ ] FE 신규 `PhotoLightbox.tsx` 컴포넌트 — 클릭 시 원본 사진 전체화면

### 1.2 Out of Scope

- 사용자별 디스크 quota
- EXIF strip 옵션 (V2 — 위치정보 제거)
- 사진 회전·자르기·필터 등 편집 기능
- 사진 OCR 기반 검색
- 사진 공유 링크/공개 URL
- PDF 출력에 사진 첨부 (별도 feature)

---

## 2. Requirements

### 2.1 Functional

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `/parse-photos` 호출 시 모든 사진이 `data/uploads/journal/{user_id}/{uuid}.jpg` 저장됨 | High |
| FR-02 | 동시에 썸네일이 `{uuid}_thumb.jpg` 로 생성됨 (긴 변 ≤ 1280px) | High |
| FR-03 | DB `journal_entry_photos` 행이 entry_id=null 로 생성됨 | High |
| FR-04 | `/parse-photos` 응답에 `photo_ids: list[int]` 포함 | High |
| FR-05 | `POST /journal` body 에 `photo_ids` 포함 시 해당 사진 entry_id 가 갱신 (소유자 검증) | High |
| FR-06 | `GET /journal/photos/{id}` 가 owner 만 다운로드, 다른 사용자에겐 403 | High |
| FR-07 | `?thumb=1` 쿼리로 썸네일 반환 | High |
| FR-08 | `DELETE /journal/photos/{id}` 가 DB 행과 디스크 파일 모두 제거 | High |
| FR-09 | `DELETE /journal/{id}` 가 연결된 사진 cascade 제거 | High |
| FR-10 | `PATCH /journal/{id}` body 의 `photo_ids` 가 추가/제거 모두 반영 | High |
| FR-11 | 폼에 첨부 사진 썸네일 그리드 + × 제거 + "사진 추가" 버튼 | High |
| FR-12 | 타임라인 카드 펼침 시 썸네일 갤러리 표시, 클릭 시 lightbox | High |
| FR-13 | 24h 이상 경과한 entry_id=null 사진은 boot-time orphan cleanup 으로 제거 | Medium |
| FR-14 | 같은 entry 에 N(>1)장 첨부 가능 | High |
| FR-15 | STT/text/auto source 도 폼에서 사진 첨부 가능 (vision 전용 X) | High |
| FR-16 | "사진 추가" 버튼은 분석 없이 단순 업로드 (POST /journal/photos) | High |

### 2.2 Non-Functional

| Category | Criteria |
|----------|----------|
| Performance | 사진 1장 저장 + 썸네일 < 1s. 사진 다운로드(<2MB) < 200ms |
| Security | owner-only 다운로드. DELETE 시 owner 검증. mime 검증 (image/*) |
| Reliability | 디스크 쓰기 실패 시 DB 행 생성 안 함 (transaction-like) |
| UX | 폼 사진 추가 즉시 썸네일 미리보기 |
| Backward Compat | 기존 entry (사진 없음) 영향 0 — `photos` 가 빈 리스트 |

---

## 3. Success Criteria

### 3.1 Definition of Done

- [ ] FR-01~FR-16 (High) 모두 구현
- [ ] 시연: 사진 업로드 → 분석 → 폼 썸네일 → 저장 → 타임라인 갤러리 → lightbox 풀 흐름
- [ ] 다른 사용자가 photo 다운로드 시도 → 403 검증
- [ ] DELETE entry 시 디스크 파일도 사라짐 검증
- [ ] BE/FE 빌드·린트 0 에러
- [ ] design 문서 작성

### 3.2 Quality

- [ ] BE unit: photo_storage 저장/삭제, owner 검증
- [ ] 수동 시연 1분 내

---

## 4. Risks and Mitigation

| Risk | Mitigation |
|------|-----------|
| 디스크 누수 (orphan) | 24h boot-time cleanup. parse-photos 후 폼 닫혀도 자동 청소. |
| 사진 권한 우회 | `GET /journal/photos/{id}` 마다 owner 검증. UUID 만으로 추측 불가. |
| 디스크 쓰기 실패 → DB 비일관 | 저장 후 commit 순서, 실패 시 디스크 파일 cleanup |
| 편집 시 사진 삭제 누락 | PATCH 에서 명시적 reconcile (DB diff 후 디스크 cleanup) |
| 모바일 큰 사진 OOM | FE 다운샘플 이미 1280px 제한, BE max 5MB 검증 |

---

## 5. Impact Analysis

### 5.1 Changed Resources

| Resource | Type | Change |
|----------|------|--------|
| `backend/app/models/journal.py` | 영농일지 전용 | `JournalEntryPhoto` 클래스 + `JournalEntry.photos` relationship 추가 |
| `backend/app/core/photo_storage.py` | 신규 | 디스크 저장/삭제/썸네일 |
| `backend/app/schemas/journal.py` | 영농일지 전용 | `JournalEntryCreate.photo_ids: list[int] \| None`, `JournalEntryPhotoResponse` 등 추가 |
| `backend/app/api/journal.py` | 영농일지 전용 | parse-photos 수정 + 3개 endpoint 신규 |
| `backend/app/core/journal_store.py` | 영농일지 전용 | create/update/delete entry 에 photos 연결 로직 |
| `backend/app/core/database.py` | 공유 (init_db) | **수정 안 함** — Base.metadata.create_all 만으로 신규 테이블 생성됨. orphan cleanup 은 별도 함수로 추가 가능 |
| `frontend/src/types/index.ts` | 공유 type | `JournalEntryAPI.photos: PhotoSummary[]` 추가 (옵셔널) |
| `frontend/src/hooks/useJournalData.ts` | 영농일지 전용 | `parsePhotos` 응답 타입 + `uploadPhoto` / `deletePhoto` 추가 |
| `frontend/src/modules/journal/PhotoInput.tsx` | 영농일지 전용 | onResult 에 photo_ids 전달 |
| `frontend/src/modules/journal/JournalEntryForm.tsx` | 영농일지 전용 | 첨부 사진 섹션 추가 |
| `frontend/src/modules/journal/JournalPage.tsx` | 영농일지 전용 | 카드 펼침 시 갤러리 + lightbox 호출 |
| `frontend/src/modules/journal/PhotoLightbox.tsx` | 신규 | 풀스크린 사진 모달 |

### 5.2 File Modification Policy

- 공유 핫스팟 (`config.py`, `database.py`) 미수정. 새 모델은 Base.metadata 로 자동 테이블 생성.
- 영농일지 전용 파일들은 자유롭게 수정 (모두 본 feature 의 owner 영역).
- 디스크 경로는 기존 `UPLOAD_BASE_DIR` 재사용.

---

## 6. Architecture Decisions

| Decision | Selected | Rationale |
|----------|----------|-----------|
| DB 구조 | 별도 `journal_entry_photos` 테이블 | 1:N 관계, 독립 쿼리(전체 갤러리), 컬럼 추가 자유 |
| 저장 시점 | parse-photos 호출 즉시 (orphan 24h 청소) | 사용자 편집 동안 두 번 fetch 회피 |
| 사진 형식 | 원본 + 1280×720 썸네일, 모두 JPEG | 디스크/대역폭 절약 |
| 거절 확정 후 빈 폼 | 첨부된 사진 photo_ids 도 폼 state 에 보관 | 사용자가 사진 첨부 의도 살림 |
| Source 무관 첨부 | 모든 source(stt/text/vision/auto) 에서 가능 | entry-level 첨부 기능으로 일반화 |
| Cleanup 시점 | boot-time (init_db 끝) | 별도 worker 도입 비용 회피, 24h 늦은 청소 허용 |

---

## 7. Next Steps

1. [ ] Design 문서 작성 (API/DB 상세, 모듈 인터페이스)
2. [ ] BE 모델 + 마이그레이션 (Base.metadata 자동)
3. [ ] BE photo_storage 유틸
4. [ ] BE API endpoints
5. [ ] BE orphan cleanup
6. [ ] BE 검증 (curl/직접 호출)
7. [ ] FE photo client + 폼 통합
8. [ ] FE 타임라인 갤러리 + lightbox
9. [ ] E2E 시나리오 (저장→표시→삭제→cascade)
10. [ ] Analysis 문서

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial draft (별도 테이블 + 사진 즉시 저장 + orphan cleanup) | JunePark2018 |

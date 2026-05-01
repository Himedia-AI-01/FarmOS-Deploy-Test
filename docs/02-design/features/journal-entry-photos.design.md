# Journal Entry Photos Design Document

> **Summary**: `journal_entry_photos` 테이블이 entry 와 N:1 관계. `/parse-photos` 가 분석과 동시에 사진을 `data/uploads/journal/{user_id}/{uuid}.jpg` + 썸네일 1280×720 으로 저장하고 photo_ids 를 반환. 폼/타임라인이 photo_ids 를 통해 사진을 표시. 4개 신규/수정 endpoint, 보안은 owner 검증으로 통일. orphan 사진은 boot-time 24h cleanup.
>
> **Project**: FarmOS - journal-entry-photos
> **Version**: 0.1.0
> **Author**: JunePark2018
> **Date**: 2026-04-28
> **Planning Doc**: [journal-entry-photos.plan.md](../../01-plan/features/journal-entry-photos.plan.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | Vision 일지의 출처 추적 + STT/text 일지 사진 첨부 + 영농 증빙 보존. |
| **WHO** | 농부, 멘토/평가자, 향후 RAG 데이터. |
| **RISK** | 디스크 누수(orphan cleanup), 권한 우회(owner 검증), DB-디스크 불일치(transactional save) |
| **SUCCESS** | SC-1~5 (plan 참조) |

---

## 1. DB Schema

### 1.1 `journal_entry_photos`

```python
class JournalEntryPhoto(Base):
    __tablename__ = "journal_entry_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("users.id"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(255), default=None)
    original_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    mime_type: Mapped[str] = mapped_column(String(50), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,  # orphan cleanup 가속
    )
```

### 1.2 `JournalEntry` 관계 추가

```python
# JournalEntry 안에 추가
photos: Mapped[list["JournalEntryPhoto"]] = relationship(
    back_populates="entry",
    cascade="all, delete-orphan",
    lazy="selectin",  # entry 조회 시 자동 join
)

# JournalEntryPhoto 안에 추가
entry: Mapped["JournalEntry | None"] = relationship(back_populates="photos")
```

### 1.3 인덱스

- `entry_id` — entry 조회 시 join
- `user_id` — owner 검증 + 사용자별 갤러리(V2)
- `created_at` — orphan cleanup 의 `WHERE created_at < now-24h` 가속

---

## 2. Storage Layout

```text
data/uploads/journal/
└── {user_id}/
    ├── {uuid}.jpg          # 원본 (FE 다운샘플 후 도착, BE 추가 처리 없음)
    └── {uuid}_thumb.jpg    # 썸네일 (긴 변 ≤ 1280px, JPEG q=82)
```

- `user_id` 디렉터리는 자동 생성
- `uuid` 는 `uuid.uuid4().hex` (32자), 추측 불가
- 모든 사진은 JPEG 으로 저장 (PNG/HEIC 입력은 Pillow open 후 JPEG 변환)
- DB 의 `file_path`/`thumb_path` 는 `UPLOAD_BASE_DIR` 기준 상대 경로

---

## 3. API Specification

### 3.1 `POST /journal/parse-photos` (수정)

기존: 분석만 → 응답 entries.

**변경**:
- 사진을 디스크에 저장 (entry_id=null)
- 응답에 `photo_ids: list[int]` 추가

```json
{
  "entries": [...],
  "used_exif": true,
  "image_count": 2,
  "photo_ids": [123, 124],   // 신규
  "rejected": false
}
```

**거절 케이스도 photo_ids 반환**:
- 사용자가 "그래도 작성"으로 빈 폼에 진행해도 사진은 살림
- 폼 닫으면 24h 후 cleanup

### 3.2 `POST /journal/photos` (신규)

분석 없이 사진만 업로드 — 폼의 "사진 추가" 버튼용.

**Request**: multipart `file` (단일), 옵션 `entry_id`(즉시 연결 시).

**Response**:
```json
{
  "photo_id": 125,
  "thumb_url": "/journal/photos/125?thumb=1",
  "url": "/journal/photos/125",
  "width": 1280,
  "height": 960
}
```

### 3.3 `GET /journal/photos/{id}` (신규)

**Query**: `?thumb=1` 시 썸네일, 없으면 원본.

**Auth**: 로그인 필수. `photo.user_id == current_user.id` 검증, 불일치 시 404 (403 노출 회피).

**Response**: `image/jpeg` binary (FastAPI `FileResponse`).

### 3.4 `DELETE /journal/photos/{id}` (신규)

owner 검증 후 DB 행 삭제 + 디스크 파일 unlink (원본 + 썸네일).

**Response**: 204 No Content.

### 3.5 `POST /journal` (수정)

**Body 추가**:
```json
{
  ... 기존 필드,
  "photo_ids": [123, 124]
}
```

**처리**:
- entry 생성 후 photo_ids 의 사진들에 대해 `entry_id` 갱신
- 각 사진의 `user_id == current_user.id` 검증, 불일치 시 무시(skip) — 권한 검증

### 3.6 `PATCH /journal/{id}` (수정)

**Body 추가**: `photo_ids: list[int] | None`

**처리** (None 이 아니면):
- 현재 entry.photos 와 새 photo_ids 비교
- 없어진 사진: DELETE (DB + 디스크)
- 추가된 사진: entry_id 갱신 (owner 검증)

### 3.7 `DELETE /journal/{id}` (cascade)

ORM `cascade="all, delete-orphan"` 으로 DB 자동 cascade. 디스크 파일은 별도 정리 — `journal_store.delete_entry` 에서 photos 미리 fetch → unlink → entry delete 순.

---

## 4. Module Specification

### 4.1 `core/photo_storage.py` (신규)

```python
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.core.config import settings


_THUMB_MAX_SIDE = 1280
_THUMB_QUALITY = 82


def _user_dir(user_id: str) -> Path:
    base = Path(settings.UPLOAD_BASE_DIR) / "journal" / user_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_photo(user_id: str, image_bytes: bytes) -> dict:
    """원본+썸네일 디스크 저장. DB 행은 호출자가 별도 생성.

    Returns: {file_path, thumb_path, width, height, mime_type, size_bytes}
    file_path / thumb_path 는 UPLOAD_BASE_DIR 기준 상대 경로.
    """
    udir = _user_dir(user_id)
    uid = uuid.uuid4().hex
    rel_orig = f"journal/{user_id}/{uid}.jpg"
    rel_thumb = f"journal/{user_id}/{uid}_thumb.jpg"

    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    # 원본 저장 (입력이 JPEG 이 아니어도 통일)
    orig_path = udir / f"{uid}.jpg"
    img.save(orig_path, format="JPEG", quality=92)
    width, height = img.size

    # 썸네일
    img.thumbnail((_THUMB_MAX_SIDE, _THUMB_MAX_SIDE))
    thumb_path = udir / f"{uid}_thumb.jpg"
    img.save(thumb_path, format="JPEG", quality=_THUMB_QUALITY)

    return {
        "file_path": rel_orig,
        "thumb_path": rel_thumb,
        "width": width,
        "height": height,
        "mime_type": "image/jpeg",
        "size_bytes": orig_path.stat().st_size,
    }


def absolute_path(rel_path: str) -> Path:
    return Path(settings.UPLOAD_BASE_DIR) / rel_path


def delete_photo_files(file_path: str | None, thumb_path: str | None) -> None:
    for rel in (file_path, thumb_path):
        if not rel:
            continue
        try:
            absolute_path(rel).unlink(missing_ok=True)
        except OSError:
            pass  # 디스크 청소 실패 무시 (orphan cleanup 으로 후속 처리 가능)


async def cleanup_orphans(db, older_than_hours: int = 24) -> int:
    """24h 이상 entry_id=null 인 사진 제거. 호출자: init_db()."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select

    from app.models.journal import JournalEntryPhoto

    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    rows = (await db.execute(
        select(JournalEntryPhoto)
        .where(JournalEntryPhoto.entry_id.is_(None))
        .where(JournalEntryPhoto.created_at < cutoff)
    )).scalars().all()

    for r in rows:
        delete_photo_files(r.file_path, r.thumb_path)
        await db.delete(r)
    await db.commit()
    return len(rows)
```

### 4.2 `core/journal_store.py` 수정

**create_entry**:
- entry 생성 후 `if photo_ids: 해당 photos 의 entry_id 갱신` (owner 검증 with `user_id == entry.user_id`)

**update_entry**:
- photo_ids 가 명시되면:
  - 현재 entry.photos id set 과 비교
  - 빠진 사진: `delete_photo_files` + DB delete
  - 추가된 사진: 해당 photos 의 entry_id 갱신 (owner 검증)

**delete_entry**:
- entry.photos 의 file 모두 disk 에서 unlink → entry delete (cascade 로 DB 행 자동 삭제)

### 4.3 `api/journal.py` 변경 요약

```python
# parse-photos 마지막에 사진 저장 추가
saved_ids: list[int] = []
for img_bytes in images:
    meta = save_photo(current_user.id, img_bytes)
    photo = JournalEntryPhoto(user_id=current_user.id, **meta)
    db.add(photo)
    await db.flush()
    saved_ids.append(photo.id)
await db.commit()
result["photo_ids"] = saved_ids

# /journal/photos POST/GET/DELETE — 별도 라우트 추가
# /journal POST/PATCH — body 의 photo_ids 처리
```

### 4.4 `core/database.py` 영향 — **없음**

- 신규 모델은 `Base.metadata.create_all` 로 자동 테이블 생성 → init_db 수정 불필요
- orphan cleanup 은 init_db 마지막에 한 줄 추가하는 게 자연스럽지만, **공유 파일 수정 회피 원칙** 에 따라 `app/main.py` lifespan startup 에 등록 (또는 `journal.py` 모듈 import 시 한 번 호출).
  - 더 깔끔: `app/main.py` lifespan 에 한 줄 — 본 feature 가 있는 동안만 효과적
  - 또는 cleanup 자체를 별도 endpoint(`POST /journal/photos/cleanup`) 로 노출하고 cron 으로 호출 — V1 에선 boot-time 으로 충분

**선택**: `app/main.py` lifespan startup 에 cleanup 한 번 호출. main.py 도 공유지만 lifespan hook 추가는 1~2줄로 작아 conflict 위험 낮음.

### 4.5 FE 컴포넌트

**`PhotoInput.tsx`**:
- onResult 콜백에 photo_ids 전달 (응답 타입 확장)

**`JournalEntryForm.tsx`** — 새 섹션:
```tsx
<section className="border-t pt-4">
  <h4 className="text-sm font-medium">첨부 사진 ({photoIds.length})</h4>
  <div className="grid grid-cols-3 gap-2 mt-2">
    {photoIds.map(id => (
      <div key={id} className="relative aspect-square">
        <img src={`${API_BASE}/journal/photos/${id}?thumb=1`}
             className="w-full h-full object-cover rounded" />
        <button onClick={() => handleRemove(id)}
                className="absolute top-1 right-1 bg-red-500 text-white rounded-full">×</button>
      </div>
    ))}
    <label className="aspect-square border-2 border-dashed rounded flex items-center justify-center cursor-pointer">
      + 사진 추가
      <input type="file" accept="image/*" hidden onChange={handleAdd} />
    </label>
  </div>
</section>
```

- `handleAdd`: 다운샘플 후 `POST /journal/photos` → 응답 photo_id 추가
- `handleRemove`: state 에서 제거 (저장 시 PATCH 가 reconcile)

**`JournalPage.tsx` 카드 펼침**:
```tsx
{entry.photos && entry.photos.length > 0 && (
  <div className="grid grid-cols-4 gap-2 mt-3">
    {entry.photos.map(p => (
      <img key={p.id}
           src={`${API_BASE}/journal/photos/${p.id}?thumb=1`}
           onClick={() => setLightbox(p.id)}
           className="aspect-square object-cover rounded cursor-pointer hover:opacity-80" />
    ))}
  </div>
)}
```

**`PhotoLightbox.tsx`** (신규):
- fullscreen modal, 클릭 시 닫힘, Esc 닫힘
- `<img src=`${API_BASE}/journal/photos/${id}` />` 로 원본 표시

---

## 5. Sequence — 사진 첨부 영농일지 작성 (사진 입력 → 저장)

```text
사용자                FE                        BE                          Disk            DB
  │                   │                          │                            │              │
  │ 사진 N장 선택 ───>│                          │                            │              │
  │                   │ 다운샘플 1280px          │                            │              │
  │                   │ POST /parse-photos ────> │                            │              │
  │                   │                          │ save_photo(N장) ─────────> │ 원본+썸네일   │
  │                   │                          │                            │              │
  │                   │                          │ INSERT journal_entry_photo ─────────────> │
  │                   │                          │  (entry_id=null × N)        │              │
  │                   │                          │                            │              │
  │                   │                          │ LLM 분석 (gpt-5-mini)       │              │
  │                   │                          │                            │              │
  │                   │ <──── {entries, photo_ids:[..]} ───────────────────── │              │
  │                   │ 폼 prefill (사진 썸네일 표시)                         │              │
  │                   │                          │                            │              │
  │ × 또는 사진 추가  │                          │                            │              │
  │ ── 편집 ─────────>│                          │                            │              │
  │ "저장" ──────────>│ POST /journal {... photo_ids} ───>                    │              │
  │                   │                          │ entry 생성                  │              │
  │                   │                          │ UPDATE photos.entry_id ─────────────────> │
  │                   │                          │ <── entry ────             │              │
  │                   │ 타임라인 갱신             │                            │              │
```

---

## 6. Error Handling

| Layer | Error | Handling |
|-------|-------|----------|
| Storage | 디스크 쓰기 실패 | DB 행 생성 안 함 (raise → 502) |
| Storage | mime 검증 실패 | 400 |
| API | 다른 user 의 photo_id 요청 | 404 (403 노출 회피) |
| API | 존재하지 않는 photo_id | 404 |
| API | photo_ids 에 다른 사용자 사진 섞임 | 해당만 skip (오류 X) |
| FE | 사진 다운로드 실패 | placeholder 이미지 |
| FE | "사진 추가" 업로드 실패 | toast.error |

---

## 7. Test Plan

### 7.1 BE Unit / Integration

- `save_photo`: 정상 JPEG, PNG → JPEG 변환, 손상 입력 → 예외
- `cleanup_orphans`: 24h 이전 entry_id=null만 제거, 25h 이전이지만 entry_id 있으면 보존
- `delete_entry` cascade: 디스크 파일도 unlink 검증
- owner 검증: 다른 user 의 photo_id 다운로드/삭제 → 404
- `PATCH /journal/{id}` photo_ids reconcile: 추가/제거/유지 모두 검증

### 7.2 E2E (수동 + preview)

| 시나리오 | 절차 | 기대 |
|----------|------|------|
| S1 | 농약 라벨 사진 1장 → 분석 → 저장 → 펼침 | 썸네일 1개, 클릭 시 lightbox |
| S2 | 사진 3장 → 분석 → 폼에서 1장 × → 저장 | 타임라인 카드에 사진 2장 |
| S3 | text 입력 → "사진 추가" 1장 → 저장 | text 일지에도 사진 1장 표시 |
| S4 | 다른 사용자 photo_id 다운로드 시도 | 404 |
| S5 | 일지 DELETE | DB 행 + 디스크 파일 모두 사라짐 |
| S6 | parse-photos 후 폼 닫기 + 24h 시뮬레이션 → cleanup | orphan 사진 제거됨 |
| S7 | 편집 시 사진 ×, 추가 후 저장 | 정확히 reconcile |

---

## 8. Open Issues / Future

| ID | Issue | Target |
|----|-------|--------|
| Q1 | 사용자별 디스크 quota | V3 |
| Q2 | EXIF strip 옵션 (GPS 비저장) | V2 |
| Q3 | 사진 회전·자르기·밝기 조정 | V2 |
| Q4 | 사진 OCR 기반 검색 | V3 |
| Q5 | PDF 출력에 사진 첨부 | V2 |

---

## 9. Implementation Order

1. **BE** model + ORM relationship
2. **BE** photo_storage.py
3. **BE** schemas/journal.py — `JournalEntryPhotoResponse`, `JournalEntryCreate.photo_ids`
4. **BE** journal_store.py — create/update/delete photos 연결
5. **BE** api/journal.py — parse-photos 수정 + 3개 endpoint
6. **BE** main.py lifespan — orphan cleanup
7. **BE** 검증 (curl + 직접 호출)
8. **FE** types + useJournalData
9. **FE** PhotoInput onResult 확장
10. **FE** JournalEntryForm 첨부 섹션
11. **FE** PhotoLightbox 컴포넌트
12. **FE** JournalPage 카드 갤러리
13. **E2E** S1~S7 시나리오
14. Analysis 문서

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial draft (별도 테이블, 즉시 저장+orphan cleanup, owner-only download) | JunePark2018 |

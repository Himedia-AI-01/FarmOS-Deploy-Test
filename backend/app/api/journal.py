"""영농일지 API 라우터."""

import logging
from datetime import date

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core import journal_store
from app.core.exif_utils import extract_exif
from app.core.journal_parser import parse_stt_text
from app.core.journal_vision_parser import parse_photos as parse_photos_internal
from app.core.pesticide_candidates import build_whisper_prompt
from app.core.photo_storage import absolute_path, delete_photo_files, save_photo
from app.core.stt import transcribe_audio
from app.models.journal import JournalEntryPhoto
from app.models.user import User
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryUpdate,
    JournalEntryResponse,
    JournalEntryListResponse,
    STTParseRequest,
    STTParseResponse,
    DailySummaryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/journal", tags=["journal"])


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    field_name: str | None = Form(default=None),
    crop: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """오디오 파일을 Groq Whisper로 전사하여 텍스트 반환.

    field_name/crop이 있으면 농약 후보 기반 Whisper prompt를 생성해 전사 정확도를 높임.
    """
    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(400, "빈 오디오 파일입니다.")

        whisper_prompt: str | None = None
        try:
            built = await build_whisper_prompt(
                db, crop=crop, top_n=30, user_id=current_user.id
            )
            whisper_prompt = built or None
        except Exception:
            whisper_prompt = None  # 힌트 실패해도 전사는 진행

        text = await transcribe_audio(
            audio_bytes,
            filename=file.filename or "audio.webm",
            content_type=file.content_type or "audio/webm",
            prompt=whisper_prompt,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"STT 전사 실패: {e}")
    # 디버깅용 컨텍스트는 로그에만 (응답에는 포함 안 함)
    _ = field_name
    return {"text": text}


@router.post("/parse-stt", response_model=STTParseResponse)
async def parse_stt(
    body: STTParseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """STT 텍스트를 농업ON 구조화 데이터로 파싱 + 농약 DB 매칭.

    entries 배열 각각에 대해 퍼지 매칭 포함 보정을 수행.
    """
    try:
        result = await parse_stt_text(
            body.raw_text,
            field_name=body.field_name,
            crop=body.crop,
            db=db,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(502, f"LLM 연결 실패: {type(e).__name__}: {e}")

    # 각 entry에 대해 농약 매칭 후처리
    try:
        from app.core.pesticide_matcher import enrich_with_pesticide_match

        enriched_entries = []
        for entry in result.get("entries", []):
            try:
                enriched_entries.append(await enrich_with_pesticide_match(db, entry))
            except Exception:
                enriched_entries.append(entry)
        result["entries"] = enriched_entries
    except Exception:
        pass  # 매칭 실패해도 파싱 결과는 정상 반환
    return result


@router.post("/parse-photos")
async def parse_photos(
    files: list[UploadFile] = File(...),
    field_name: str | None = Form(default=None),
    crop: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """사진 1~N장을 Vision LLM으로 분석해 영농일지 entry 배열을 prefill 반환.

    `/parse-stt`와 동일한 entry shape `{entries: [{parsed, confidence}]}` 을 반환한다.
    동시에 모든 사진을 디스크에 저장하고 photo_ids 를 응답에 포함하여, 사용자가 폼에서
    저장 시 entry 와 연결할 수 있게 한다 (entry_id=null 임시 사진은 24h 후 cleanup).
    """
    if not files:
        raise HTTPException(400, "사진 1장 이상 업로드해주세요.")
    if len(files) > settings.JOURNAL_VISION_MAX_IMAGES:
        raise HTTPException(
            400,
            f"최대 {settings.JOURNAL_VISION_MAX_IMAGES}장까지 업로드 가능합니다.",
        )

    images: list[bytes] = []
    exif_hints = []
    for f in files:
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(400, f"이미지 파일만 업로드 가능합니다: {f.filename}")
        data = await f.read()
        if not data:
            raise HTTPException(400, f"빈 이미지 파일: {f.filename}")
        if len(data) > settings.JOURNAL_VISION_MAX_BYTES:
            limit_mb = settings.JOURNAL_VISION_MAX_BYTES // (1024 * 1024)
            raise HTTPException(413, f"사진 크기는 {limit_mb}MB 이하여야 합니다.")
        images.append(data)
        exif_hints.append(extract_exif(data))

    try:
        result = await parse_photos_internal(
            images=images,
            exif_hints=exif_hints,
            field_name=field_name,
            crop=crop,
            db=db,
        )
    except httpx.TimeoutException:
        # 알려진 흐름 — implicit traceback 은 디버깅 가치 낮아 차단.
        raise HTTPException(504, "Vision 분석 시간이 초과되었습니다.") from None
    except HTTPException:
        raise
    except Exception as e:
        # 원본 traceback 보존 — 서버 로그에서 진짜 원인 추적용.
        raise HTTPException(
            502, f"Vision 분석 실패: {type(e).__name__}: {e}"
        ) from e

    # 농약 매칭 후처리 (STT 경로와 동일)
    try:
        from app.core.pesticide_matcher import enrich_with_pesticide_match

        enriched_entries = []
        for entry in result.get("entries", []):
            try:
                enriched_entries.append(await enrich_with_pesticide_match(db, entry))
            except Exception as match_err:
                # 단일 entry 매칭 실패는 일상 흐름(미매칭 농약 등) — debug 레벨로 noise 회피.
                logger.debug(
                    "journal.parse_photos.pesticide_match_skipped err=%s", match_err
                )
                enriched_entries.append(entry)
        result["entries"] = enriched_entries
    except Exception as exc:
        # 모듈 로드 자체 실패 — 환경/배포 이슈일 수 있어 운영자가 인지하도록 warning.
        logger.warning(
            "journal.parse_photos.pesticide_matcher_unavailable err=%s", exc
        )

    # 사진 디스크 저장 + DB 행 생성 (entry_id=null 임시 사진)
    # strict=True: images/files 는 위에서 1:1 로 만들어졌으므로 항상 동일 길이 — 깨지면 즉시 실패.
    # DB flush/commit 실패 시 이미 디스크에 저장된 파일들이 영구 누수되지 않도록 경로를 추적.
    saved_disk_paths: list[tuple[str | None, str | None]] = []
    saved_ids: list[int] = []
    try:
        for img_bytes, f in zip(images, files, strict=True):
            try:
                meta = save_photo(current_user.id, img_bytes)
            except Exception as save_err:
                # 한 장 실패해도 나머지는 진행. silent 가 아니라 운영자가 인지하도록 warning.
                logger.warning(
                    "journal.parse_photos.save_photo_failed file=%s err=%s",
                    f.filename, save_err,
                )
                continue
            # 이 시점부터 디스크 파일이 존재 — DB 단계 실패 시 정리 대상에 포함.
            saved_disk_paths.append((meta["file_path"], meta["thumb_path"]))
            photo = JournalEntryPhoto(
                user_id=current_user.id,
                original_filename=f.filename,
                **meta,
            )
            db.add(photo)
            await db.flush()
            saved_ids.append(photo.id)
        if saved_ids:
            await db.commit()
    except Exception:
        # DB flush/commit 실패 — transaction rollback 후 디스크 파일 일괄 정리
        # (DB 에 row 가 없으니 24h orphan cleanup 으로도 회수 불가능한 영구 누수 차단).
        await db.rollback()
        for fp, tp in saved_disk_paths:
            delete_photo_files(fp, tp)
        logger.exception("journal.parse_photos.db_persist_failed")
        raise

    result["image_count"] = len(images)
    result["photo_ids"] = saved_ids
    return result


# ── 사진 첨부 endpoints ──────────────────────────────────────────────────


@router.post("/photos")
async def upload_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """분석 없이 사진만 업로드 — 폼의 "사진 추가" 버튼용. 응답으로 photo_id 반환."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 이미지 파일입니다.")
    if len(data) > settings.JOURNAL_VISION_MAX_BYTES:
        limit_mb = settings.JOURNAL_VISION_MAX_BYTES // (1024 * 1024)
        raise HTTPException(413, f"사진 크기는 {limit_mb}MB 이하여야 합니다.")

    try:
        meta = save_photo(current_user.id, data)
    except Exception as e:
        raise HTTPException(
            502, f"사진 저장 실패: {type(e).__name__}: {e}"
        ) from e

    photo = JournalEntryPhoto(
        user_id=current_user.id,
        original_filename=file.filename,
        **meta,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    return {
        "photo_id": photo.id,
        "width": photo.width,
        "height": photo.height,
        "size_bytes": photo.size_bytes,
    }


@router.get("/photos/{photo_id}")
async def download_photo(
    photo_id: int,
    thumb: int = Query(default=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """사진 다운로드. owner 만 접근 가능 (다른 사용자엔 404)."""
    photo = (
        await db.execute(
            select(JournalEntryPhoto).where(JournalEntryPhoto.id == photo_id)
        )
    ).scalar_one_or_none()
    if not photo or photo.user_id != current_user.id:
        # 권한 없음/존재 X 모두 404 (정보 노출 회피)
        raise HTTPException(404, "사진을 찾을 수 없습니다.")

    rel = photo.thumb_path if thumb and photo.thumb_path else photo.file_path
    abs_path = absolute_path(rel)
    if not abs_path.exists():
        raise HTTPException(404, "사진 파일이 디스크에 없습니다.")
    return FileResponse(abs_path, media_type=photo.mime_type or "image/jpeg")


@router.delete("/photos/{photo_id}", status_code=204)
async def delete_photo(
    photo_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """사진 명시적 삭제 (× 버튼). owner 만 가능."""
    photo = (
        await db.execute(
            select(JournalEntryPhoto).where(JournalEntryPhoto.id == photo_id)
        )
    ).scalar_one_or_none()
    if not photo or photo.user_id != current_user.id:
        raise HTTPException(404, "사진을 찾을 수 없습니다.")
    # commit 전에 unlink 하면 commit 실패 시 DB row 는 살아있는데 디스크 파일만 사라져
    # 이후 다운로드가 깨짐. 경로만 보관하고 commit 성공 후 unlink (journal_store 의
    # update_entry/delete_entry/cleanup_orphans 와 동일 패턴).
    file_path = photo.file_path
    thumb_path = photo.thumb_path
    await db.delete(photo)
    await db.commit()
    delete_photo_files(file_path, thumb_path)
    return None


@router.get("/daily-summary", response_model=DailySummaryResponse)
async def daily_summary(
    target_date: date = Query(..., alias="date"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """특정 날짜의 영농 요약 조회."""
    result = await journal_store.get_daily_summary(db, current_user.id, target_date)
    return result


@router.get("/missing-fields")
async def missing_fields(
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """기간 내 영농일지 누락 항목 조회."""
    entries, _ = await journal_store.list_entries(
        db,
        current_user.id,
        page=1,
        page_size=1000,
        date_from=date_from,
        date_to=date_to,
    )
    alerts = journal_store.check_missing_fields(entries)
    return {"missing_fields": alerts, "total": len(alerts)}


@router.get("/export-pdf")
async def export_pdf(
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 PDF 내보내기."""
    from fastapi.responses import Response
    from app.core.journal_pdf import generate_journal_pdf

    entries, _ = await journal_store.list_entries(
        db,
        current_user.id,
        page=1,
        page_size=1000,
        date_from=date_from,
        date_to=date_to,
    )
    pdf_bytes = generate_journal_pdf(
        entries,
        farm_name=current_user.farmname or current_user.name,
        date_from=date_from,
        date_to=date_to,
    )
    filename = f"journal_{date_from}_{date_to}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("", response_model=JournalEntryResponse, status_code=201)
async def create_journal_entry(
    body: JournalEntryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 생성."""
    entry = await journal_store.create_entry(db, current_user.id, body)
    return entry


@router.get("", response_model=JournalEntryListResponse)
async def list_journal_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    work_stage: str | None = None,
    crop: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 목록 조회 (필터 + 페이징)."""
    items, total = await journal_store.list_entries(
        db, current_user.id, page, page_size, date_from, date_to, work_stage, crop
    )
    return JournalEntryListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{entry_id}", response_model=JournalEntryResponse)
async def get_journal_entry(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 단건 조회."""
    entry = await journal_store.get_entry(db, current_user.id, entry_id)
    if not entry:
        raise HTTPException(404, "영농일지를 찾을 수 없습니다.")
    return entry


@router.patch("/{entry_id}", response_model=JournalEntryResponse)
async def update_journal_entry(
    entry_id: int,
    body: JournalEntryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 수정 (보낸 필드만 업데이트)."""
    entry = await journal_store.update_entry(db, current_user.id, entry_id, body)
    if not entry:
        raise HTTPException(404, "영농일지를 찾을 수 없습니다.")
    return entry


@router.delete("/{entry_id}")
async def delete_journal_entry(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """영농일지 삭제."""
    deleted = await journal_store.delete_entry(db, current_user.id, entry_id)
    if not deleted:
        raise HTTPException(404, "영농일지를 찾을 수 없습니다.")
    return {"message": "영농일지가 삭제되었습니다."}

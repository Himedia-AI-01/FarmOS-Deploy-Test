"""영농일지 사진 디스크 저장 유틸.

원본 + 썸네일을 `UPLOAD_BASE_DIR/journal/{user_id}/` 에 JPEG 으로 저장한다.
DB 행 생성/연결은 호출자(API/store) 책임. 본 모듈은 디스크 I/O 만 담당.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


_THUMB_MAX_SIDE = 1280
_THUMB_QUALITY = 82
_ORIG_QUALITY = 92


def _user_dir(user_id: str) -> Path:
    base = Path(settings.UPLOAD_BASE_DIR) / "journal" / user_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_photo(user_id: str, image_bytes: bytes) -> dict:
    """원본 + 썸네일 저장.

    Returns:
        {file_path, thumb_path, width, height, mime_type, size_bytes}
        — file_path / thumb_path 는 UPLOAD_BASE_DIR 기준 상대 경로.
    Raises:
        OSError, PIL.UnidentifiedImageError 등 — 호출자가 처리.
    """
    udir = _user_dir(user_id)
    uid = uuid.uuid4().hex
    rel_orig = f"journal/{user_id}/{uid}.jpg"
    rel_thumb = f"journal/{user_id}/{uid}_thumb.jpg"

    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    orig_path = udir / f"{uid}.jpg"
    thumb_path = udir / f"{uid}_thumb.jpg"

    # 원본 + 썸네일 저장을 한 단위로 묶어 부분 실패 시 생성된 파일을 즉시 정리.
    # DB row 가 만들어지기 전에 실패하면 orphan cleanup(entry_id=null 기준) 도 잡지 못해
    # 디스크에만 영구히 남는 누수가 발생하므로, 본 함수 안에서 rollback 책임을 진다.
    try:
        img.save(orig_path, format="JPEG", quality=_ORIG_QUALITY)
        width, height = img.size

        # 썸네일 — copy() 로 원본 size 영향 X
        thumb_img = img.copy()
        thumb_img.thumbnail((_THUMB_MAX_SIDE, _THUMB_MAX_SIDE))
        thumb_img.save(thumb_path, format="JPEG", quality=_THUMB_QUALITY)
    except Exception:
        for p in (orig_path, thumb_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return {
        "file_path": rel_orig,
        "thumb_path": rel_thumb,
        "width": width,
        "height": height,
        "mime_type": "image/jpeg",
        "size_bytes": orig_path.stat().st_size,
    }


def absolute_path(rel_path: str) -> Path:
    """상대 경로 → UPLOAD_BASE_DIR 기준 절대 경로."""
    return Path(settings.UPLOAD_BASE_DIR) / rel_path


def delete_photo_files(file_path: str | None, thumb_path: str | None) -> None:
    """디스크 파일 제거 (원본 + 썸네일). 실패해도 예외 안 던짐 — orphan cleanup 후속 처리."""
    for rel in (file_path, thumb_path):
        if not rel:
            continue
        try:
            absolute_path(rel).unlink(missing_ok=True)
        except OSError:
            pass


async def cleanup_orphans(db: AsyncSession, older_than_hours: int = 24) -> int:
    """`older_than_hours` 시간 이상 경과한 entry_id=null 인 사진 일괄 정리.

    boot-time 호출용. 짧은 시간 windowed 청소를 가정. 매우 많은 orphan 이 있어도
    한 번의 트랜잭션으로 처리되므로 V1 규모에서는 충분.

    트랜잭션 순서: 경로 수집 → DB delete → commit → (commit 성공 후) 디스크 unlink.
    commit 전에 unlink 하면 commit 실패 시 DB 에는 row 가 남아있는데 디스크 파일만
    사라져 정합성이 깨지므로 의도적으로 분리.

    Returns:
        삭제된 사진 수 (commit 성공 기준).
    """
    from app.models.journal import JournalEntryPhoto

    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    rows = (
        await db.execute(
            select(JournalEntryPhoto)
            .where(JournalEntryPhoto.entry_id.is_(None))
            .where(JournalEntryPhoto.created_at < cutoff)
        )
    ).scalars().all()

    if not rows:
        return 0

    paths_to_unlink = [(r.file_path, r.thumb_path) for r in rows]
    for r in rows:
        await db.delete(r)
    await db.commit()

    # commit 성공 후에만 디스크 정리. unlink 자체 실패는 후속 cleanup 또는 수동 작업.
    for fp, tp in paths_to_unlink:
        delete_photo_files(fp, tp)
    return len(rows)

"""영농일지 CRUD 저장소."""

from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.photo_storage import delete_photo_files
from app.models.journal import JournalEntry, JournalEntryPhoto
from app.schemas.journal import JournalEntryCreate, JournalEntryUpdate


async def _attach_photos(
    db: AsyncSession, user_id: str, entry_id: int, photo_ids: list[int]
) -> None:
    """photo_ids 의 사진 owner 검증 후 entry_id 갱신.

    다른 사용자 photo 가 섞여 있으면 해당만 무시 (오류 X).
    이미 다른 entry 와 연결된 photo 도 무시 (이중 연결 방지).

    호출자의 트랜잭션 안에서 동작 — 자체 commit 안 함. 호출자가 entry 생성/수정과
    함께 한 번에 commit 하여 원자성 보장.

    원자적 UPDATE 한 방으로 처리해 select→python 대입 사이의 race condition
    (동시 두 요청이 같은 photo_id 를 다른 entry 에 붙이려는 경우) 차단.
    DB 레벨 WHERE entry_id IS NULL 조건이 false 인 row 는 자동 제외.
    """
    if not photo_ids:
        return
    await db.execute(
        update(JournalEntryPhoto)
        .where(
            JournalEntryPhoto.id.in_(photo_ids),
            JournalEntryPhoto.user_id == user_id,
            JournalEntryPhoto.entry_id.is_(None),
        )
        .values(entry_id=entry_id)
    )


async def create_entry(
    db: AsyncSession, user_id: str, data: JournalEntryCreate
) -> JournalEntry:
    payload = data.model_dump()
    photo_ids = payload.pop("photo_ids", None) or []
    entry = JournalEntry(user_id=user_id, **payload)
    db.add(entry)
    # flush 로 entry.id 만 확보하고 commit 은 마지막에 한 번 — entry 생성과 사진
    # attach 를 한 트랜잭션으로 묶어 부분 성공(entry 만 생기고 photo 미연결) 차단.
    await db.flush()
    if photo_ids:
        await _attach_photos(db, user_id, entry.id, photo_ids)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_entry(
    db: AsyncSession, user_id: str, entry_id: int
) -> JournalEntry | None:
    result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_entries(
    db: AsyncSession,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    date_from: date | None = None,
    date_to: date | None = None,
    work_stage: str | None = None,
    crop: str | None = None,
) -> tuple[list[JournalEntry], int]:
    base = select(JournalEntry).where(JournalEntry.user_id == user_id)
    count_q = (
        select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.user_id == user_id)
    )

    if date_from:
        base = base.where(JournalEntry.work_date >= date_from)
        count_q = count_q.where(JournalEntry.work_date >= date_from)
    if date_to:
        base = base.where(JournalEntry.work_date <= date_to)
        count_q = count_q.where(JournalEntry.work_date <= date_to)
    if work_stage:
        base = base.where(JournalEntry.work_stage == work_stage)
        count_q = count_q.where(JournalEntry.work_stage == work_stage)
    if crop:
        base = base.where(JournalEntry.crop == crop)
        count_q = count_q.where(JournalEntry.crop == crop)

    total = (await db.execute(count_q)).scalar() or 0

    offset = (page - 1) * page_size
    query = base.order_by(JournalEntry.work_date.desc(), JournalEntry.id.desc())
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return items, total


async def update_entry(
    db: AsyncSession,
    user_id: str,
    entry_id: int,
    data: JournalEntryUpdate,
) -> JournalEntry | None:
    entry = await get_entry(db, user_id, entry_id)
    if not entry:
        return None

    updates = data.model_dump(exclude_unset=True)
    new_photo_ids = updates.pop("photo_ids", None)

    for field, value in updates.items():
        setattr(entry, field, value)
    entry.updated_at = datetime.now(timezone.utc)

    # reconcile 로 디스크에서 지울 경로들. commit 성공 후에 unlink 해야
    # commit 실패 시 DB↔디스크 영속 상태 불일치(파일만 사라지는 케이스) 회피.
    paths_to_unlink: list[tuple[str | None, str | None]] = []

    if new_photo_ids is not None:
        # reconcile — 빠진 사진은 DB delete + 경로 수집, 새 사진은 attach
        current = (
            await db.execute(
                select(JournalEntryPhoto).where(
                    JournalEntryPhoto.entry_id == entry_id
                )
            )
        ).scalars().all()
        new_set = set(new_photo_ids)
        for p in current:
            if p.id not in new_set:
                paths_to_unlink.append((p.file_path, p.thumb_path))
                await db.delete(p)
        added = [pid for pid in new_photo_ids if pid not in {p.id for p in current}]
        if added:
            await _attach_photos(db, user_id, entry_id, added)

    await db.commit()
    await db.refresh(entry)

    # commit 성공 후에만 디스크 파일 unlink (실패 시 orphan 은 후속 cleanup 으로 처리 가능)
    for fp, tp in paths_to_unlink:
        delete_photo_files(fp, tp)

    return entry


async def delete_entry(db: AsyncSession, user_id: str, entry_id: int) -> bool:
    entry = await get_entry(db, user_id, entry_id)
    if not entry:
        return False
    # 디스크에서 지울 경로를 먼저 수집만 하고, commit 성공 후에 unlink.
    # commit 전에 unlink 하면 DB rollback 시 entry/사진 row 는 살아있는데
    # 디스크 파일만 사라져 이후 다운로드가 깨짐 (영속 상태 불일치).
    photos = (
        await db.execute(
            select(JournalEntryPhoto).where(JournalEntryPhoto.entry_id == entry_id)
        )
    ).scalars().all()
    paths_to_unlink = [(p.file_path, p.thumb_path) for p in photos]

    await db.delete(entry)
    await db.commit()

    # commit 성공 후 디스크 정리 — 실패 시 orphan 파일은 후속 cleanup 작업으로 회수
    for fp, tp in paths_to_unlink:
        delete_photo_files(fp, tp)
    return True


# ── 누락 항목 체크 ──


def check_missing_fields(entries: list[JournalEntry]) -> list[dict]:
    """영농일지 목록에서 누락 필드를 검사.

    Returns:
        [{"entry_id": int, "field_name": str, "message": str,
          "work_date": str, "crop": str, "created_at": str}, ...]
    """
    alerts: list[dict] = []

    for entry in entries:
        eid = entry.id
        entry_meta = {
            "work_date": entry.work_date.isoformat() if entry.work_date else None,
            "crop": entry.crop,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }

        # 작물관리인데 농약/비료 사용 정보가 없는 경우
        if entry.work_stage == "작물관리":
            if not entry.usage_pesticide_product and not entry.usage_fertilizer_product:
                alerts.append(
                    {
                        "entry_id": eid,
                        "field_name": "usage_pesticide_product / usage_fertilizer_product",
                        "message": "작물관리 작업에 농약 또는 비료 사용 정보가 없습니다.",
                        **entry_meta,
                    }
                )

        # 농약 제품명은 있는데 사용량이 없는 경우
        if entry.usage_pesticide_product and not entry.usage_pesticide_amount:
            alerts.append(
                {
                    "entry_id": eid,
                    "field_name": "usage_pesticide_amount",
                    "message": f"농약 '{entry.usage_pesticide_product}'의 사용량이 누락되었습니다.",
                    **entry_meta,
                }
            )

        # 비료 제품명은 있는데 사용량이 없는 경우
        if entry.usage_fertilizer_product and not entry.usage_fertilizer_amount:
            alerts.append(
                {
                    "entry_id": eid,
                    "field_name": "usage_fertilizer_amount",
                    "message": f"비료 '{entry.usage_fertilizer_product}'의 사용량이 누락되었습니다.",
                    **entry_meta,
                }
            )

        # 농약 구입 종류는 있는데 제품명/구입량이 없는 경우
        if entry.purchase_pesticide_type:
            if not entry.purchase_pesticide_product:
                alerts.append(
                    {
                        "entry_id": eid,
                        "field_name": "purchase_pesticide_product",
                        "message": "농약 구입 제품명이 누락되었습니다.",
                        **entry_meta,
                    }
                )
            if not entry.purchase_pesticide_amount:
                alerts.append(
                    {
                        "entry_id": eid,
                        "field_name": "purchase_pesticide_amount",
                        "message": "농약 구입량이 누락되었습니다.",
                        **entry_meta,
                    }
                )

        # 비료 구입 종류는 있는데 제품명/구입량이 없는 경우
        if entry.purchase_fertilizer_type:
            if not entry.purchase_fertilizer_product:
                alerts.append(
                    {
                        "entry_id": eid,
                        "field_name": "purchase_fertilizer_product",
                        "message": "비료 구입 제품명이 누락되었습니다.",
                        **entry_meta,
                    }
                )
            if not entry.purchase_fertilizer_amount:
                alerts.append(
                    {
                        "entry_id": eid,
                        "field_name": "purchase_fertilizer_amount",
                        "message": "비료 구입량이 누락되었습니다.",
                        **entry_meta,
                    }
                )

    return alerts


# ── 일일 요약 ──


def _generate_summary_template(
    entries: list[JournalEntry],
    target_date: date,
    crops: list[str],
    stages: list[str],
    weather: str | None,
    missing: list[dict],
) -> str:
    """템플릿 기반 요약문 (LLM 폴백용)."""
    date_str = target_date.strftime("%Y년 %m월 %d일")
    parts = [f"{date_str} 영농보고서:"]
    parts.append(f"총 {len(entries)}건의 작업을 기록했습니다.")
    if crops:
        parts.append(f"작목: {', '.join(crops)}.")
    for stage in stages:
        count = sum(1 for e in entries if e.work_stage == stage)
        parts.append(f"{stage} {count}건.")
    if weather:
        parts.append(f"날씨: {weather}.")
    if missing:
        parts.append(f"누락 항목 {len(missing)}건이 있습니다. 확인이 필요합니다.")
    return " ".join(parts)


async def _generate_summary_llm(
    entries: list[JournalEntry],
    target_date: date,
    weather: str | None,
    missing: list[dict],
) -> str | None:
    """OpenRouter LLM으로 자연스러운 요약문 생성. 실패 시 None 반환."""
    # 일지 데이터를 텍스트로 정리
    entry_lines = []
    for e in entries:
        line = f"- {e.crop} / {e.field_name} / {e.work_stage}"
        if e.usage_pesticide_product:
            line += f" / 농약: {e.usage_pesticide_product}"
            if e.usage_pesticide_amount:
                line += f" {e.usage_pesticide_amount}"
        if e.usage_fertilizer_product:
            line += f" / 비료: {e.usage_fertilizer_product}"
            if e.usage_fertilizer_amount:
                line += f" {e.usage_fertilizer_amount}"
        if e.detail:
            line += f" / {e.detail}"
        entry_lines.append(line)

    date_str = target_date.strftime("%Y년 %m월 %d일")
    prompt = f"""다음은 {date_str}의 영농일지 기록입니다.

{chr(10).join(entry_lines)}

날씨: {weather or "기록 없음"}
누락 항목: {len(missing)}건

위 내용을 바탕으로 '오늘의 영농보고서'를 3~5문장의 자연스러운 한국어로 작성하세요.
농부가 하루를 마치고 읽기 좋은 보고서 형식으로, 핵심 작업 내용과 주의사항을 포함하세요.
마크다운이나 제목 없이 본문만 작성하세요."""

    try:
        import httpx
        from app.core.config import settings

        url = f"{settings.LITELLM_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
        payload = {
            "model": settings.LITELLM_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "당신은 영농 보고서를 작성하는 전문가입니다. 간결하고 실용적인 한국어로 보고서를 작성합니다.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip() if content else None
    except Exception:
        return None


async def get_daily_summary(db: AsyncSession, user_id: str, target_date: date) -> dict:
    """특정 날짜의 영농일지를 집계하여 요약 생성."""
    result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.user_id == user_id,
            JournalEntry.work_date == target_date,
        )
    )
    entries = list(result.scalars().all())

    if not entries:
        return {
            "date": target_date.isoformat(),
            "entry_count": 0,
            "stages_worked": [],
            "crops": [],
            "weather": None,
            "missing_fields": [],
            "summary_text": f"{target_date.isoformat()} 기록된 영농일지가 없습니다.",
        }

    # 집계
    stages = list({e.work_stage for e in entries})
    crops = list({e.crop for e in entries})
    weathers = [e.weather for e in entries if e.weather]
    weather = weathers[0] if weathers else None
    missing = check_missing_fields(entries)

    # 요약문 생성 (LLM → 실패 시 템플릿 폴백)
    summary_text = await _generate_summary_llm(entries, target_date, weather, missing)
    if not summary_text:
        summary_text = _generate_summary_template(
            entries, target_date, crops, stages, weather, missing
        )

    return {
        "date": target_date.isoformat(),
        "entry_count": len(entries),
        "stages_worked": stages,
        "crops": crops,
        "weather": weather,
        "missing_fields": missing,
        "summary_text": summary_text,
    }

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.services.ai_agent_bridge import AiAgentBridge

from app.api import (
    ai_agent,
    auth,
    health,
    journal,
    daily_journal,
    knowledge,
    market,
    pesticide,
    review_analysis,
    diagnosis,
    subsidy,
)
from app.core.config import settings
from app.core.database import async_session, close_db, init_db
from app.core.security import hash_password
from app.models.user import User  # noqa: F401 — Base.metadata 등록용
from app.models.review_analysis import ReviewAnalysis, ReviewSentiment  # noqa: F401
from app.models.diagnosis import DiagnosisHistory  # noqa: F401
from app.models.journal import JournalEntry  # noqa: F401
from app.models.daily_journal import DailyJournal, DailyJournalRevision  # noqa: F401
from app.models.ai_agent import (  # noqa: F401 — Base.metadata 등록용 (agent-action-history)
    AiAgentDecision,
    AiAgentActivityDaily,
    AiAgentActivityHourly,
)
from app.models.subsidy import Subsidy  # noqa: F401


async def seed_users():
    """테스트 계정이 없으면 시딩."""
    seed_data = [
        {
            "id": "farmer01",
            "name": "김사과",
            "email": "farmer01@farmos.kr",
            "password": "farm1234",
            "location": "경북 영주시",
            "area": 33.0,
            "farmname": "김사과 사과농장",
            "profile": "",
        },
        {
            "id": "parkpear",
            "name": "박배나무",
            "email": "parkpear@farmos.kr",
            "password": "pear5678",
            "location": "충남 천안시",
            "area": 25.5,
            "farmname": "박씨네 배 과수원",
            "profile": "",
        },
    ]
    async with async_session() as db:
        for data in seed_data:
            exists = await db.execute(select(User).where(User.id == data["id"]))
            if exists.scalar_one_or_none():
                continue
            user = User(
                id=data["id"],
                name=data["name"],
                email=data["email"],
                password=hash_password(data["password"]),
                location=data["location"],
                area=data["area"],
                farmname=data["farmname"],
                profile=data["profile"],
            )
            db.add(user)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_users()

    # journal-entry-photos: 24h 경과 entry_id=null 인 사진 정리 (boot-time)
    try:
        from app.core.photo_storage import cleanup_orphans

        async with async_session() as db:
            removed = await cleanup_orphans(db, older_than_hours=24)
            if removed:
                logging.getLogger(__name__).info(
                    "journal_photo_orphans.cleaned count=%d", removed
                )
    except Exception:  # noqa: BLE001 — cleanup 실패가 기동 막지 않음
        # exception() 사용 → traceback 포함 자동 기록. 운영자가 즉시 원인 추적 가능.
        logging.getLogger(__name__).exception(
            "journal_photo_orphans.cleanup_failed"
        )

    # AI Agent Bridge (agent-action-history) — Relay patch 적용 시 활성화.
    # IOT_RELAY_API_KEY 가 비어 있으면 (env 미주입) 플래그가 켜져도 안전 비활성화한다.
    bridge: AiAgentBridge | None = None
    if settings.AI_AGENT_BRIDGE_ENABLED:
        if not settings.IOT_RELAY_API_KEY:
            logging.getLogger(__name__).warning(
                "ai_agent_bridge.disabled_missing_api_key "
                "AI_AGENT_BRIDGE_ENABLED=True 이지만 IOT_RELAY_API_KEY 가 비어있음 — "
                "환경변수/.env 로 키를 주입한 뒤 재시작하세요."
            )
        else:
            try:
                bridge = AiAgentBridge(settings=settings, session_factory=async_session)
                await bridge.start()
                app.state.ai_agent_bridge = bridge
            except Exception as exc:  # noqa: BLE001 — Bridge 실패가 BE 기동 막지 않음
                logging.getLogger(__name__).warning(
                    "ai_agent_bridge.start_failed err=%s", exc
                )

    # 공익직불 지원금 시드 (3개 프로그램)
    from app.services.subsidy.seed_data import seed_subsidies
    async with async_session() as db:
        await seed_subsidies(db)

    # 공익직불 RAG 준비 (팀원 신규 셋업 시 자동 인덱싱 포함):
    #  1) RAG 싱글톤 + 리랭커 모델 사전 로드 → 첫 요청 지연 방지
    #  2) ChromaDB 가 비어있고 Markdown 캐시가 있으면 자동 인덱싱
    #     (Git 리포에 커밋된 data/gov/*.md 를 사용 — PDF 재파싱 불필요)
    # 실패 시 서버 기동은 계속 (graceful fallback): /subsidy/match 는 DB 전용이므로 영향 없고,
    # /subsidy/ask 는 빈 citation + escalation_needed=True 로 응답.
    # 다만 관찰 가능성을 위해 app.state 에 플래그를 남겨 /health 등에서 확인 가능하게 함.
    import asyncio as _asyncio
    import logging as _logging
    _log = _logging.getLogger("app.main")
    app.state.subsidy_rag_ready = False
    try:
        from app.services.subsidy.gov_rag import _get_reranker, run_ingest_pipeline
        from app.services.subsidy.tools import _get_rag

        rag = await _asyncio.to_thread(_get_rag)
        if rag.count() == 0:
            _log.info("공익직불 ChromaDB 비어있음 — 캐시된 Markdown 으로 자동 인덱싱 시작")
            try:
                await _asyncio.to_thread(run_ingest_pipeline, False)
            except FileNotFoundError as e:
                _log.warning(f"자동 인덱싱 스킵 (Markdown 캐시 없음): {e}")
            except Exception as e:
                _log.error(f"자동 인덱싱 실패 — /subsidy/ask 는 빈 결과 반환: {e}", exc_info=True)
        await _asyncio.to_thread(_get_reranker)
        app.state.subsidy_rag_ready = rag.count() > 0
    except Exception as e:
        # UPSTAGE_API_KEY 미설정·네트워크 오류·모델 로드 실패 등 — 서버 기동은 계속
        _log.warning(f"공익직불 RAG 준비 실패 (/subsidy/ask 제한 동작): {e}")

    # 💡 3일 지난 이미지를 삭제하는 백그라운드 태스크 시작
    async def cleanup_old_diagnosis_images():
        """24시간마다 data/uploads/diagnosis 디렉토리 내의 3일 이상 된 파일을 삭제."""
        upload_dir = Path(settings.UPLOAD_BASE_DIR) / "diagnosis"
        while True:
            try:
                if upload_dir.exists():
                    now = time.time()
                    three_days_ago = now - (3 * 24 * 60 * 60)
                    
                    deleted_count = 0
                    # 블로킹 I/O 최소화를 위해 루프 내부에서 체크
                    for file in upload_dir.glob("*.webp"):
                        if file.is_file() and file.stat().st_mtime < three_days_ago:
                            # 💡 블로킹 작업을 스레드에서 실행 고려 가능하나, 파일 수가 적으므로 우선 유지
                            file.unlink()
                            deleted_count += 1
                    
                    if deleted_count > 0:
                        _log.info(f"Cleanup: Deleted {deleted_count} old diagnosis images.")
            except Exception as e:
                _log.error(f"Cleanup task error: {str(e)}")
            
            # 24시간 대기
            await _asyncio.sleep(24 * 60 * 60)

    # 💡 가비지 컬렉션 방지를 위해 app.state에 강한 참조 유지
    app.state.cleanup_task = _asyncio.create_task(cleanup_old_diagnosis_images())

    # 농약 DB 자동 시드 — 번들 VERSION 이 DB 버전보다 새로우면 백그라운드 시드
    try:
        from app.core.pesticide_autoseed import schedule_pesticide_autoseed
        schedule_pesticide_autoseed()
    except Exception as e:
        _log.warning(f"농약 DB 자동 시드 스케줄 실패: {e}")

    yield

    # 💡 백그라운드 태스크 종료 처리
    cleanup_task = getattr(app.state, "cleanup_task", None)
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (_asyncio.CancelledError, Exception):
            pass

    if bridge is not None:
        try:
            await bridge.stop()
        except Exception as exc:  # noqa: BLE001 — 종료 실패도 BE shutdown 은 계속 진행
            logging.getLogger(__name__).warning(
                "ai_agent_bridge.stop_failed err=%s", exc, exc_info=True
            )
    await close_db()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 💡 업로드된 정적 파일(이미지 등) 서빙 설정
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")

app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(health.router, prefix=settings.API_V1_PREFIX)
app.include_router(journal.router, prefix=settings.API_V1_PREFIX)
app.include_router(daily_journal.router, prefix=settings.API_V1_PREFIX)
app.include_router(knowledge.router, prefix=settings.API_V1_PREFIX)
app.include_router(pesticide.router, prefix=settings.API_V1_PREFIX)
app.include_router(market.router, prefix=settings.API_V1_PREFIX)
app.include_router(review_analysis.router, prefix=settings.API_V1_PREFIX)
app.include_router(diagnosis.router, prefix=settings.API_V1_PREFIX)
app.include_router(ai_agent.router, prefix=settings.API_V1_PREFIX)
app.include_router(subsidy.router, prefix=settings.API_V1_PREFIX)

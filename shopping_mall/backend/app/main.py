import os
import sys

# Windows: bge-m3(임베딩)와 CrossEncoder(리랭커)가 각각 torch를 로딩할 때
# OpenMP DLL 중복으로 세그폴트가 발생하는 문제를 방지.
# torch가 import되기 전, 모든 import보다 먼저 설정해야 효과가 있다.
if sys.platform.startswith("win"):
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    # Windows 기본 이벤트 루프는 ProactorEventLoop인데,
    # psycopg3 async(AsyncPostgresSaver)는 SelectorEventLoop를 요구한다.
    # uvicorn이 이벤트 루프를 생성하기 전에 정책을 설정해야 효과가 있다.
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text


def _setup_app_logging() -> None:
    """앱 로거(ai.*, app.*)에 UTF-8 파일 핸들러를 붙인다.

    chatbot.log에만 기록하고 stdout에는 쓰지 않는다.
    - stdout(shop-be.log): uvicorn 인프라 로그(ASCII) 전용
    - chatbot.log: 챗봇 비즈니스 로그(한글 포함) 전용, encoding="utf-8" 고정

    stdout 리다이렉트 시 Windows cp949 인코딩 문제를 완전히 회피한다.
    """
    from app.paths import LOG_DIR
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "chatbot.log"

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)

    for name in ("ai", "app", "httpx", "chromadb"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) for h in lg.handlers):
            lg.addHandler(file_handler)
            lg.propagate = False


# 모듈 임포트 시점에 핸들러 등록 — lifespan보다 앞서 실행되어
# 워커 프로세스 전체 생애 동안 chatbot.log에 UTF-8로 기록된다.
_setup_app_logging()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.database import Base, engine
from app.routers import products, categories, cart, orders, users, reviews, stores, wishlists
from app.routers import shipments, calendar, reports, analytics, chatbot, admin
from app.routers import faq
from app import models  # noqa: F401 - Import models to register them with Base

logger = logging.getLogger(__name__)

# Create tables on startup
Base.metadata.create_all(bind=engine)


def _ensure_schema_patches() -> None:
    """Apply small idempotent schema patches for projects without Alembic."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE shop_tickets "
                "ADD COLUMN IF NOT EXISTS flags TEXT NOT NULL DEFAULT '[]'"
            )
        )
    logger.info("shop_tickets.flags schema patch ensured.")


_ensure_schema_patches()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle: start and stop APScheduler."""
    sched = None
    try:
        from jobs.scheduler import setup_scheduler
        sched = setup_scheduler()
        sched.start()
        logger.info("APScheduler started.")
    except Exception as e:
        logger.warning(f"Failed to start scheduler: {e}")

    try:
        from app.routers.chatbot import set_chatbot_service
        from ai.rag import RAGService
        from ai.agent import AgentExecutor, build_primary_llm, build_fallback_llm
        from ai.agent.llm import _set_langsmith_env
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from ai.agent.subagents.cs.prompts import CS_INPUT_PROMPT, CS_OUTPUT_PROMPT
        from ai.agent.order_graph.graph import build_order_graph
        from ai.agent.supervisor import SupervisorExecutor
        from ai.agent.supervisor.prompts import SUPERVISOR_INPUT_PROMPT, SUPERVISOR_OUTPUT_PROMPT
        from app.services.multi_agent_chatbot import MultiAgentChatbotService

        _set_langsmith_env()
        rag = RAGService()

        # BM25·Reranker 사전 로드 — 첫 요청 시 이벤트 루프 블로킹 방지
        # BM25는 JSON 파싱이라 빠름, Reranker(~570MB)는 스레드풀에서 로드
        import asyncio
        from ai.rag import _load_bm25, _load_reranker
        _load_bm25()
        if settings.reranker_model:
            logger.info("Reranker 사전 로드 시작: %s", settings.reranker_model)
            await asyncio.to_thread(_load_reranker, settings.reranker_model)

        primary = build_primary_llm()
        fallback = build_fallback_llm()

        # FAQ 작성 에이전트 초기화 — 챗봇 서비스와 LLM·RAG 공유
        from app.routers.faq import set_faq_writer
        from ai.agent.faq_writer import FaqWriterAgent
        set_faq_writer(FaqWriterAgent(primary=primary, fallback=fallback, rag_service=rag))
        logger.info("FaqWriterAgent initialized.")

        async with AsyncPostgresSaver.from_conn_string(settings.langgraph_postgres_url) as checkpointer:
            await checkpointer.setup()
            order_graph = build_order_graph(checkpointer)

            cs_executor = AgentExecutor(
                primary=primary,
                fallback=fallback,
                rag_service=rag,
                max_iterations=settings.agent_max_iterations,
            )
            supervisor = SupervisorExecutor(
                primary=primary,
                fallback=fallback,
                cs_executor=cs_executor,
                cs_input_prompt=CS_INPUT_PROMPT,
                cs_output_prompt=CS_OUTPUT_PROMPT,
                order_graph=order_graph,
            )
            set_chatbot_service(MultiAgentChatbotService(
                supervisor,
                input_prompt=SUPERVISOR_INPUT_PROMPT,
                output_prompt=SUPERVISOR_OUTPUT_PROMPT,
            ))
            logger.info("Multi-agent chatbot service initialized (LangChain).")
            yield

    except Exception as e:
        logger.warning(f"Failed to initialize chatbot service: {e}")
        yield

    finally:
        if sched is not None:
            sched.shutdown(wait=False)
            logger.info("APScheduler stopped.")


app = FastAPI(title="Shopping Mall API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """CORSMiddleware 안쪽의 ExceptionMiddleware에 등록되는 전역 핸들러.

    라우터 레벨 try/except를 빠져나온 일반 애플리케이션 예외가
    Starlette ServerErrorMiddleware까지 전파되면 CORS 헤더가 누락된 채 500이
    반환될 수 있다. 이 핸들러가 ExceptionMiddleware 안에서 exception을 잡아
    JSONResponse를 반환함으로써 CORSMiddleware가 헤더를 정상적으로 주입한다.
    """
    logger.error(
        "전역 예외 핸들러 — 처리되지 않은 예외: %s: %s",
        type(exc).__name__, exc, exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."},
    )

# Existing routers
app.include_router(products.router)
app.include_router(categories.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(users.router)
app.include_router(reviews.router)
app.include_router(stores.router)
app.include_router(wishlists.router)

# Backoffice routers
app.include_router(shipments.router)
app.include_router(calendar.router)
app.include_router(reports.router)
app.include_router(analytics.router)
app.include_router(chatbot.router)
app.include_router(admin.router)
app.include_router(faq.router)


@app.get("/")
def root():
    return {"message": "Shopping Mall API is running", "docs": "/docs"}

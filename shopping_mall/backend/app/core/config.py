"""애플리케이션 설정 — 환경변수를 한 곳에서 관리합니다."""
import re

from pydantic_settings import BaseSettings

from app.paths import BACKEND_ROOT


class Settings(BaseSettings):
    # ── 데이터베이스 ────────────────────────────────────────────────────────
    database_url: str = ""

    # ── 보안 및 인증 ────────────────────────────────────────────────────────
    jwt_secret_key: str = ""

    # CORS 허용 도메인 (JSON 배열 형식으로 작성하거나 * 사용 가능)
    allow_origins: list[str] = []

    # ── 외부 API 연동 ───────────────────────────────────────────────────────
    # FarmOS 백엔드 연동 주소 (공유 인증 및 데이터 조회용)
    farmos_api_url: str = ""
    anniversary_api_key: str = ""

    # ── 임베딩 ──────────────────────────────────────────────────────────────
    # provider: openrouter | sentence_transformers | openai
    # ⚠️  시딩과 쿼리는 동일한 provider + model이어야 합니다. 변경 시 re-seed 필요.
    embed_provider: str = "sentence_transformers"
    # sentence_transformers 기본값: BAAI/bge-m3 (다국어·1024dim·8192 tok — ~2.2GB)
    # openrouter: openai/text-embedding-3-small (LITELLM_API_KEY 재사용)
    # openai: text-embedding-3-small
    embed_model: str = "BAAI/bge-m3"
    embed_api_key: str = ""      # openai provider 전용 (openrouter는 litellm_api_key 재사용)
    embed_base_url: str = ""     # openai-compatible 엔드포인트 오버라이드 (선택)

    # ── LLM (Primary + Utility 공용 — LiteLLM 프록시 경유) ──────────────────
    # Primary: 챗봇 에이전트 도구 선택·응답 생성
    # Utility: 주간 리포트 생성·비용 분류
    litellm_url: str = "https://openrouter.ai/api/v1"
    litellm_api_key: str = ""
    litellm_model: str = "openai/gpt-4o-mini"
    llm_provider: str = "openrouter"  # 참고용 (현재 라우팅에 직접 사용되지 않음)

    # ── Fallback LLM (Anthropic Claude) ─────────────────────────────────────
    anthropic_api_key: str = ""
    claude_fallback_model: str = "claude-haiku-4-5"

    # ── 재랭킹 ──────────────────────────────────────────────────────────────
    # Cross-Encoder 재랭킹 모델. 비워두면 재랭킹 비활성화.
    # 추천: dragonkue/bge-reranker-v2-m3-ko (한국어 특화, ~570MB)
    reranker_model: str = "dragonkue/bge-reranker-v2-m3-ko"

    # ── RAG 검색 ────────────────────────────────────────────────────────────
    # Dense 벡터 검색 거리 임계값. 임베딩 모델 교체 시 re-seed 후 재측정 필요.
    # bge-m3 기준 실측값: 정책 0.24~0.45, FAQ 0.24~0.42, 보관법 0.39~0.41
    rag_distance_threshold: float = 0.50
    # 보관법은 유사 항목 거리가 threshold에 근접하므로 별도 설정
    rag_storage_distance_threshold: float = 0.35
    rag_storage_retry_threshold: float = 0.40

    # ── 에이전트 ────────────────────────────────────────────────────────────
    agent_max_iterations: int = 10

    # true → SupervisorExecutor (멀티 에이전트 + LangGraph OrderGraph)
    # false → 기존 단일 AgentExecutor
    use_multi_agent: bool = True

    # ── LangSmith 트레이싱 ───────────────────────────────────────────────────
    langchain_tracing_v2: str = "false"
    langchain_api_key: str = ""
    langchain_project: str = "farmos-chatbot"

    # ── 경로 ────────────────────────────────────────────────────────────────
    # 정책 문서(PDF/DOCX) 폴더. 기본값: shopping_mall/backend/ai/docs/
    # 다른 위치를 쓰려면 .env에 POLICY_DOCS_DIR=/절대/경로 로 지정.
    policy_docs_dir: str = str(BACKEND_ROOT / "ai" / "docs")

    @property
    def langgraph_postgres_url(self) -> str:
        """AsyncPostgresSaver(psycopg3)용 URL — SQLAlchemy 드라이버 접미사 제거.

        예: postgresql+asyncpg://... → postgresql://...
        PostgreSQL URL이 아닌 경우 ValueError를 발생시킵니다.
        """
        url = self.database_url
        if not re.match(r"^postgres(?:ql)?(?:\+\w+)?://", url):
            scheme_match = re.match(r"^([^:/]+)", url)
            detected = scheme_match.group(1) if scheme_match else "(unknown)"
            raise ValueError(
                f"DATABASE_URL이 PostgreSQL URL이 아닙니다 (감지된 스킴: {detected!r}). "
                "AsyncPostgresSaver는 PostgreSQL 전용입니다."
            )
        return re.sub(r"^(postgres(?:ql)?)\+\w+://", r"\1://", url)

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

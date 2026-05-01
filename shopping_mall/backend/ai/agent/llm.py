"""LangChain LLM 팩토리 — Primary(OpenAI 호환) + Fallback(Claude)."""
import os

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from app.core.config import settings


def _set_langsmith_env() -> None:
    """LangSmith 트레이싱 환경변수 설정.

    LangChain은 LANGCHAIN_* 환경변수를 직접 읽으므로
    Settings 값을 여기서 os.environ에 주입합니다.

    모듈 임포트 시 자동 실행되지 않습니다.
    app/main.py의 lifespan에서 명시적으로 호출해야 합니다.
    """
    if settings.langchain_api_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.langchain_tracing_v2)
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langchain_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)


def build_primary_llm() -> ChatOpenAI:
    """Primary LLM — OpenAI 호환 엔드포인트 (LiteLLM 프록시 / OpenRouter / OpenAI)."""
    return ChatOpenAI(
        base_url=settings.litellm_url,
        api_key=settings.litellm_api_key,
        model=settings.litellm_model,
    )


def build_fallback_llm() -> ChatAnthropic | None:
    """Fallback LLM — Anthropic Claude. API 키 없으면 None."""
    if not settings.anthropic_api_key:
        return None
    return ChatAnthropic(
        api_key=settings.anthropic_api_key,
        model=settings.claude_fallback_model,
    )

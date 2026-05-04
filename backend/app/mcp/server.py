"""FastMCP 서버 빌더.

Design Ref: §6.2 (mcp/server.py)

build_review_mcp() 가 FastMCP 인스턴스를 생성하고 register_all_tools 로 tool 들을
등록한다. main.py 는 이 인스턴스에서 .http_app(path="/") 를 얻어 FastAPI 에 mount 한다.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from app.mcp.tools import register_all_tools

logger = logging.getLogger("app.mcp.server")


def build_review_mcp() -> FastMCP:
    """FarmOS Review MCP 서버 인스턴스를 빌드한다."""
    mcp = FastMCP(
        name="farmos-review-mcp",
        instructions=(
            "FarmOS 농산물 리뷰 자동화 분석 MCP 서버.\n"
            "ChromaDB 기반 의미검색, LLM 감성분석/키워드/요약, 트렌드/이상 탐지, "
            "PDF 리포트 생성을 제공합니다.\n"
            "인증: Authorization Bearer <JWT> 또는 Cookie farmos_token=<JWT>."
        ),
    )
    register_all_tools(mcp)
    logger.info("farmos-review-mcp built (tools registered)")
    return mcp


__all__ = ["build_review_mcp"]

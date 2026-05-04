"""FarmOS Review MCP — FastMCP 기반 리뷰 자동화 분석 MCP 서버 패키지.

Design Ref: §2 (FastAPI mount), §11.1 (File Structure)
"""

from app.mcp.server import build_review_mcp

__all__ = ["build_review_mcp"]

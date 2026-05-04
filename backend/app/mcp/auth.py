"""MCP tool 인증 어댑터 — Authorization Bearer + Cookie 둘 다 지원.

Design Ref: §4 (Authentication Adapter)
Plan SC: SC-04 (멀티테넌트 컨텍스트 — JWT 호출자 → seller_id)

전략:
    1. 우선 Authorization: Bearer <JWT> 헤더에서 토큰을 찾는다 (외부 MCP 클라이언트).
    2. 없으면 Cookie: farmos_token=<JWT> 에서 토큰을 찾는다 (브라우저).
    3. 둘 다 없으면 ToolError.
    4. 기존 core.security.decode_access_token 으로 검증, core.user_store.find_by_id 로
       User 조회. 검증 로직은 라우터(deps.py:get_current_user) 와 동일.

이로써 MCP 도구는 별도 인증 미들웨어를 갖지 않고도 기존 FastAPI 와 동일한 신뢰 모델을
공유한다 (Plan D-2 충족).
"""

from __future__ import annotations

import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.context import request_ctx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import user_store
from app.core.deps import COOKIE_KEY
from app.core.security import decode_access_token
from app.models.user import User

logger = logging.getLogger("app.mcp.auth")


def _extract_token() -> str | None:
    """현재 MCP request 에서 JWT 토큰을 추출한다.

    우선순위: Authorization: Bearer > Cookie: farmos_token.
    request_ctx.get() 이 None 을 반환하거나 request 객체가 없으면 None.
    """
    try:
        rc = request_ctx.get()
    except LookupError:
        return None
    if rc is None:
        return None

    request = getattr(rc, "request", None)
    if request is None:
        return None

    # 1) Authorization: Bearer <token>
    headers = getattr(request, "headers", None)
    if headers is not None:
        auth_value = headers.get("authorization") or headers.get("Authorization") or ""
        if auth_value.lower().startswith("bearer "):
            token = auth_value[7:].strip()
            if token:
                return token

    # 2) Cookie: farmos_token
    cookies = getattr(request, "cookies", None)
    if isinstance(cookies, dict):
        token = cookies.get(COOKIE_KEY)
        if token:
            return token
    elif cookies is not None:
        # Some frameworks expose cookies as a multidict-like accessor.
        try:
            token = cookies.get(COOKIE_KEY)
        except Exception:  # noqa: BLE001 — defensive
            token = None
        if token:
            return token

    return None


async def get_current_user_from_ctx(db: AsyncSession) -> User:
    """현재 MCP 요청 컨텍스트에서 인증된 User 를 반환한다.

    Args:
        db: 호출자가 관리하는 AsyncSession (tool 함수 내부에서 열린 세션)

    Raises:
        ToolError: 토큰 없음 / 만료 / 위변조 / 사용자 미존재.

    Returns:
        활성 User 객체.
    """
    token = _extract_token()
    if not token:
        raise ToolError(
            "Authentication required: provide Authorization Bearer token "
            "or Cookie farmos_token."
        )

    payload = decode_access_token(token)
    if payload is None:
        raise ToolError("Invalid or expired access token.")

    user_id = payload.get("sub", "")
    if not user_id:
        raise ToolError("Token missing 'sub' claim.")

    user = await user_store.find_by_id(db, user_id)
    if user is None:
        raise ToolError("User not found or inactive.")

    return user


__all__ = ["get_current_user_from_ctx"]

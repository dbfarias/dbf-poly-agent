"""API authentication middleware — supports API key and JWT."""

from fastapi import Header, HTTPException, Request

from api.auth import decode_jwt
from bot.config import settings


async def verify_api_key(
    request: Request,
    x_api_key: str = Header(default=""),
) -> str:
    """Validate authentication via X-API-Key header or Bearer JWT token.

    Accepts either:
    - X-API-Key: <api_secret_key>  (programmatic access)
    - Authorization: Bearer <jwt>  (dashboard login)

    Exempt routes (e.g. /api/health, /api/auth/login) should NOT use this.
    """
    # Check API key first
    if x_api_key and x_api_key == settings.api_secret_key:
        return x_api_key

    # Check JWT Bearer token
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_jwt(token)
        if payload and payload.get("sub"):
            return payload["sub"]

    raise HTTPException(status_code=401, detail="Invalid authentication")

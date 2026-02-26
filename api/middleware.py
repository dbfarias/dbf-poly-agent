"""API authentication middleware."""

from fastapi import Header, HTTPException

from bot.config import settings


async def verify_api_key(x_api_key: str = Header(...)) -> str:
    """Validate X-API-Key header against the configured secret.

    Exempt routes (e.g. /api/health) should NOT include this dependency.
    """
    if x_api_key != settings.api_secret_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

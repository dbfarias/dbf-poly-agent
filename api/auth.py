"""JWT authentication for the dashboard."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bot.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str


def _verify_password(plain: str, expected: str) -> bool:
    """Constant-time password comparison."""
    return hmac.compare_digest(
        hashlib.sha256(plain.encode()).hexdigest(),
        hashlib.sha256(expected.encode()).hexdigest(),
    )


def create_jwt(username: str) -> tuple[str, datetime]:
    """Create a signed JWT token."""
    expires = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    payload = {
        "sub": username,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.api_secret_key, algorithm=JWT_ALGORITHM)
    return token, expires


def decode_jwt(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        return jwt.decode(token, settings.api_secret_key, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=503,
            detail="Dashboard login not configured. Set DASHBOARD_PASSWORD in .env",
        )

    if (
        req.username != settings.dashboard_user
        or not _verify_password(req.password, settings.dashboard_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token, expires = create_jwt(req.username)
    return LoginResponse(token=token, expires_at=expires.isoformat())

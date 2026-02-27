"""JWT authentication for the dashboard."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bot.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
COOKIE_NAME = "polybot_session"


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


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


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=503,
            detail="Dashboard login not configured. Set DASHBOARD_PASSWORD in .env",
        )

    # Constant-time comparison for both username and password to prevent timing attacks.
    # Always check both to avoid leaking which field is wrong.
    username_ok = hmac.compare_digest(req.username, settings.dashboard_user)
    password_ok = _verify_password(req.password, settings.dashboard_password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token, expires = create_jwt(req.username)

    # Auto-detect HTTPS via X-Forwarded-Proto (set by nginx)
    is_https = request.headers.get("x-forwarded-proto") == "https"

    response = JSONResponse(
        content={"token": token, "expires_at": expires.isoformat()},
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_https,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
        path="/",
    )
    return response


@router.get("/me")
async def me(request: Request):
    """Check if the current session is authenticated (via cookie or header)."""
    # Try cookie first
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        # Fallback to Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token:
        payload = decode_jwt(token)
        if payload and payload.get("sub"):
            return {"authenticated": True, "username": payload["sub"]}

    raise HTTPException(status_code=401, detail="Not authenticated")


@router.post("/logout")
async def logout():
    """Clear the session cookie."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response

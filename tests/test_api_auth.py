"""Comprehensive tests for api/auth.py and api/middleware.py."""

import os

# Must be set before any bot.config import so the Settings validator passes.
os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import time
from datetime import datetime, timezone
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from api.auth import (
    COOKIE_NAME,
    JWT_ALGORITHM,
    JWT_EXPIRY_HOURS,
    _verify_password,
    create_jwt,
    decode_jwt,
)
from api.auth import (
    router as auth_router,
)
from api.middleware import verify_api_key
from bot.config import settings

TEST_API_KEY = os.environ["API_SECRET_KEY"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_client():
    """Async HTTP client wired to the auth router + a protected endpoint."""
    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/api/protected")
    async def protected(user: str = Depends(verify_api_key)):
        return {"user": user}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# JWT helper functions
# ---------------------------------------------------------------------------


class TestCreateJwt:
    """Tests for create_jwt()."""

    def test_returns_token_string_and_future_expiry(self):
        token, expires = create_jwt("alice")

        assert isinstance(token, str)
        assert len(token) > 0
        assert isinstance(expires, datetime)
        assert expires > datetime.now(timezone.utc)

    def test_token_is_decodable(self):
        token, _expires = create_jwt("bob")
        payload = pyjwt.decode(
            token, settings.api_secret_key, algorithms=[JWT_ALGORITHM]
        )

        assert payload["sub"] == "bob"
        assert "exp" in payload
        assert "iat" in payload

    def test_expiry_is_24h_in_future(self):
        _token, expires = create_jwt("carol")
        now = datetime.now(timezone.utc)
        delta = expires - now

        # Allow a small tolerance (the function calls datetime.now twice)
        assert 23 * 3600 < delta.total_seconds() <= JWT_EXPIRY_HOURS * 3600 + 5


class TestDecodeJwt:
    """Tests for decode_jwt()."""

    def test_returns_payload_for_valid_token(self):
        token, _expires = create_jwt("dave")
        payload = decode_jwt(token)

        assert payload is not None
        assert payload["sub"] == "dave"

    def test_returns_none_for_expired_token(self):
        expired_payload = {
            "sub": "expired_user",
            "exp": int(time.time()) - 3600,  # 1 hour in the past
            "iat": int(time.time()) - 7200,
        }
        token = pyjwt.encode(
            expired_payload, settings.api_secret_key, algorithm=JWT_ALGORITHM
        )

        assert decode_jwt(token) is None

    def test_returns_none_for_invalid_signature(self):
        token, _expires = create_jwt("eve")
        # Re-sign with a different key
        tampered = pyjwt.encode(
            {"sub": "eve", "exp": int(time.time()) + 3600},
            "wrong-key-xxxxxxxxxxxxxxxx",
            algorithm=JWT_ALGORITHM,
        )

        assert decode_jwt(tampered) is None

    def test_returns_none_for_garbage_string(self):
        assert decode_jwt("not.a.jwt") is None
        assert decode_jwt("") is None

    def test_returns_none_for_tampered_payload(self):
        """Modify the payload portion of a valid token to break the signature."""
        token, _expires = create_jwt("frank")
        # Flip a character in the payload section (middle segment)
        parts = token.split(".")
        payload_chars = list(parts[1])
        payload_chars[0] = "A" if payload_chars[0] != "A" else "B"
        parts[1] = "".join(payload_chars)
        tampered = ".".join(parts)

        assert decode_jwt(tampered) is None


class TestVerifyPassword:
    """Tests for _verify_password() — uses bcrypt against settings.dashboard_password."""

    def _reset_hash(self):
        """Clear cached bcrypt hash so it re-hashes on next call."""
        import api.auth as auth_mod
        auth_mod._cached_hash = None

    def test_returns_true_for_matching_passwords(self):
        self._reset_hash()
        with patch.object(settings, "dashboard_password", "hunter2"):
            assert _verify_password("hunter2", "") is True

    def test_returns_false_for_different_passwords(self):
        self._reset_hash()
        with patch.object(settings, "dashboard_password", "hunter3"):
            assert _verify_password("hunter2", "") is False

    def test_returns_false_for_wrong_password(self):
        self._reset_hash()
        with patch.object(settings, "dashboard_password", "correct"):
            assert _verify_password("wrong", "") is False

    def test_unicode_passwords(self):
        self._reset_hash()
        with patch.object(settings, "dashboard_password", "senha\u00e7a"):
            assert _verify_password("senha\u00e7a", "") is True
            assert _verify_password("senhaca", "") is False


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------


class TestLoginEndpoint:
    """Tests for POST /api/auth/login."""

    def _reset_hash(self):
        """Clear cached bcrypt hash so it re-hashes per test."""
        import api.auth as auth_mod
        auth_mod._cached_hash = None

    async def test_successful_login_returns_expiry_and_cookie(self, auth_client):
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "secret123"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "secret123"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" not in data  # Token no longer in response body
        assert "expires_at" in data

        # Verify the httpOnly cookie was set
        cookie_header = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in cookie_header
        assert "httponly" in cookie_header.lower()

    async def test_wrong_password_returns_401(self, auth_client):
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "correct_password"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong_password"},
            )

        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]

    async def test_wrong_username_returns_401(self, auth_client):
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "secret123"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "hacker", "password": "secret123"},
            )

        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]

    async def test_missing_password_configured_returns_503(self, auth_client):
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", ""),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "anything"},
            )

        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    async def test_empty_username_rejected_by_validation(self, auth_client):
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "secret123"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "", "password": "secret123"},
            )

        assert resp.status_code == 422  # Pydantic validation error

    async def test_missing_fields_rejected(self, auth_client):
        resp = await auth_client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_cookie_contains_valid_jwt(self, auth_client):
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "mypass"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "mypass"},
            )

        # Token is in the cookie, not the response body
        cookie_header = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in cookie_header
        # Extract token from set-cookie header
        for part in cookie_header.split(";"):
            if COOKIE_NAME in part:
                token = part.split("=", 1)[1]
                break
        payload = decode_jwt(token)
        assert payload is not None
        assert payload["sub"] == "admin"

    async def test_login_secure_cookie_with_https_header(self, auth_client):
        """When x-forwarded-proto is https, cookie should have secure flag."""
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "secret"),
        ):
            resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "secret"},
                headers={"x-forwarded-proto": "https"},
            )

        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "secure" in cookie_header.lower()


# ---------------------------------------------------------------------------
# /me endpoint
# ---------------------------------------------------------------------------


class TestMeEndpoint:
    """Tests for GET /api/auth/me."""

    async def test_authenticated_via_bearer_header(self, auth_client):
        token, _expires = create_jwt("admin")
        resp = await auth_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["username"] == "admin"

    async def test_authenticated_via_cookie(self, auth_client):
        token, _expires = create_jwt("admin")
        auth_client.cookies.set(COOKIE_NAME, token)

        resp = await auth_client.get("/api/auth/me")

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["username"] == "admin"

    async def test_no_auth_returns_401(self, auth_client):
        resp = await auth_client.get("/api/auth/me")

        assert resp.status_code == 401
        assert "Not authenticated" in resp.json()["detail"]

    async def test_expired_token_returns_401(self, auth_client):
        expired_payload = {
            "sub": "admin",
            "exp": int(time.time()) - 60,
            "iat": int(time.time()) - 3600,
        }
        token = pyjwt.encode(
            expired_payload, settings.api_secret_key, algorithm=JWT_ALGORITHM
        )

        resp = await auth_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 401

    async def test_invalid_token_returns_401(self, auth_client):
        resp = await auth_client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer garbage.token.here"},
        )

        assert resp.status_code == 401

    async def test_cookie_takes_precedence_over_header(self, auth_client):
        """When both cookie and header exist, cookie is checked first."""
        cookie_token, _ = create_jwt("cookie_user")
        header_token, _ = create_jwt("header_user")
        auth_client.cookies.set(COOKIE_NAME, cookie_token)

        resp = await auth_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {header_token}"},
        )

        assert resp.status_code == 200
        assert resp.json()["username"] == "cookie_user"


# ---------------------------------------------------------------------------
# Logout endpoint
# ---------------------------------------------------------------------------


class TestLogoutEndpoint:
    """Tests for POST /api/auth/logout."""

    async def test_logout_clears_cookie(self, auth_client):
        resp = await auth_client.post("/api/auth/logout")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Check that set-cookie clears the cookie (max-age=0 or expires in past)
        cookie_header = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in cookie_header
        # FastAPI delete_cookie sets max-age=0
        assert "max-age=0" in cookie_header.lower() or 'max-age="0"' in cookie_header.lower()

    async def test_logout_without_prior_session_succeeds(self, auth_client):
        """Logout should succeed even without an active session."""
        resp = await auth_client.post("/api/auth/logout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Middleware: verify_api_key
# ---------------------------------------------------------------------------


class TestVerifyApiKeyMiddleware:
    """Tests for the verify_api_key dependency in api/middleware.py."""

    async def test_valid_api_key_passes(self, auth_client):
        resp = await auth_client.get(
            "/api/protected",
            headers={"X-API-Key": TEST_API_KEY},
        )

        assert resp.status_code == 200
        assert resp.json()["user"] == TEST_API_KEY

    async def test_valid_jwt_bearer_passes(self, auth_client):
        token, _expires = create_jwt("jwt_user")
        resp = await auth_client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert resp.json()["user"] == "jwt_user"

    async def test_valid_cookie_passes(self, auth_client):
        token, _expires = create_jwt("cookie_user")
        auth_client.cookies.set(COOKIE_NAME, token)

        resp = await auth_client.get("/api/protected")

        assert resp.status_code == 200
        assert resp.json()["user"] == "cookie_user"

    async def test_invalid_api_key_no_jwt_returns_401(self, auth_client):
        resp = await auth_client.get(
            "/api/protected",
            headers={"X-API-Key": "wrong-key-totally-invalid"},
        )

        assert resp.status_code == 401
        assert "Invalid authentication" in resp.json()["detail"]

    async def test_expired_jwt_returns_401(self, auth_client):
        expired_payload = {
            "sub": "expired_user",
            "exp": int(time.time()) - 60,
            "iat": int(time.time()) - 3600,
        }
        token = pyjwt.encode(
            expired_payload, settings.api_secret_key, algorithm=JWT_ALGORITHM
        )

        resp = await auth_client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 401

    async def test_no_credentials_returns_401(self, auth_client):
        resp = await auth_client.get("/api/protected")

        assert resp.status_code == 401

    async def test_api_key_takes_precedence_over_jwt(self, auth_client):
        """When both API key and JWT are present, API key is checked first."""
        token, _expires = create_jwt("jwt_user")
        resp = await auth_client.get(
            "/api/protected",
            headers={
                "X-API-Key": TEST_API_KEY,
                "Authorization": f"Bearer {token}",
            },
        )

        assert resp.status_code == 200
        # verify_api_key returns the raw key for API key auth
        assert resp.json()["user"] == TEST_API_KEY

    async def test_invalid_bearer_prefix_ignored(self, auth_client):
        """A non-'Bearer ' Authorization header should not match JWT path."""
        resp = await auth_client.get(
            "/api/protected",
            headers={"Authorization": "Token some-token-value"},
        )

        assert resp.status_code == 401

    async def test_jwt_without_sub_claim_returns_401(self, auth_client):
        """A JWT missing the 'sub' claim should be rejected."""
        payload = {
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            # no "sub" key
        }
        token = pyjwt.encode(
            payload, settings.api_secret_key, algorithm=JWT_ALGORITHM
        )

        resp = await auth_client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 401

    async def test_cookie_with_invalid_jwt_returns_401(self, auth_client):
        """An invalid JWT in the cookie should be rejected."""
        auth_client.cookies.set(COOKIE_NAME, "not-a-valid-jwt")

        resp = await auth_client.get("/api/protected")

        assert resp.status_code == 401

    async def test_empty_api_key_header_not_accepted(self, auth_client):
        """An empty X-API-Key header should not pass authentication."""
        resp = await auth_client.get(
            "/api/protected",
            headers={"X-API-Key": ""},
        )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Integration: full login flow
# ---------------------------------------------------------------------------


class TestFullLoginFlow:
    """End-to-end login -> access protected -> logout flow."""

    def _reset_hash(self):
        """Clear cached bcrypt hash so it re-hashes per test."""
        import api.auth as auth_mod
        auth_mod._cached_hash = None

    async def test_login_then_access_protected_then_logout(self, auth_client):
        # Step 1: Login
        self._reset_hash()
        with (
            patch.object(settings, "dashboard_user", "admin"),
            patch.object(settings, "dashboard_password", "pass123"),
        ):
            login_resp = await auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "pass123"},
            )

        assert login_resp.status_code == 200
        # Token is only in cookie, not response body
        assert "token" not in login_resp.json()

        # Step 2: Use a separately created token for header-based access
        token, _ = create_jwt("admin")
        protected_resp = await auth_client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert protected_resp.status_code == 200
        assert protected_resp.json()["user"] == "admin"

        # Step 3: Also verify /me works
        me_resp = await auth_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["authenticated"] is True

        # Step 4: Logout
        logout_resp = await auth_client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"ok": True}

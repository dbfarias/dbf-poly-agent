"""Tests for api/routers/websocket.py — auth, connection cap, message limits."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.auth import COOKIE_NAME, create_jwt
from api.routers.websocket import ConnectionManager, MAX_CONNECTIONS, MAX_MESSAGE_SIZE, manager


# ---------------------------------------------------------------------------
# ConnectionManager unit tests
# ---------------------------------------------------------------------------


class TestConnectionManager:
    """Unit tests for the ConnectionManager class."""

    def test_starts_empty(self):
        mgr = ConnectionManager()
        assert len(mgr.active) == 0

    @pytest.mark.asyncio
    async def test_connect_accepts_under_cap(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        result = await mgr.connect(ws)
        assert result is True
        assert ws in mgr.active
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_rejects_at_cap(self):
        mgr = ConnectionManager()
        # Fill to capacity
        for _ in range(MAX_CONNECTIONS):
            ws = AsyncMock()
            await mgr.connect(ws)

        # Next should be rejected
        extra = AsyncMock()
        result = await mgr.connect(extra)
        assert result is False
        assert extra not in mgr.active

    @pytest.mark.asyncio
    async def test_disconnect_removes_client(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        mgr.active.append(ws)
        await mgr.disconnect(ws)
        assert ws not in mgr.active

    @pytest.mark.asyncio
    async def test_disconnect_unknown_client_is_noop(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.disconnect(ws)  # Should not raise

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self):
        mgr = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        mgr.active = [ws1, ws2]

        data = {"type": "test", "msg": "hello"}
        await mgr.broadcast(data)

        ws1.send_json.assert_awaited_once_with(data)
        ws2.send_json.assert_awaited_once_with(data)

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        mgr = ConnectionManager()
        alive = AsyncMock()
        dead = AsyncMock()
        dead.send_json.side_effect = Exception("connection closed")
        mgr.active = [alive, dead]

        await mgr.broadcast({"type": "test"})

        assert alive in mgr.active
        assert dead not in mgr.active

    @pytest.mark.asyncio
    async def test_broadcast_empty_connections_is_noop(self):
        mgr = ConnectionManager()
        await mgr.broadcast({"type": "test"})  # Should not raise


# ---------------------------------------------------------------------------
# WebSocket endpoint integration tests
# ---------------------------------------------------------------------------


class TestWebSocketEndpoint:
    """Integration tests for the /ws/live endpoint."""

    @pytest.fixture
    def app(self):
        from api.routers.websocket import router
        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.mark.asyncio
    async def test_rejects_without_cookie(self, app):
        """WebSocket should reject connections without a valid session cookie."""
        from starlette.testclient import TestClient

        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/live"):
                pass  # Should not reach here


class TestMaxMessageSize:
    """Test the MAX_MESSAGE_SIZE constant."""

    def test_max_message_size_is_reasonable(self):
        assert MAX_MESSAGE_SIZE == 1024
        assert isinstance(MAX_MESSAGE_SIZE, int)


# ---------------------------------------------------------------------------
# _serialize helper tests
# ---------------------------------------------------------------------------


class TestSerialize:
    """Tests for the _serialize depth guard, Enum, and Decimal handling."""

    def test_serialize_depth_guard(self):
        """Deeply nested objects are stringified at depth > 10."""
        from api.routers.websocket import _serialize

        obj = {"a": "leaf"}
        for _ in range(15):
            obj = {"nested": obj}
        result = _serialize(obj)
        # Should not raise RecursionError; at depth > 10 inner becomes str
        assert isinstance(result, dict)
        # Walk down to the depth boundary (depth 0..10 are dicts)
        current = result
        for _ in range(11):
            assert isinstance(current, dict)
            current = current["nested"]
        # At depth 11, the value is stringified
        assert isinstance(current, str)

    def test_serialize_enum(self):
        """Enum values are serialized to their .value."""
        from enum import Enum

        from api.routers.websocket import _serialize

        class Color(Enum):
            RED = "red"

        assert _serialize(Color.RED) == "red"

    def test_serialize_decimal(self):
        """Decimal values are serialized to float."""
        from decimal import Decimal

        from api.routers.websocket import _serialize

        assert _serialize(Decimal("3.14")) == 3.14
        assert isinstance(_serialize(Decimal("3.14")), float)

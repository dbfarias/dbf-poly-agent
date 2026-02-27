"""WebSocket endpoint for real-time dashboard updates."""

import asyncio
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth import decode_jwt
from api.dependencies import get_engine
from bot.config import settings

router = APIRouter(tags=["websocket"])

MAX_CONNECTIONS = 10


class ConnectionManager:
    """Manage WebSocket connections for the dashboard."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> bool:
        """Accept connection if under the cap. Returns False if rejected."""
        if len(self.active) >= MAX_CONNECTIONS:
            return False
        await ws.accept()
        self.active.append(ws)
        return True

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    # Validate token query param — accept API key or JWT (constant-time comparison)
    token = ws.query_params.get("token", "")
    is_api_key = hmac.compare_digest(token, settings.api_secret_key) if token else False
    is_jwt = not is_api_key and decode_jwt(token) is not None
    if not is_api_key and not is_jwt:
        await ws.close(code=4001, reason="Unauthorized")
        return

    accepted = await manager.connect(ws)
    if not accepted:
        await ws.close(code=4029, reason="Too many connections")
        return
    try:
        while True:
            # Send periodic updates
            try:
                engine = get_engine()
                status = engine.get_status()
                await ws.send_json({
                    "type": "status",
                    "data": _serialize(status),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except RuntimeError:
                await ws.send_json({"type": "error", "message": "Engine not ready"})

            # Wait for next update or client message
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # No message from client, send next update
    except WebSocketDisconnect:
        manager.disconnect(ws)


def _serialize(obj):
    """Convert non-serializable objects for JSON."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

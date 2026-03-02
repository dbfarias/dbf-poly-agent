"""WebSocket endpoint for real-time dashboard updates."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth import decode_jwt
from api.dependencies import get_engine

logger = structlog.get_logger()
router = APIRouter(tags=["websocket"])

MAX_CONNECTIONS = 10


class ConnectionManager:
    """Manage WebSocket connections for the dashboard."""

    def __init__(self):
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> bool:
        """Accept connection if under the cap. Returns False if rejected."""
        async with self._lock:
            if len(self.active) >= MAX_CONNECTIONS:
                return False
            await ws.accept()
            self.active.append(ws)
            return True

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, data: dict):
        async with self._lock:
            snapshot = list(self.active)

        dead = []
        for ws in snapshot:
            try:
                await ws.send_json(data)
            except WebSocketDisconnect:
                dead.append(ws)
            except Exception:
                logger.warning("ws_broadcast_error", exc_info=True)
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


manager = ConnectionManager()


async def broadcast_trade_event(
    trade_event: str = "",
    market_id: str = "",
    question: str = "",
    strategy: str = "",
    side: str = "",
    price: float = 0.0,
    size: float = 0.0,
    pnl: float | None = None,
    **_kwargs,
) -> None:
    """Broadcast a trade event to all connected dashboard clients."""
    await manager.broadcast({
        "type": "trade",
        "event": trade_event,
        "data": {
            "market_id": market_id,
            "question": question,
            "strategy": strategy,
            "side": side,
            "price": price,
            "size": size,
            "pnl": pnl,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


MAX_MESSAGE_SIZE = 1024


@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    # Validate auth — httpOnly cookie only (no token in URL)
    from api.auth import COOKIE_NAME

    cookie_token = ws.cookies.get(COOKIE_NAME, "")
    if not cookie_token or decode_jwt(cookie_token) is None:
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
                msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                if len(msg) > MAX_MESSAGE_SIZE:
                    await ws.close(code=1009, reason="Message too large")
                    return
            except asyncio.TimeoutError:
                pass  # No message from client, send next update
    except WebSocketDisconnect:
        await manager.disconnect(ws)


def _serialize(obj, _depth: int = 0):
    """Convert non-serializable objects for JSON."""
    if _depth > 10:
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v, _depth + 1) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

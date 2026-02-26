"""WebSocket endpoint for real-time dashboard updates."""

import asyncio
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import get_engine

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage WebSocket connections for the dashboard."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # Send periodic updates
            try:
                engine = get_engine()
                status = engine.get_status()
                await ws.send_json({
                    "type": "status",
                    "data": _serialize(status),
                    "timestamp": datetime.utcnow().isoformat(),
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

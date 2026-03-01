"""Lightweight async event bus — decouples bot from API layer."""

import asyncio
from collections import defaultdict
from typing import Callable

import structlog

logger = structlog.get_logger()

Handler = Callable[..., object]


class EventBus:
    """Simple async publish/subscribe bus.

    Usage:
        bus = EventBus()
        bus.on("trade_filled", my_handler)
        await bus.emit("trade_filled", market_id="abc", price=0.5)
    """

    def __init__(self):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> None:
        """Subscribe a handler to an event."""
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Handler) -> None:
        """Unsubscribe a handler from an event."""
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, **kwargs) -> None:
        """Emit an event — call all registered handlers.

        Errors in individual handlers are logged but don't affect other handlers.
        """
        for handler in list(self._handlers.get(event, [])):
            try:
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.warning("event_handler_error", event_name=event, exc_info=True)


# Singleton instance used across the application
event_bus = EventBus()

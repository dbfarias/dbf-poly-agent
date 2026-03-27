"""Thread-local HTTP session mixin for thread-safe HTTP clients."""

from __future__ import annotations

import threading

import httpx
import structlog

logger = structlog.get_logger()


class ThreadLocalSessionMixin:
    """Mixin that provides thread-local httpx sessions.

    When using asyncio.to_thread(), each thread gets its own HTTP
    session to prevent connection pooling bugs from sharing sessions
    across threads.

    Note: kwargs passed to _get_session() are only used on the first
    call per (instance, thread) pair. Subsequent calls return the
    cached session regardless of kwargs.
    """

    _thread_local = threading.local()

    def _get_session(self, **kwargs: object) -> httpx.Client:
        """Get or create a thread-local httpx.Client.

        Sessions are isolated per-instance and per-thread to prevent
        cross-instance session sharing.
        """
        if not hasattr(self._thread_local, "sessions"):
            self._thread_local.sessions = {}

        # Key by (instance_id, thread_id) to isolate per-instance
        key = (id(self), threading.get_ident())
        if key not in self._thread_local.sessions:
            self._thread_local.sessions[key] = httpx.Client(**kwargs)
            logger.debug(
                "thread_local_session_created",
                instance_id=id(self),
                thread_id=threading.get_ident(),
            )
        return self._thread_local.sessions[key]

    def _close_sessions(self) -> None:
        """Close all thread-local sessions for this instance."""
        if not hasattr(self._thread_local, "sessions"):
            return
        instance_id = id(self)
        to_remove = [
            k for k in self._thread_local.sessions
            if k[0] == instance_id
        ]
        for key in to_remove:
            try:
                self._thread_local.sessions[key].close()
            except Exception:
                logger.debug("session_close_failed", key=key)
            del self._thread_local.sessions[key]

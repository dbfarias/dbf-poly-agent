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

    Usage:
        Use _get_session() when making synchronous HTTP calls from
        worker threads (e.g. inside asyncio.to_thread()).
        Use the existing async self._client for normal async operations.
    """

    _thread_local = threading.local()

    def _get_session(self, **kwargs: object) -> httpx.Client:
        """Get or create a thread-local httpx.Client."""
        if not hasattr(self._thread_local, "sessions"):
            self._thread_local.sessions = {}

        thread_id = threading.get_ident()
        if thread_id not in self._thread_local.sessions:
            self._thread_local.sessions[thread_id] = httpx.Client(**kwargs)
            logger.debug(
                "thread_local_session_created",
                thread_id=thread_id,
            )
        return self._thread_local.sessions[thread_id]

    def _close_sessions(self) -> None:
        """Close all thread-local sessions."""
        if hasattr(self._thread_local, "sessions"):
            for _tid, session in self._thread_local.sessions.items():
                try:
                    session.close()
                except Exception:
                    pass
            self._thread_local.sessions.clear()

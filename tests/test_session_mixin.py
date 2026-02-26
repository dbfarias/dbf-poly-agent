"""Tests for ThreadLocalSessionMixin — thread-safe HTTP sessions."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import threading

from bot.polymarket.session_mixin import ThreadLocalSessionMixin


class FakeClient(ThreadLocalSessionMixin):
    """Test class using the mixin."""

    pass


def test_different_threads_get_different_sessions():
    """Each thread should receive its own httpx.Client instance."""
    client = FakeClient()
    sessions: dict[int, object] = {}
    errors: list[str] = []

    def worker(tid: int) -> None:
        try:
            session = client._get_session(timeout=5)
            sessions[tid] = session
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors in threads: {errors}"
    assert len(sessions) == 3

    # All sessions should be distinct objects
    session_ids = [id(s) for s in sessions.values()]
    assert len(set(session_ids)) == 3, "Threads should get different sessions"

    client._close_sessions()


def test_same_thread_reuses_session():
    """Calling _get_session() twice in the same thread returns the same instance."""
    client = FakeClient()

    session1 = client._get_session(timeout=5)
    session2 = client._get_session(timeout=5)

    assert session1 is session2, "Same thread should reuse the session"

    client._close_sessions()


def test_close_sessions_clears_all():
    """_close_sessions() should close and remove all tracked sessions."""
    client = FakeClient()

    # Create a session in the current thread
    session = client._get_session(timeout=5)
    assert session is not None

    client._close_sessions()

    # After closing, a new call should create a fresh session
    new_session = client._get_session(timeout=5)
    assert new_session is not session, "Should create new session after close"

    client._close_sessions()


def test_different_instances_get_different_sessions():
    """Two different mixin instances in the same thread get isolated sessions."""
    client_a = FakeClient()
    client_b = FakeClient()

    session_a = client_a._get_session(timeout=5)
    session_b = client_b._get_session(timeout=5)

    assert session_a is not session_b, "Different instances should get different sessions"

    # Closing one should not affect the other
    client_a._close_sessions()
    session_b_again = client_b._get_session(timeout=5)
    assert session_b_again is session_b, "Other instance session should survive"

    client_b._close_sessions()

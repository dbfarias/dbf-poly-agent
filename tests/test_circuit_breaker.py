"""Tests for bot/utils/circuit_breaker.py — circuit breaker pattern.

Covers all three states (closed, open, half_open), transitions between them,
and the allow_request gating logic. Time-based tests use patched time.monotonic.
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import patch

import pytest

from bot.utils.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_breaker(
    name: str = "test",
    failure_threshold: int = 3,
    recovery_seconds: float = 60.0,
) -> CircuitBreaker:
    """Create a CircuitBreaker with test-friendly defaults."""
    return CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_seconds=recovery_seconds,
    )


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_initial_state_is_closed(self):
        cb = _make_breaker()
        assert cb.state == "closed"

    def test_initial_is_open_false(self):
        cb = _make_breaker()
        assert cb.is_open is False

    def test_initial_allow_request_true(self):
        cb = _make_breaker()
        assert cb.allow_request() is True


# ---------------------------------------------------------------------------
# record_success keeps circuit closed
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    def test_record_success_keeps_closed(self):
        cb = _make_breaker()
        cb.record_success()
        assert cb.state == "closed"

    def test_record_success_resets_failures(self):
        cb = _make_breaker(failure_threshold=5)
        # Accumulate some failures (but not enough to trip)
        cb.record_failure()
        cb.record_failure()
        assert cb._failures == 2
        cb.record_success()
        assert cb._failures == 0
        assert cb.state == "closed"

    def test_record_success_after_many_calls(self):
        cb = _make_breaker()
        for _ in range(10):
            cb.record_success()
        assert cb.state == "closed"
        assert cb._failures == 0


# ---------------------------------------------------------------------------
# Circuit opens after failure_threshold failures
# ---------------------------------------------------------------------------


class TestCircuitOpens:
    def test_opens_at_exact_threshold(self):
        cb = _make_breaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()  # 3rd failure hits threshold
        assert cb._state == "open"

    def test_opens_above_threshold(self):
        cb = _make_breaker(failure_threshold=3)
        for _ in range(5):
            cb.record_failure()
        assert cb._state == "open"

    def test_state_property_returns_open(self):
        """state property should return 'open' right after tripping
        (before recovery_seconds have elapsed)."""
        cb = _make_breaker(failure_threshold=2, recovery_seconds=120.0)
        cb.record_failure()
        cb.record_failure()
        # Patch monotonic so elapsed < recovery_seconds
        with patch("bot.utils.circuit_breaker.time.monotonic", return_value=cb._last_failure_time + 1.0):
            assert cb.state == "open"

    def test_is_open_true_after_trip(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=120.0)
        cb.record_failure()
        cb.record_failure()
        with patch("bot.utils.circuit_breaker.time.monotonic", return_value=cb._last_failure_time + 1.0):
            assert cb.is_open is True

    def test_failure_threshold_of_one(self):
        cb = _make_breaker(failure_threshold=1)
        cb.record_failure()
        assert cb._state == "open"

    def test_below_threshold_stays_closed(self):
        cb = _make_breaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == "closed"


# ---------------------------------------------------------------------------
# Open circuit rejects requests
# ---------------------------------------------------------------------------


class TestOpenCircuitRejects:
    def test_allow_request_false_when_open(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=300.0)
        cb.record_failure()
        cb.record_failure()
        with patch("bot.utils.circuit_breaker.time.monotonic", return_value=cb._last_failure_time + 1.0):
            assert cb.allow_request() is False

    def test_multiple_allow_request_calls_all_rejected(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=300.0)
        cb.record_failure()
        cb.record_failure()
        with patch("bot.utils.circuit_breaker.time.monotonic", return_value=cb._last_failure_time + 1.0):
            for _ in range(5):
                assert cb.allow_request() is False


# ---------------------------------------------------------------------------
# Transition to half_open after recovery_seconds
# ---------------------------------------------------------------------------


class TestHalfOpenTransition:
    def test_becomes_half_open_after_recovery(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        # Simulate time passing beyond recovery_seconds
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 61.0,
        ):
            assert cb.state == "half_open"

    def test_half_open_at_exact_recovery_boundary(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        # Patch monotonic for the failures too, ensuring deterministic timing
        with patch("bot.utils.circuit_breaker.time.monotonic", return_value=1000.0):
            cb.record_failure()
            cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=1000.0 + 60.0,
        ):
            assert cb.state == "half_open"

    def test_still_open_just_before_recovery(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 59.9,
        ):
            assert cb.state == "open"

    def test_allow_request_true_in_half_open(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 61.0,
        ):
            assert cb.allow_request() is True

    def test_is_open_false_in_half_open(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 61.0,
        ):
            assert cb.is_open is False


# ---------------------------------------------------------------------------
# Successful call in half_open resets to closed
# ---------------------------------------------------------------------------


class TestHalfOpenSuccessResets:
    def test_success_in_half_open_closes_circuit(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        # Verify it's open/half_open
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 61.0,
        ):
            assert cb.state == "half_open"
        # Simulate a successful test call
        cb.record_success()
        assert cb.state == "closed"
        assert cb._failures == 0

    def test_allow_request_works_after_reset(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.allow_request() is True


# ---------------------------------------------------------------------------
# Failure in half_open reopens the circuit
# ---------------------------------------------------------------------------


class TestHalfOpenFailureReopens:
    def test_failure_in_half_open_reopens(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        # Trip the circuit
        cb.record_failure()
        cb.record_failure()
        # Simulate half_open state by letting time pass
        # Now record another failure (the test call in half_open failed)
        cb.record_failure()
        # The failure count exceeds threshold, circuit should be open
        assert cb._state == "open"

    def test_reopened_circuit_rejects(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        # Another failure while in open/half_open
        cb.record_failure()
        # Right after the failure, should be open
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 1.0,
        ):
            assert cb.allow_request() is False
            assert cb.state == "open"


# ---------------------------------------------------------------------------
# state property returns correct state in all scenarios
# ---------------------------------------------------------------------------


class TestStateProperty:
    def test_state_closed_initially(self):
        cb = _make_breaker()
        assert cb.state == "closed"

    def test_state_closed_after_sub_threshold_failures(self):
        cb = _make_breaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"

    def test_state_open_after_threshold(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=120.0)
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 1.0,
        ):
            assert cb.state == "open"

    def test_state_half_open_after_recovery(self):
        cb = _make_breaker(failure_threshold=2, recovery_seconds=30.0)
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 31.0,
        ):
            assert cb.state == "half_open"

    def test_state_closed_after_success_resets(self):
        cb = _make_breaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "closed"

    def test_state_internal_vs_property_open(self):
        """Internal _state is 'open' but property may return 'half_open'
        if enough time has elapsed."""
        cb = _make_breaker(failure_threshold=1, recovery_seconds=10.0)
        cb.record_failure()
        assert cb._state == "open"
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 11.0,
        ):
            # Internal is still "open" but property converts to "half_open"
            assert cb._state == "open"
            assert cb.state == "half_open"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_name_is_set(self):
        cb = CircuitBreaker(name="polymarket_api", failure_threshold=5, recovery_seconds=120.0)
        assert cb.name == "polymarket_api"

    def test_default_parameters(self):
        cb = CircuitBreaker(name="test")
        assert cb.failure_threshold == 5
        assert cb.recovery_seconds == 120.0

    def test_success_failure_interleaved(self):
        """Interleaved successes reset the counter, preventing trip."""
        cb = _make_breaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # Reset
        cb.record_failure()
        cb.record_failure()
        # Only 2 consecutive since last reset
        assert cb.state == "closed"

    def test_full_lifecycle(self):
        """Full lifecycle: closed -> open -> half_open -> closed."""
        cb = _make_breaker(failure_threshold=2, recovery_seconds=10.0)

        # 1. Start closed
        assert cb.state == "closed"
        assert cb.allow_request() is True

        # 2. Trip to open
        cb.record_failure()
        cb.record_failure()
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 1.0,
        ):
            assert cb.state == "open"
            assert cb.allow_request() is False

        # 3. Wait for recovery -> half_open
        with patch(
            "bot.utils.circuit_breaker.time.monotonic",
            return_value=cb._last_failure_time + 11.0,
        ):
            assert cb.state == "half_open"
            assert cb.allow_request() is True

        # 4. Success in half_open -> closed
        cb.record_success()
        assert cb.state == "closed"
        assert cb.allow_request() is True

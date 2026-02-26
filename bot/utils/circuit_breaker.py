"""Circuit breaker pattern for external API calls.

Prevents cascading failures by temporarily skipping calls to services
that are experiencing repeated failures.

States:
  CLOSED  — normal operation, calls go through
  OPEN    — circuit tripped, calls are short-circuited (return fallback)
  HALF_OPEN — after recovery_seconds, allow one test call to check recovery
"""

import time

import structlog

logger = structlog.get_logger()


class CircuitBreaker:
    """Circuit breaker that trips after consecutive failures."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 120.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds

        self._failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"

    @property
    def state(self) -> str:
        if self._state == "open":
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_seconds:
                return "half_open"
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def record_success(self) -> None:
        """Record a successful call — resets the failure counter."""
        if self._failures > 0 or self._state != "closed":
            logger.info(
                "circuit_breaker_reset",
                name=self.name,
                previous_failures=self._failures,
            )
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit."""
        self._failures += 1
        self._last_failure_time = time.monotonic()

        if self._failures >= self.failure_threshold:
            if self._state != "open":
                logger.warning(
                    "circuit_breaker_opened",
                    name=self.name,
                    failures=self._failures,
                    recovery_seconds=self.recovery_seconds,
                )
            self._state = "open"

    def allow_request(self) -> bool:
        """Check if a request should be allowed through.

        Returns True if the circuit is closed or half-open (test call).
        Returns False if the circuit is open (skip the call).
        """
        state = self.state
        if state == "closed":
            return True
        if state == "half_open":
            # Allow one test call
            logger.info("circuit_breaker_half_open_test", name=self.name)
            return True
        return False

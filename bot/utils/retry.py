"""Retry utilities with exponential backoff."""

import asyncio
import functools
from collections.abc import Callable

import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()


def _log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempts."""
    logger.warning(
        "retry_attempt",
        attempt=retry_state.attempt_number,
        fn=retry_state.fn.__name__ if retry_state.fn else "unknown",
        error=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )


# Retry decorator for API calls
api_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((Exception,)),
    before_sleep=_log_retry,
    reraise=True,
)


def async_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Async retry decorator with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        wait = min(max_wait, min_wait * (2 ** (attempt - 1)))
                        logger.warning(
                            "async_retry",
                            fn=func.__name__,
                            attempt=attempt,
                            wait=wait,
                            error=str(e),
                        )
                        await asyncio.sleep(wait)
            raise last_exc

        return wrapper

    return decorator

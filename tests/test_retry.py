"""Tests for async_retry decorator."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bot.utils.retry import async_retry


# ------------------------------------------------------------------
# async_retry succeeds on first try
# ------------------------------------------------------------------
async def test_retry_succeeds_on_first_try():
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def always_ok():
        nonlocal call_count
        call_count += 1
        return "success"

    result = await always_ok()

    assert result == "success"
    assert call_count == 1


# ------------------------------------------------------------------
# async_retry retries then succeeds
# ------------------------------------------------------------------
async def test_retry_retries_then_succeeds():
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def fail_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError(f"attempt {call_count}")
        return "recovered"

    result = await fail_twice()

    assert result == "recovered"
    assert call_count == 3


async def test_retry_retries_once_then_succeeds():
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def fail_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("first attempt fails")
        return 42

    result = await fail_once()

    assert result == 42
    assert call_count == 2


# ------------------------------------------------------------------
# async_retry exhausts all attempts then raises
# ------------------------------------------------------------------
async def test_retry_exhausts_all_attempts_then_raises():
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def always_fail():
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"boom {call_count}")

    with pytest.raises(RuntimeError, match="boom 3"):
        await always_fail()

    assert call_count == 3


async def test_retry_single_attempt_raises_immediately():
    call_count = 0

    @async_retry(max_attempts=1, min_wait=0.01, max_wait=0.1)
    async def one_shot():
        nonlocal call_count
        call_count += 1
        raise TypeError("only chance")

    with pytest.raises(TypeError, match="only chance"):
        await one_shot()

    assert call_count == 1


# ------------------------------------------------------------------
# backoff timing (verify waits increase)
# ------------------------------------------------------------------
async def test_backoff_timing_increases():
    """Verify that sleep durations follow exponential backoff."""
    sleep_durations = []

    @async_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def always_fail():
        raise RuntimeError("fail")

    with patch("bot.utils.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.return_value = None

        with pytest.raises(RuntimeError):
            await always_fail()

        sleep_durations = [call.args[0] for call in mock_sleep.call_args_list]

    # 3 sleeps for 4 attempts (no sleep after last failure)
    assert len(sleep_durations) == 3

    # Verify exponential backoff: min_wait * 2^(attempt-1)
    # attempt 1: 1.0 * 2^0 = 1.0
    # attempt 2: 1.0 * 2^1 = 2.0
    # attempt 3: 1.0 * 2^2 = 4.0
    assert sleep_durations[0] == pytest.approx(1.0)
    assert sleep_durations[1] == pytest.approx(2.0)
    assert sleep_durations[2] == pytest.approx(4.0)

    # Each wait is strictly larger than the previous
    for i in range(1, len(sleep_durations)):
        assert sleep_durations[i] > sleep_durations[i - 1]


async def test_backoff_capped_at_max_wait():
    """Verify that wait time is capped at max_wait."""
    sleep_durations = []

    @async_retry(max_attempts=5, min_wait=5.0, max_wait=10.0)
    async def always_fail():
        raise RuntimeError("fail")

    with patch("bot.utils.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.return_value = None

        with pytest.raises(RuntimeError):
            await always_fail()

        sleep_durations = [call.args[0] for call in mock_sleep.call_args_list]

    # 4 sleeps for 5 attempts
    assert len(sleep_durations) == 4

    # attempt 1: min(10, 5*1) = 5
    # attempt 2: min(10, 5*2) = 10
    # attempt 3: min(10, 5*4) = 10 (capped)
    # attempt 4: min(10, 5*8) = 10 (capped)
    assert sleep_durations[0] == pytest.approx(5.0)
    assert sleep_durations[1] == pytest.approx(10.0)
    assert sleep_durations[2] == pytest.approx(10.0)
    assert sleep_durations[3] == pytest.approx(10.0)

    for d in sleep_durations:
        assert d <= 10.0


# ------------------------------------------------------------------
# retry passes through return value
# ------------------------------------------------------------------
async def test_retry_passes_through_return_value():
    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def return_dict():
        return {"key": "value", "count": 99}

    result = await return_dict()
    assert result == {"key": "value", "count": 99}


async def test_retry_passes_through_none_return():
    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
    async def return_none():
        return None

    result = await return_none()
    assert result is None


async def test_retry_passes_through_list_return():
    @async_retry(max_attempts=2, min_wait=0.01, max_wait=0.1)
    async def return_list():
        return [1, 2, 3]

    result = await return_list()
    assert result == [1, 2, 3]


# ------------------------------------------------------------------
# Additional edge cases
# ------------------------------------------------------------------
async def test_retry_only_catches_specified_exceptions():
    """When exceptions tuple is restricted, other exceptions propagate immediately."""
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1, exceptions=(ValueError,))
    async def raise_type_error():
        nonlocal call_count
        call_count += 1
        raise TypeError("not retried")

    with pytest.raises(TypeError, match="not retried"):
        await raise_type_error()

    # Should have only been called once since TypeError is not in exceptions tuple
    assert call_count == 1


async def test_retry_catches_specified_exception_subclass():
    call_count = 0

    @async_retry(max_attempts=3, min_wait=0.01, max_wait=0.1, exceptions=(ConnectionError,))
    async def fail_then_ok():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionResetError("subclass of ConnectionError")
        return "done"

    result = await fail_then_ok()
    assert result == "done"
    assert call_count == 3


async def test_retry_preserves_function_name():
    """functools.wraps should preserve the original function name."""

    @async_retry(max_attempts=2, min_wait=0.01, max_wait=0.1)
    async def my_special_function():
        return True

    assert my_special_function.__name__ == "my_special_function"

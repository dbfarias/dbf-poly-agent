"""Tests for DataApiClient."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.polymarket.data_api import DataApiClient
from bot.polymarket.types import PositionInfo


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_response(*, json_data, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    response = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://fake"),
    )
    return response


# ------------------------------------------------------------------
# Initialization creates httpx client
# ------------------------------------------------------------------
async def test_initialize_creates_httpx_client():
    client = DataApiClient()
    assert client._client is None

    await client.initialize()

    assert client._client is not None
    assert isinstance(client._client, httpx.AsyncClient)
    await client.close()


async def test_close_closes_httpx_client():
    client = DataApiClient()
    await client.initialize()
    inner = client._client

    await client.close()
    assert inner.is_closed


async def test_close_when_no_client():
    """Closing without initializing should not raise."""
    client = DataApiClient()
    await client.close()  # no-op, should not raise


# ------------------------------------------------------------------
# Paper mode returns empty/default values
# ------------------------------------------------------------------
@patch("bot.polymarket.data_api.settings")
async def test_get_positions_paper_mode_returns_empty(mock_settings):
    mock_settings.is_paper = True
    client = DataApiClient()

    result = await client.get_positions.__wrapped__(client, address="0xABC")
    assert result == []


@patch("bot.polymarket.data_api.settings")
async def test_get_balance_paper_mode_returns_initial_bankroll(mock_settings):
    mock_settings.is_paper = True
    mock_settings.initial_bankroll = 5.0
    client = DataApiClient()

    result = await client.get_balance.__wrapped__(client, address="0xABC")
    assert result == 5.0


@patch("bot.polymarket.data_api.settings")
async def test_get_trade_history_paper_mode_returns_empty(mock_settings):
    mock_settings.is_paper = True
    client = DataApiClient()

    result = await client.get_trade_history.__wrapped__(client, address="0xABC")
    assert result == []


# ------------------------------------------------------------------
# get_positions returns positions
# ------------------------------------------------------------------
@patch("bot.polymarket.data_api.settings")
async def test_get_positions_returns_positions(mock_settings):
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None  # use explicit address

    api_data = [
        {
            "conditionId": "cond-1",
            "asset": "token-1",
            "outcome": "Yes",
            "title": "Will it rain?",
            "size": "10.0",
            "avgPrice": "0.55",
            "curPrice": "0.60",
            "cashPnl": "0.50",
        },
        {
            "conditionId": "cond-2",
            "asset": "token-2",
            "outcome": "No",
            "title": "Will BTC hit 100k?",
            "size": "5.0",
            "avgPrice": "0.30",
            "curPrice": "0.25",
            "cashPnl": "-0.25",
        },
    ]

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=_make_response(json_data=api_data))

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_positions.__wrapped__(client, address="0xWALLET")

    assert len(result) == 2
    assert isinstance(result[0], PositionInfo)
    assert result[0].market_id == "cond-1"
    assert result[0].token_id == "token-1"
    assert result[0].outcome == "Yes"
    assert result[0].question == "Will it rain?"
    assert result[0].size == 10.0
    assert result[0].avg_price == 0.55
    assert result[0].current_price == 0.60
    assert result[0].unrealized_pnl == 0.50

    assert result[1].market_id == "cond-2"
    assert result[1].unrealized_pnl == -0.25


@patch("bot.polymarket.data_api.settings")
async def test_get_positions_no_address_returns_empty(mock_settings):
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    client = DataApiClient()

    result = await client.get_positions.__wrapped__(client, address=None)
    assert result == []


@patch("bot.polymarket.data_api.settings")
async def test_get_positions_http_error_returns_empty(mock_settings):
    mock_settings.is_paper = False

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"error": "bad"}, status_code=500)
    )

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_positions.__wrapped__(client, address="0xWALLET")
    assert result == []


# ------------------------------------------------------------------
# get_balance returns float
# ------------------------------------------------------------------
@patch("bot.polymarket.data_api.settings")
async def test_get_balance_returns_float(mock_settings):
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"balance": 42.5})
    )

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_balance.__wrapped__(client, address="0xWALLET")
    assert result == 42.5
    assert isinstance(result, float)


@patch("bot.polymarket.data_api.settings")
async def test_get_balance_no_address_returns_zero(mock_settings):
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    client = DataApiClient()

    result = await client.get_balance.__wrapped__(client, address=None)
    assert result == 0.0


@patch("bot.polymarket.data_api.settings")
async def test_get_balance_http_error_returns_zero(mock_settings):
    mock_settings.is_paper = False

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"error": "nope"}, status_code=503)
    )

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_balance.__wrapped__(client, address="0xWALLET")
    assert result == 0.0


# ------------------------------------------------------------------
# get_trade_history
# ------------------------------------------------------------------
@patch("bot.polymarket.data_api.settings")
async def test_get_trade_history_returns_list(mock_settings):
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    trades_data = [{"id": "t1", "side": "BUY"}, {"id": "t2", "side": "SELL"}]

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=_make_response(json_data=trades_data))

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_trade_history.__wrapped__(client, address="0xWALLET")
    assert len(result) == 2
    assert result[0]["id"] == "t1"

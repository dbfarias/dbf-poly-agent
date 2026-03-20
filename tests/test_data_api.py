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
async def test_get_positions_http_error_propagates(mock_settings):
    """HTTP errors propagate so @async_retry can retry them."""
    mock_settings.is_paper = False

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"error": "bad"}, status_code=500)
    )

    client = DataApiClient()
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_positions.__wrapped__(client, address="0xWALLET")


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
async def test_get_balance_http_error_propagates(mock_settings):
    """HTTP errors propagate so @async_retry can retry them."""
    mock_settings.is_paper = False

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"error": "nope"}, status_code=503)
    )

    client = DataApiClient()
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_balance.__wrapped__(client, address="0xWALLET")


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


# ------------------------------------------------------------------
# Retry behavior — HTTP errors propagate, parse errors handled
# ------------------------------------------------------------------


@patch("bot.polymarket.data_api.settings")
async def test_get_trade_history_http_error_propagates(mock_settings):
    """HTTP errors propagate so @async_retry can retry them."""
    mock_settings.is_paper = False

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=_make_response(json_data={"error": "fail"}, status_code=502)
    )

    client = DataApiClient()
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_trade_history.__wrapped__(client, address="0xWALLET")


@patch("bot.polymarket.data_api.settings")
async def test_get_positions_parse_error_skips_bad_item(mock_settings):
    """Malformed position data is skipped, valid ones still returned."""
    mock_settings.is_paper = False

    api_data = [
        {
            "conditionId": "cond-ok",
            "asset": "token-ok",
            "outcome": "Yes",
            "title": "Good market",
            "size": "10.0",
            "avgPrice": "0.55",
            "curPrice": "0.60",
            "cashPnl": "0.50",
        },
        {
            "conditionId": "cond-bad",
            "asset": "token-bad",
            "outcome": "No",
            "title": "Bad market",
            "size": "not_a_number",  # Will cause ValueError in float()
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
    # First item OK, second has bad "size" so skipped
    assert len(result) == 1
    assert result[0].market_id == "cond-ok"


# ------------------------------------------------------------------
# No-outcome price inversion (bug fix)
# ------------------------------------------------------------------

@patch("bot.polymarket.data_api.settings")
async def test_get_positions_no_outcome_inverts_cur_price(mock_settings):
    """For No outcome positions, current_price must be 1 - curPrice.

    The Polymarket Data API `curPrice` field is always the YES-token price
    regardless of which outcome is held.  A No position with curPrice=0.21
    (YES price ~21¢) should have current_price=0.79 (No token ~79¢).
    """
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    api_data = [
        {
            "conditionId": "cond-no",
            "asset": "token-no",
            "outcome": "No",
            "title": "US forces enter Iran by March 31?",
            "size": "5.0",
            "avgPrice": "0.27",
            "curPrice": "0.205",   # YES price reported by API
            "cashPnl": "-0.33",
        },
    ]

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=_make_response(json_data=api_data))

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_positions.__wrapped__(client, address="0xWALLET")

    assert len(result) == 1
    pos = result[0]
    assert pos.outcome == "No"
    # current_price should be the No token price: 1 - 0.205 = 0.795
    assert pos.current_price == pytest.approx(0.795)
    # avg_price unchanged (avgPrice field is the actual cost basis)
    assert pos.avg_price == pytest.approx(0.27)


@patch("bot.polymarket.data_api.settings")
async def test_get_positions_yes_outcome_price_unchanged(mock_settings):
    """For Yes outcome positions, current_price equals curPrice directly."""
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    api_data = [
        {
            "conditionId": "cond-yes",
            "asset": "token-yes",
            "outcome": "Yes",
            "title": "Will BTC hit 100k?",
            "size": "10.0",
            "avgPrice": "0.70",
            "curPrice": "0.85",
            "cashPnl": "1.50",
        },
    ]

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=_make_response(json_data=api_data))

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_positions.__wrapped__(client, address="0xWALLET")

    assert len(result) == 1
    pos = result[0]
    assert pos.outcome == "Yes"
    # Yes outcome: current_price == curPrice
    assert pos.current_price == pytest.approx(0.85)


@patch("bot.polymarket.data_api.settings")
async def test_get_positions_no_outcome_case_insensitive(mock_settings):
    """No-outcome inversion applies regardless of outcome string casing."""
    mock_settings.is_paper = False
    mock_settings.poly_private_key = None

    api_data = [
        {
            "conditionId": "cond-no-lower",
            "asset": "token-no-lower",
            "outcome": "no",          # lowercase
            "title": "Some market",
            "size": "3.0",
            "avgPrice": "0.40",
            "curPrice": "0.30",
            "cashPnl": "0.0",
        },
    ]

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=_make_response(json_data=api_data))

    client = DataApiClient()
    client._client = mock_http

    result = await client.get_positions.__wrapped__(client, address="0xWALLET")

    assert len(result) == 1
    # 1 - 0.30 = 0.70
    assert result[0].current_price == pytest.approx(0.70)

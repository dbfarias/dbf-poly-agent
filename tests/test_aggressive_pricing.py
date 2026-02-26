"""Tests for aggressive limit order pricing (spread_cross_offset)."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import patch

import pytest

from bot.polymarket.client import PolymarketClient
from bot.polymarket.types import OrderSide


@pytest.fixture
def paper_client():
    """PolymarketClient in paper mode."""
    with patch("bot.polymarket.client.settings") as mock_settings:
        mock_settings.is_paper = True
        mock_settings.poly_private_key = ""
        client = PolymarketClient()
        client._initialized = True
        yield client


@pytest.mark.asyncio
async def test_buy_with_spread_cross_offset(paper_client):
    """BUY price should increase by the offset for faster fills."""
    result = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.BUY,
        price=0.50,
        size=10.0,
        spread_cross_offset=0.02,
    )
    assert result["price"] == 0.52
    assert result["side"] == "BUY"


@pytest.mark.asyncio
async def test_sell_with_spread_cross_offset(paper_client):
    """SELL price should decrease by the offset for faster fills."""
    result = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.SELL,
        price=0.50,
        size=10.0,
        spread_cross_offset=0.02,
    )
    assert result["price"] == 0.48
    assert result["side"] == "SELL"


@pytest.mark.asyncio
async def test_offset_capped_at_max_price(paper_client):
    """BUY with offset should not exceed 0.99."""
    result = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.BUY,
        price=0.98,
        size=10.0,
        spread_cross_offset=0.05,
    )
    assert result["price"] <= 0.99


@pytest.mark.asyncio
async def test_offset_floored_at_min_price(paper_client):
    """SELL with offset should not go below 0.01."""
    result = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.SELL,
        price=0.03,
        size=200.0,
        spread_cross_offset=0.05,
    )
    assert result["price"] >= 0.01


@pytest.mark.asyncio
async def test_zero_offset_no_change(paper_client):
    """Zero offset should not alter the price (default behavior)."""
    result_default = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.BUY,
        price=0.50,
        size=10.0,
    )
    result_zero = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.BUY,
        price=0.50,
        size=10.0,
        spread_cross_offset=0.0,
    )
    assert result_default["price"] == result_zero["price"]


@pytest.mark.asyncio
async def test_re_rounds_to_tick_size(paper_client):
    """Price after offset should be rounded to tick size (0.01)."""
    # 0.50 + 0.015 = 0.515 -> ceil to 0.52 for BUY
    result = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.BUY,
        price=0.50,
        size=10.0,
        spread_cross_offset=0.015,
    )
    # Must be a multiple of TICK_SIZE (rounded to 2 decimal places)
    assert result["price"] == 0.52  # ceil(0.515 / 0.01) * 0.01
    assert round(result["price"], 2) == result["price"]

    # 0.50 - 0.015 = 0.485 -> floor to 0.48 for SELL
    result_sell = await paper_client.place_order(
        token_id="token_abc",
        side=OrderSide.SELL,
        price=0.50,
        size=10.0,
        spread_cross_offset=0.015,
    )
    assert result_sell["price"] == 0.48  # floor(0.485 / 0.01) * 0.01
    assert round(result_sell["price"], 2) == result_sell["price"]

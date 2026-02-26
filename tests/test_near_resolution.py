"""Tests for near-resolution priority improvements.

Covers:
- Granular resolution bonus scale in value_betting
- Near-certainty detector (price >= 0.80 + hours <= 48)
- Confidence boosts for near-resolution markets
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.value_betting import ValueBettingStrategy
from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy() -> ValueBettingStrategy:
    return ValueBettingStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_market(
    hours_to_resolution: float = 48.0,
    yes_price: float = 0.45,
    no_price: float | None = None,
    market_id: str = "mkt1",
    question: str = "Will X happen by tomorrow?",
) -> GammaMarket:
    if no_price is None:
        no_price = 1.0 - yes_price
    end = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
    end_date_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return GammaMarket(
        id=market_id,
        question=question,
        endDateIso=end_date_iso,
        outcomes=["Yes", "No"],
        outcomePrices=f"[{yes_price},{no_price}]",
        clobTokenIds='["token_yes","token_no"]',
    )


def _strong_book(bid_volume: float = 500.0, ask_volume: float = 200.0) -> OrderBook:
    """Order book with strong bid imbalance (>15%)."""
    return OrderBook(
        market="mkt1",
        bids=[OrderBookEntry(price=0.45, size=bid_volume / 5) for _ in range(5)],
        asks=[OrderBookEntry(price=0.55, size=ask_volume / 5) for _ in range(5)],
    )


# ---------------------------------------------------------------------------
# Resolution bonus scale tests
# ---------------------------------------------------------------------------


class TestResolutionBonusScale:
    """Test the granular resolution bonus (6h, 12h, 24h, 48h, 72h)."""

    @pytest.mark.asyncio
    async def test_6h_gets_highest_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=5.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.6

    @pytest.mark.asyncio
    async def test_12h_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=10.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.4

    @pytest.mark.asyncio
    async def test_24h_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=20.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.25

    @pytest.mark.asyncio
    async def test_48h_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=36.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.15

    @pytest.mark.asyncio
    async def test_72h_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=60.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.05

    @pytest.mark.asyncio
    async def test_beyond_72h_no_bonus(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=100.0, yes_price=0.45)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["resolution_bonus"] == 1.0


# ---------------------------------------------------------------------------
# Near-certainty detector tests
# ---------------------------------------------------------------------------


class TestNearCertaintyDetector:
    """Test near-certainty boost for extreme prices + short resolution."""

    @pytest.mark.asyncio
    async def test_high_yes_price_near_resolution_gets_boost(self):
        strat = _make_strategy()
        # YES at 0.85 is in range; book imbalance favors YES
        book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.84, size=100.0) for _ in range(5)],
            asks=[OrderBookEntry(price=0.86, size=40.0) for _ in range(5)],
        )
        strat.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=24.0, yes_price=0.85)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.metadata["near_certainty"] is True

    @pytest.mark.asyncio
    async def test_no_boost_beyond_48h(self):
        strat = _make_strategy()
        book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.84, size=100.0) for _ in range(5)],
            asks=[OrderBookEntry(price=0.86, size=40.0) for _ in range(5)],
        )
        strat.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=72.0, yes_price=0.85)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        if signal is not None:
            assert signal.metadata["near_certainty"] is False

    @pytest.mark.asyncio
    async def test_no_boost_mid_price(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market = _make_market(hours_to_resolution=24.0, yes_price=0.55)

        signal = await strat._evaluate_market(market, max_hours=168.0)
        if signal is not None:
            assert signal.metadata["near_certainty"] is False


# ---------------------------------------------------------------------------
# Confidence boost tests
# ---------------------------------------------------------------------------


class TestConfidenceBoosts:
    """Test granular confidence boosts for near-resolution markets."""

    @pytest.mark.asyncio
    async def test_6h_confidence_highest(self):
        strat = _make_strategy()
        strat.get_order_book = AsyncMock(return_value=_strong_book())
        market_6h = _make_market(hours_to_resolution=5.0, yes_price=0.45)
        market_72h = _make_market(hours_to_resolution=60.0, yes_price=0.45, market_id="mkt2")

        signal_6h = await strat._evaluate_market(market_6h, max_hours=168.0)
        signal_72h = await strat._evaluate_market(market_72h, max_hours=168.0)

        assert signal_6h is not None
        assert signal_72h is not None
        assert signal_6h.confidence > signal_72h.confidence

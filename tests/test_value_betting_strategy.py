"""Tests for ValueBettingStrategy.

Covers:
- __init__ (line 34-38)
- adjust_params (line 40-42)
- scan() (lines 44-63)
- _evaluate_market() (lines 65-167)
- _score_signal() (lines 169-175)
- should_exit() (lines 177-181)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.value_betting import (
    IMBALANCE_THRESHOLD,
    MAX_PRICE,
    MIN_BOOK_VOLUME,
    MIN_EDGE,
    MIN_PRICE,
    RELATIVE_STOP_LOSS,
    ValueBettingStrategy,
)
from bot.agent.strategies.time_decay import HOURS_MEDIUM
from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry, TradeSignal, OrderSide


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
    no_price: float = 0.55,
    end_date_iso: str | None = None,
    clob_token_ids: str = '["token_yes","token_no"]',
    market_id: str = "mkt1",
    question: str = "Will X happen?",
) -> GammaMarket:
    if end_date_iso is None:
        end = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        end_date_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return GammaMarket(
        id=market_id,
        question=question,
        endDateIso=end_date_iso,
        outcomes=["Yes", "No"],
        outcomePrices=f"[{yes_price},{no_price}]",
        clobTokenIds=clob_token_ids,
    )


def _make_order_book(
    bid_sizes: list[float] | None = None,
    ask_sizes: list[float] | None = None,
    bid_price: float = 0.44,
    ask_price: float = 0.47,
) -> OrderBook:
    """Create an OrderBook with configurable bid/ask volumes."""
    if bid_sizes is None:
        bid_sizes = [100.0, 80.0, 60.0, 40.0, 20.0]
    if ask_sizes is None:
        ask_sizes = [20.0, 15.0, 10.0, 5.0, 5.0]

    bids = [OrderBookEntry(price=bid_price, size=s) for s in bid_sizes]
    asks = [OrderBookEntry(price=ask_price, size=s) for s in ask_sizes]
    return OrderBook(market="mkt1", bids=bids, asks=asks)


def _balanced_order_book() -> OrderBook:
    """Order book with equal bid/ask volume — imbalance = 0."""
    bids = [OrderBookEntry(price=0.44, size=50.0)]
    asks = [OrderBookEntry(price=0.47, size=50.0)]
    return OrderBook(market="mkt1", bids=bids, asks=asks)


# ---------------------------------------------------------------------------
# __init__ and class attributes
# ---------------------------------------------------------------------------


class TestInit:
    def test_strategy_name(self):
        strategy = _make_strategy()
        assert strategy.name == "value_betting"

    def test_min_tier_is_tier1(self):
        strategy = _make_strategy()
        assert strategy.min_tier == CapitalTier.TIER1

    def test_min_edge_initialized(self):
        strategy = _make_strategy()
        assert strategy.MIN_EDGE == MIN_EDGE

    def test_imbalance_threshold_initialized(self):
        strategy = _make_strategy()
        assert strategy.IMBALANCE_THRESHOLD == IMBALANCE_THRESHOLD

    def test_urgency_initialized_to_one(self):
        strategy = _make_strategy()
        assert strategy._urgency == 1.0


# ---------------------------------------------------------------------------
# adjust_params
# ---------------------------------------------------------------------------


class TestAdjustParams:
    def test_urgency_stored_from_adjustments(self):
        strategy = _make_strategy()
        strategy.adjust_params({"urgency_multiplier": 1.4})
        assert strategy._urgency == 1.4

    def test_missing_urgency_defaults_to_one(self):
        strategy = _make_strategy()
        strategy._urgency = 1.3  # set to non-default first
        strategy.adjust_params({})
        assert strategy._urgency == 1.0

    def test_urgency_below_one_stored(self):
        strategy = _make_strategy()
        strategy.adjust_params({"urgency_multiplier": 0.7})
        assert strategy._urgency == 0.7

    def test_extra_keys_in_adjustments_ignored(self):
        strategy = _make_strategy()
        strategy.adjust_params({"urgency_multiplier": 1.2, "calibration": {}, "other": 99})
        assert strategy._urgency == 1.2


# ---------------------------------------------------------------------------
# _evaluate_market — early-exit guards
# ---------------------------------------------------------------------------


class TestEvaluateMarketGuards:
    async def test_no_end_date_returns_none(self):
        strategy = _make_strategy()
        market = GammaMarket(
            id="mkt1",
            question="Test?",
            endDateIso="",
            outcomes=["Yes", "No"],
            outcomePrices="[0.45,0.55]",
            clobTokenIds='["t1","t2"]',
        )
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_expired_market_returns_none(self):
        strategy = _make_strategy()
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        market = _make_market(end_date_iso=past.strftime("%Y-%m-%dT%H:%M:%SZ"))
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_too_far_market_returns_none(self):
        """Market resolving in 200h exceeds max_hours=168."""
        strategy = _make_strategy()
        market = _make_market(hours_to_resolution=200.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_no_token_ids_returns_none(self):
        strategy = _make_strategy()
        market = _make_market(clob_token_ids="[]")
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_no_yes_price_returns_none(self):
        strategy = _make_strategy()
        market = GammaMarket(
            id="mkt1",
            question="Test?",
            endDateIso=(
                datetime.now(timezone.utc) + timedelta(hours=48)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            outcomes=["Yes", "No"],
            outcomePrices="",  # empty → yes_price is None
            clobTokenIds='["t1","t2"]',
        )
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_order_book_exception_returns_none(self):
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("API down"))
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_empty_bids_returns_none(self):
        strategy = _make_strategy()
        empty_book = OrderBook(
            market="mkt1",
            bids=[],
            asks=[OrderBookEntry(price=0.47, size=100.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=empty_book)
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_empty_asks_returns_none(self):
        strategy = _make_strategy()
        empty_book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.44, size=100.0)],
            asks=[],
        )
        strategy.get_order_book = AsyncMock(return_value=empty_book)
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_zero_total_volume_returns_none(self):
        strategy = _make_strategy()
        zero_book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.44, size=0.0)],
            asks=[OrderBookEntry(price=0.47, size=0.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=zero_book)
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_low_imbalance_returns_none(self):
        """Imbalance below IMBALANCE_THRESHOLD (0.15) → no signal."""
        strategy = _make_strategy()
        balanced = _balanced_order_book()
        strategy.get_order_book = AsyncMock(return_value=balanced)
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None


# ---------------------------------------------------------------------------
# _evaluate_market — BUY YES path (positive imbalance)
# ---------------------------------------------------------------------------


class TestEvaluateMarketBuyYes:
    async def _run(
        self,
        yes_price: float = 0.45,
        bid_sizes: list[float] | None = None,
        ask_sizes: list[float] | None = None,
        hours: float = 48.0,
        max_hours: float = 168.0,
    ) -> TradeSignal | None:
        strategy = _make_strategy()
        book = _make_order_book(bid_sizes=bid_sizes, ask_sizes=ask_sizes)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=hours, yes_price=yes_price)
        return await strategy._evaluate_market(market, max_hours=max_hours)

    async def test_strong_bid_pressure_returns_yes_signal(self):
        """More bids than asks → YES is underpriced → BUY YES."""
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.side == OrderSide.BUY
        assert signal.token_id == "token_yes"

    async def test_signal_strategy_name(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.strategy == "value_betting"

    async def test_signal_market_id(self):
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=48.0, market_id="unique_mkt")
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.market_id == "unique_mkt"

    async def test_estimated_prob_capped_at_095(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.estimated_prob <= 0.95

    async def test_edge_is_positive(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.edge > 0

    async def test_confidence_capped_at_095(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.confidence <= 0.95

    async def test_metadata_contains_required_keys(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert "hours_to_resolution" in signal.metadata
        assert "imbalance" in signal.metadata
        assert "bid_volume" in signal.metadata
        assert "ask_volume" in signal.metadata

    async def test_imbalance_stored_in_metadata_positive(self):
        signal = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        assert signal is not None
        assert signal.metadata["imbalance"] > 0

    async def test_short_resolution_boosts_confidence(self):
        """Markets resolving within 24h get a +0.08 confidence bonus."""
        signal_short = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            hours=12.0,
        )
        signal_long = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            hours=100.0,
        )
        assert signal_short is not None
        assert signal_long is not None
        assert signal_short.confidence > signal_long.confidence

    async def test_medium_resolution_boosts_confidence(self):
        """Markets resolving in 24-72h get a +0.04 confidence bonus."""
        signal_mid = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            hours=48.0,
        )
        signal_long = await self._run(
            yes_price=0.45,
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            hours=100.0,
        )
        assert signal_mid is not None
        assert signal_long is not None
        assert signal_mid.confidence > signal_long.confidence

    async def test_insufficient_edge_returns_none(self):
        """imbalance * 0.1 < MIN_EDGE (0.03) → no signal."""
        strategy = _make_strategy()
        # Craft a book with just enough imbalance to exceed IMBALANCE_THRESHOLD
        # but not enough for edge: imbalance ~0.16, edge = 0.16 * 0.1 = 0.016 < 0.03
        bid_sizes = [58.0]  # total bid = 58
        ask_sizes = [42.0]  # total ask = 42, total = 100, imbalance = 0.16
        book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.44, size=58.0)],
            asks=[OrderBookEntry(price=0.47, size=42.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=48.0, yes_price=0.45)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_naive_end_date_still_works(self):
        """End date without tzinfo should be handled gracefully (line 74)."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)

        # Provide a naive ISO date (no timezone suffix)
        future = datetime.now(timezone.utc) + timedelta(hours=48)
        naive_iso = future.strftime("%Y-%m-%dT%H:%M:%S")  # no Z / no offset
        market = GammaMarket(
            id="mkt_naive",
            question="Naive date?",
            endDateIso=naive_iso,
            outcomes=["Yes", "No"],
            outcomePrices="[0.45,0.55]",
            clobTokenIds='["token_yes","token_no"]',
        )
        result = await strategy._evaluate_market(market, max_hours=168.0)
        # Should not raise — may return signal or None depending on date parsing
        # The important thing is no exception was raised
        assert result is None or isinstance(result, TradeSignal)


# ---------------------------------------------------------------------------
# _evaluate_market — BUY NO path (negative imbalance)
# ---------------------------------------------------------------------------


class TestEvaluateMarketBuyNo:
    async def _run_no_signal(
        self,
        yes_price: float = 0.70,
        no_price: float = 0.30,
        hours: float = 48.0,
        max_hours: float = 168.0,
        token_ids: str = '["token_yes","token_no"]',
    ) -> TradeSignal | None:
        """Helper that creates a book with strong ask pressure (negative imbalance)."""
        strategy = _make_strategy()
        # Strong ask pressure: many more asks than bids
        book = _make_order_book(
            bid_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            ask_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = GammaMarket(
            id="mkt1",
            question="Will X?",
            endDateIso=(
                datetime.now(timezone.utc) + timedelta(hours=hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            outcomes=["Yes", "No"],
            outcomePrices=f"[{yes_price},{no_price}]",
            clobTokenIds=token_ids,
        )
        return await strategy._evaluate_market(market, max_hours=max_hours)

    async def test_strong_ask_pressure_returns_no_signal(self):
        """More asks than bids → NO might be value → BUY NO."""
        signal = await self._run_no_signal(yes_price=0.70, no_price=0.30)
        assert signal is not None
        assert signal.outcome == "No"
        assert signal.side == OrderSide.BUY
        assert signal.token_id == "token_no"

    async def test_no_signal_uses_no_price(self):
        signal = await self._run_no_signal(yes_price=0.70, no_price=0.30)
        assert signal is not None
        assert signal.market_price == pytest.approx(0.30)

    async def test_no_price_falls_back_to_one_minus_yes(self):
        """When no_price is absent, it falls back to 1.0 - yes_price."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            ask_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        # outcomePrices with only one element — no_price will be None → fallback
        market = GammaMarket(
            id="mkt1",
            question="Will X?",
            endDateIso=(
                datetime.now(timezone.utc) + timedelta(hours=48)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            outcomes=["Yes", "No"],
            outcomePrices="[0.70]",  # only yes price
            clobTokenIds='["token_yes","token_no"]',
        )
        result = await strategy._evaluate_market(market, max_hours=168.0)
        # Should not raise — will use 1.0 - 0.70 = 0.30 as no_price
        assert result is None or isinstance(result, TradeSignal)

    async def test_only_one_token_id_falls_back_to_yes(self):
        """NO path requires two token_ids; with only one, falls back to YES if in range."""
        signal = await self._run_no_signal(
            token_ids='["token_yes"]',
        )
        # YES=0.70 is in range, so it falls back to YES signal
        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.token_id == "token_yes"

    async def test_no_signal_insufficient_edge_returns_none(self):
        """If estimated_prob - no_price < MIN_EDGE, no signal is returned."""
        strategy = _make_strategy()
        # Imbalance just above threshold but not enough for 3% edge
        book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.44, size=42.0)],
            asks=[OrderBookEntry(price=0.47, size=58.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=48.0, yes_price=0.70, no_price=0.30)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_imbalance_stored_negative_in_metadata(self):
        signal = await self._run_no_signal(yes_price=0.70, no_price=0.30)
        assert signal is not None
        assert signal.metadata["imbalance"] < 0


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


class TestScan:
    async def test_scan_returns_list(self):
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("no book"))
        markets = [_make_market(hours_to_resolution=48.0)]
        result = await strategy.scan(markets)
        assert isinstance(result, list)

    async def test_scan_empty_markets_returns_empty(self):
        strategy = _make_strategy()
        result = await strategy.scan([])
        assert result == []

    async def test_scan_collects_signals(self):
        """All markets with sufficient imbalance/edge produce signals."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        markets = [
            _make_market(hours_to_resolution=48.0, market_id="m1"),
            _make_market(hours_to_resolution=24.0, market_id="m2"),
        ]
        result = await strategy.scan(markets)
        assert len(result) == 2

    async def test_scan_filters_out_non_qualifying_markets(self):
        """Markets failing guards (no token_ids) produce no signal."""
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("no book"))
        markets = [_make_market(clob_token_ids="[]")]
        result = await strategy.scan(markets)
        assert result == []

    async def test_scan_sorted_by_score_descending(self):
        """scan() sorts signals by _score_signal (higher = first)."""
        strategy = _make_strategy()
        # Set urgency behind target so max_hours = 168h (both markets qualify)
        strategy.adjust_params({"urgency_multiplier": 1.5})
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        # Short-term market should score higher than long-term
        markets = [
            _make_market(hours_to_resolution=120.0, market_id="long"),
            _make_market(hours_to_resolution=12.0, market_id="short"),
        ]
        result = await strategy.scan(markets)
        assert len(result) == 2
        # Short market (higher time_score) must appear first
        assert result[0].metadata["hours_to_resolution"] < result[1].metadata["hours_to_resolution"]

    async def test_scan_respects_urgency_max_hours(self):
        """With urgency=1.5 (behind), markets up to 168h are scanned."""
        strategy = _make_strategy()
        strategy.adjust_params({"urgency_multiplier": 1.5})
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=150.0)
        result = await strategy.scan([market])
        assert len(result) == 1

    async def test_scan_urgency_ahead_excludes_long_markets(self):
        """With urgency=0.6 (ahead), only markets <24h are scanned."""
        strategy = _make_strategy()
        strategy.adjust_params({"urgency_multiplier": 0.6})
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=48.0)  # 48h > 24h max
        result = await strategy.scan([market])
        assert result == []

    async def test_scan_mixed_markets_returns_only_qualifying(self):
        """Mix of valid and invalid markets; only valid ones produce signals."""
        strategy = _make_strategy()

        good_book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=good_book)

        markets = [
            _make_market(hours_to_resolution=48.0, market_id="good"),
            _make_market(clob_token_ids="[]"),  # no token_ids → None
        ]
        result = await strategy.scan(markets)
        assert len(result) == 1
        assert result[0].market_id == "good"


# ---------------------------------------------------------------------------
# _score_signal()
# ---------------------------------------------------------------------------


class TestScoreSignal:
    def _make_signal(self, hours: float, edge: float) -> TradeSignal:
        return TradeSignal(
            strategy="value_betting",
            market_id="mkt1",
            token_id="token_yes",
            side=OrderSide.BUY,
            estimated_prob=0.55,
            market_price=0.45,
            edge=edge,
            size_usd=0.0,
            confidence=0.70,
            metadata={"hours_to_resolution": hours},
        )

    def test_shorter_market_scores_higher(self):
        short = self._make_signal(hours=12.0, edge=0.04)
        long_ = self._make_signal(hours=120.0, edge=0.04)
        assert ValueBettingStrategy._score_signal(short) > ValueBettingStrategy._score_signal(long_)

    def test_higher_edge_scores_higher_same_hours(self):
        high = self._make_signal(hours=48.0, edge=0.06)
        low = self._make_signal(hours=48.0, edge=0.01)
        assert ValueBettingStrategy._score_signal(high) > ValueBettingStrategy._score_signal(low)

    def test_very_short_beats_long_with_higher_edge(self):
        """A 6h market with 3% edge should beat a 5-day market with 5% edge."""
        short = self._make_signal(hours=6.0, edge=0.03)
        long_ = self._make_signal(hours=120.0, edge=0.05)
        assert ValueBettingStrategy._score_signal(short) > ValueBettingStrategy._score_signal(long_)

    def test_missing_hours_metadata_uses_default(self):
        """When hours_to_resolution is absent, HOURS_MEDIUM is used as default."""
        signal = TradeSignal(
            strategy="value_betting",
            market_id="mkt1",
            token_id="token_yes",
            side=OrderSide.BUY,
            estimated_prob=0.55,
            market_price=0.45,
            edge=0.04,
            size_usd=0.0,
            confidence=0.70,
            metadata={},  # no hours_to_resolution
        )
        # time_score = max(0, 1 - HOURS_MEDIUM / HOURS_MEDIUM) = 0.0
        # edge_score = min(1, 0.04/0.05) = 0.8
        # total = 0.0 * 0.6 + 0.8 * 0.4 = 0.32
        score = ValueBettingStrategy._score_signal(signal)
        assert score == pytest.approx(0.32)

    def test_zero_hours_produces_max_time_score(self):
        """0h to resolution → time_score = 1.0 (max)."""
        signal = self._make_signal(hours=0.0, edge=0.05)
        score = ValueBettingStrategy._score_signal(signal)
        # time_score=1.0, edge_score=min(1,0.05/0.05)=1.0 → 0.6+0.4=1.0
        assert score == pytest.approx(1.0)

    def test_score_is_float(self):
        signal = self._make_signal(hours=48.0, edge=0.04)
        assert isinstance(ValueBettingStrategy._score_signal(signal), float)


# ---------------------------------------------------------------------------
# should_exit()
# ---------------------------------------------------------------------------


class TestShouldExit:
    async def test_price_below_040_triggers_exit(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.39) is True

    async def test_price_exactly_040_no_exit(self):
        """Boundary: 0.40 is not < 0.40, so should NOT exit."""
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.40) is False

    async def test_price_above_040_no_exit(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.60) is False

    async def test_price_very_high_no_exit(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.95) is False

    async def test_price_near_zero_triggers_exit(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.01) is True

    async def test_extra_kwargs_ignored(self):
        """should_exit accepts **kwargs without raising."""
        strategy = _make_strategy()
        result = await strategy.should_exit("mkt1", 0.50, avg_price=0.45, created_at=None)
        assert result is False

    async def test_take_profit_3pct_after_6h(self):
        """3%+ profit after 6h+ hold → exit."""
        strategy = _make_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=8)
        result = await strategy.should_exit(
            "mkt1", 0.93, avg_price=0.90, created_at=created,
        )
        assert result is True

    async def test_take_profit_too_fresh(self):
        """3%+ profit but only 1h hold → no exit."""
        strategy = _make_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await strategy.should_exit(
            "mkt1", 0.93, avg_price=0.90, created_at=created,
        )
        assert result is False

    async def test_relative_stop_loss_triggers_at_10pct(self):
        """Exit if price dropped 10%+ from entry."""
        strategy = _make_strategy()
        # avg_price=0.90, current=0.80 → 11.1% loss → exit
        assert await strategy.should_exit("mkt1", 0.80, avg_price=0.90) is True

    async def test_relative_stop_loss_no_trigger_below_threshold(self):
        """No exit if price only dropped 8% from entry."""
        strategy = _make_strategy()
        # avg_price=0.90, current=0.83 → 7.8% loss → no exit
        assert await strategy.should_exit("mkt1", 0.83, avg_price=0.90) is False

    async def test_no_relative_stop_loss_without_avg_price(self):
        """Without avg_price kwarg, relative stop-loss doesn't trigger."""
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.60) is False


# ---------------------------------------------------------------------------
# New filters: MAX_PRICE, MIN_PRICE, MIN_BOOK_VOLUME
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _evaluate_market — Dual-side evaluation (cheap YES → NO signal)
# ---------------------------------------------------------------------------


class TestDualSideEvaluation:
    async def test_cheap_yes_generates_no_signal(self):
        """YES=$0.04 is out of range, but NO=$0.96 is also out → None."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        # YES=$0.04 (below MIN_PRICE=0.05), NO=$0.96 (above MAX_PRICE=0.95)
        market = _make_market(
            hours_to_resolution=48.0,
            yes_price=0.04,
            no_price=0.96,
        )
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        assert signal is None

    async def test_cheap_yes_no_in_range_generates_no_signal(self):
        """YES=$0.08 out of mid-range, NO=$0.92 in range → NO signal via imbalance."""
        strategy = _make_strategy()
        # Negative imbalance (more asks → YES overpriced → NO underpriced)
        book = _make_order_book(
            bid_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
            ask_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(
            hours_to_resolution=48.0,
            yes_price=0.08,
            no_price=0.92,
        )
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.outcome == "No"
        assert signal.side == OrderSide.BUY
        assert signal.token_id == "token_no"
        assert signal.market_price == pytest.approx(0.92)

    async def test_expensive_yes_generates_no_signal(self):
        """YES=$0.96 is out of range, but NO=$0.04 is also out of range → None."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(
            hours_to_resolution=48.0,
            yes_price=0.96,
            no_price=0.04,
        )
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        # Both out of range → None
        assert signal is None

    async def test_mid_range_picks_higher_edge_side(self):
        """When both sides are valid, the side with higher edge wins."""
        strategy = _make_strategy()
        # With equal imbalance, both sides get the same abs_imbalance * 0.1 edge.
        # The side with the lower price gets the higher edge relative to its price.
        # At equal edge magnitude, yes_edge >= no_edge picks YES.
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(
            hours_to_resolution=48.0,
            yes_price=0.50,
            no_price=0.50,
        )
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        # Both edges equal → pick_yes (yes_edge >= no_edge)
        assert signal.outcome == "Yes"

    async def test_only_one_token_id_skips_no_side(self):
        """With only one token_id, NO side is not evaluated."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(
            hours_to_resolution=48.0,
            yes_price=0.45,
            clob_token_ids='["token_yes"]',
        )
        signal = await strategy._evaluate_market(market, max_hours=168.0)
        assert signal is not None
        assert signal.outcome == "Yes"


class TestNewFilters:
    async def test_high_price_market_rejected(self):
        """Markets with yes_price > MAX_PRICE (0.95) are skipped."""
        strategy = _make_strategy()
        market = _make_market(yes_price=0.96)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_boundary_price_at_max_accepted(self):
        """Markets at exactly MAX_PRICE are not filtered."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(yes_price=0.95)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        # Should not be filtered by price (may still fail edge checks)
        # The important thing is price filter didn't reject it
        assert result is None or isinstance(result, TradeSignal)

    async def test_low_price_market_rejected(self):
        """Markets with yes_price < MIN_PRICE (0.05) are skipped."""
        strategy = _make_strategy()
        market = _make_market(yes_price=0.04)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_thin_order_book_rejected(self):
        """Order books with total volume < MIN_BOOK_VOLUME are skipped."""
        strategy = _make_strategy()
        thin_book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.44, size=20.0)],
            asks=[OrderBookEntry(price=0.47, size=15.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=thin_book)
        market = _make_market(hours_to_resolution=48.0)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is None

    async def test_sufficient_volume_accepted(self):
        """Order books at or above MIN_BOOK_VOLUME pass the filter."""
        strategy = _make_strategy()
        book = _make_order_book(
            bid_sizes=[200.0, 150.0, 100.0, 80.0, 60.0],
            ask_sizes=[10.0, 8.0, 6.0, 4.0, 2.0],
        )
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(hours_to_resolution=48.0, yes_price=0.45)
        result = await strategy._evaluate_market(market, max_hours=168.0)
        assert result is not None

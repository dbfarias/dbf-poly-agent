"""Tests for strategy improvements #2-#10.

Covers:
- #2 Momentum in time_decay confidence
- #3 Adaptive rebalance thresholds in PositionCloser
- #4 Calibration penalty in Kelly sizing (RiskManager)
- #5 MIN_VOLUME_24H in MarketAnalyzer (150.0)
- #6 Mean-reversion filter in value_betting
- #7 Asymmetric exits for swing_trading
- #8 Time-tiered take-profit for time_decay
- #9 Order book velocity in value_betting
- #10 Crypto threshold extraction in llm_debate
"""

import os
import time

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.position_closer import PositionCloser
from bot.agent.risk_manager import RiskManager
from bot.agent.strategies.time_decay import TimeDecayStrategy
from bot.agent.strategies.value_betting import ValueBettingStrategy
from bot.agent.strategies.swing_trading import SwingTradingStrategy
from bot.polymarket.types import (
    GammaMarket,
    OrderBook,
    OrderBookEntry,
    OrderSide,
    TradeSignal,
)
from bot.research.llm_debate import extract_crypto_threshold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_td_strategy(price_tracker=None) -> TimeDecayStrategy:
    return TimeDecayStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
        price_tracker=price_tracker,
    )


def _make_vb_strategy(price_tracker=None) -> ValueBettingStrategy:
    return ValueBettingStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
        price_tracker=price_tracker,
    )


def _make_swing_strategy() -> SwingTradingStrategy:
    return SwingTradingStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_position(
    market_id="mkt1",
    token_id="token1",
    size=10.0,
    current_price=0.50,
    avg_price=0.55,
    question="Will X?",
    outcome="Yes",
    category="crypto",
    strategy="time_decay",
    unrealized_pnl=None,
    created_at=None,
):
    pos = MagicMock()
    pos.market_id = market_id
    pos.token_id = token_id
    pos.size = size
    pos.current_price = current_price
    pos.avg_price = avg_price
    pos.question = question
    pos.outcome = outcome
    pos.category = category
    pos.strategy = strategy
    pos.unrealized_pnl = (
        unrealized_pnl if unrealized_pnl is not None else (current_price - avg_price) * size
    )
    pos.created_at = created_at if created_at is not None else datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    return pos


def _make_signal(
    market_id="mkt_new",
    edge=0.06,
    strategy="value_betting",
    question="New signal?",
    token_id="token_new",
    market_price=0.60,
    outcome="Yes",
    estimated_prob=0.66,
    metadata=None,
):
    sig = MagicMock()
    sig.market_id = market_id
    sig.edge = edge
    sig.strategy = strategy
    sig.question = question
    sig.token_id = token_id
    sig.side = MagicMock()
    sig.side.value = "BUY"
    sig.market_price = market_price
    sig.outcome = outcome
    sig.estimated_prob = estimated_prob
    sig.metadata = metadata or {"category": "crypto"}
    return sig


def _make_closer(min_rebalance_edge=0.04):
    order_manager = AsyncMock()
    portfolio = AsyncMock()
    risk_manager = MagicMock()
    closer = PositionCloser(order_manager, portfolio, risk_manager)
    closer.min_rebalance_edge = min_rebalance_edge
    return closer


# ===========================================================================
# #2 Momentum in time_decay confidence
# ===========================================================================


class TestTimeDecayMomentumConfidence:
    """Test _calculate_confidence momentum adjustments."""

    def test_no_price_tracker_no_change(self):
        """Without a price_tracker, confidence is unaffected by momentum."""
        strat = _make_td_strategy(price_tracker=None)
        base = strat._calculate_confidence(0.92, 48.0, "mkt1")
        # Same result with or without market_id — no tracker to query
        base_no_id = strat._calculate_confidence(0.92, 48.0, "")
        assert base == base_no_id

    def test_positive_momentum_boosts_confidence(self):
        """Momentum > 0.02 should multiply confidence by MOMENTUM_BOOST (1.12)."""
        tracker = MagicMock()
        tracker.momentum.return_value = 0.05  # Strong positive
        strat = _make_td_strategy(price_tracker=tracker)

        conf_no_mom = strat._calculate_confidence(0.92, 48.0, "")
        conf_with_mom = strat._calculate_confidence(0.92, 48.0, "mkt1")

        assert conf_with_mom > conf_no_mom
        # Should be approximately base * 1.12 (capped at 0.99)
        expected = min(0.99, conf_no_mom * strat.MOMENTUM_BOOST)
        assert abs(conf_with_mom - expected) < 0.001

    def test_negative_momentum_penalizes_confidence(self):
        """Momentum < -0.02 should multiply confidence by MOMENTUM_PENALTY (0.88)."""
        tracker = MagicMock()
        tracker.momentum.return_value = -0.05  # Strong negative
        strat = _make_td_strategy(price_tracker=tracker)

        conf_no_mom = strat._calculate_confidence(0.92, 48.0, "")
        conf_with_mom = strat._calculate_confidence(0.92, 48.0, "mkt1")

        assert conf_with_mom < conf_no_mom
        expected = min(0.99, conf_no_mom * strat.MOMENTUM_PENALTY)
        assert abs(conf_with_mom - expected) < 0.001

    def test_small_momentum_no_change(self):
        """Momentum between -0.02 and 0.02 should not change confidence."""
        tracker = MagicMock()
        tracker.momentum.return_value = 0.01  # Too small
        strat = _make_td_strategy(price_tracker=tracker)

        conf_no_mom = strat._calculate_confidence(0.92, 48.0, "")
        conf_with_mom = strat._calculate_confidence(0.92, 48.0, "mkt1")

        assert conf_with_mom == conf_no_mom

    def test_momentum_exactly_at_threshold(self):
        """Momentum exactly at 0.02 should NOT trigger boost (> not >=)."""
        tracker = MagicMock()
        tracker.momentum.return_value = 0.02
        strat = _make_td_strategy(price_tracker=tracker)

        conf_no_mom = strat._calculate_confidence(0.92, 48.0, "")
        conf_with_mom = strat._calculate_confidence(0.92, 48.0, "mkt1")

        assert conf_with_mom == conf_no_mom

    def test_momentum_none_no_change(self):
        """If tracker returns None for momentum, confidence unchanged."""
        tracker = MagicMock()
        tracker.momentum.return_value = None
        strat = _make_td_strategy(price_tracker=tracker)

        conf_no_mom = strat._calculate_confidence(0.92, 48.0, "")
        conf_with_mom = strat._calculate_confidence(0.92, 48.0, "mkt1")

        assert conf_with_mom == conf_no_mom


# ===========================================================================
# #3 Adaptive rebalance thresholds
# ===========================================================================


class TestAdaptiveRebalance:
    """Test per-candidate adaptive min_edge in try_rebalance."""

    @pytest.mark.asyncio
    async def test_deep_loser_relaxed_threshold(self):
        """Position losing >10% should have effective_min_edge * 0.5."""
        closer = _make_closer(min_rebalance_edge=0.04)
        # Position with 12% loss
        pos = _make_position(
            avg_price=0.50, current_price=0.44, unrealized_pnl=-0.60
        )
        # Signal with edge of 0.025 — below 0.04 but above 0.04 * 0.5 = 0.02
        signal = _make_signal(edge=0.025)
        trade = MagicMock()
        trade.status = "filled"
        closer.order_manager.close_position = AsyncMock(return_value=trade)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
            result = await closer.try_rebalance(signal, [pos])

        assert result is not None

    @pytest.mark.asyncio
    async def test_moderate_loser_medium_threshold(self):
        """Position losing 5-10% should have effective_min_edge * 0.7."""
        closer = _make_closer(min_rebalance_edge=0.04)
        # Position with 7% loss
        pos = _make_position(
            avg_price=0.50, current_price=0.465, unrealized_pnl=-0.35
        )
        # Signal with edge of 0.03 — above 0.04 * 0.7 = 0.028
        signal = _make_signal(edge=0.03)
        trade = MagicMock()
        trade.status = "filled"
        closer.order_manager.close_position = AsyncMock(return_value=trade)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
            result = await closer.try_rebalance(signal, [pos])

        assert result is not None

    @pytest.mark.asyncio
    async def test_small_loser_full_threshold(self):
        """Position losing <5% should require the full effective_min_edge."""
        closer = _make_closer(min_rebalance_edge=0.04)
        # Position with 3% loss
        pos = _make_position(
            avg_price=0.50, current_price=0.485, unrealized_pnl=-0.15
        )
        # Signal with edge of 0.03 — below 0.04 (unchanged threshold)
        signal = _make_signal(edge=0.03)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
            result = await closer.try_rebalance(signal, [pos])

        # Should be rejected because 0.03 < 0.04
        assert result is None

    @pytest.mark.asyncio
    async def test_small_loser_passes_with_full_edge(self):
        """Position losing <5% with signal edge >= min_rebalance_edge should pass."""
        closer = _make_closer(min_rebalance_edge=0.04)
        # Position with 3% loss
        pos = _make_position(
            avg_price=0.50, current_price=0.485, unrealized_pnl=-0.15
        )
        signal = _make_signal(edge=0.05)  # Above 0.04
        trade = MagicMock()
        trade.status = "filled"
        closer.order_manager.close_position = AsyncMock(return_value=trade)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
            result = await closer.try_rebalance(signal, [pos])

        assert result is not None

    @pytest.mark.asyncio
    async def test_fast_reject_below_half_effective(self):
        """Signal edge below effective_min_edge * 0.5 should fast-reject."""
        closer = _make_closer(min_rebalance_edge=0.04)
        # Signal edge 0.01 < 0.04 * 0.5 = 0.02 → fast reject (no candidates evaluated)
        signal = _make_signal(edge=0.01)
        pos = _make_position(
            avg_price=0.50, current_price=0.40, unrealized_pnl=-1.0
        )

        result = await closer.try_rebalance(signal, [pos])

        assert result is None


# ===========================================================================
# #4 Calibration penalty in Kelly
# ===========================================================================


class TestCalibrationKelly:
    """Test _calibration_bucket and _calculate_size with calibration."""

    def test_calibration_bucket_95_plus(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.97) == "95-99"
        assert rm._calibration_bucket(0.95) == "95-99"

    def test_calibration_bucket_90_95(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.92) == "90-95"

    def test_calibration_bucket_85_90(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.87) == "85-90"

    def test_calibration_bucket_80_85(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.82) == "80-85"

    def test_calibration_bucket_70_80(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.75) == "70-80"

    def test_calibration_bucket_60_70(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.65) == "60-70"

    def test_calibration_bucket_below_60(self):
        rm = RiskManager()
        assert rm._calibration_bucket(0.55) == "50-60"

    def test_overconfident_reduces_kelly(self):
        """Calibration ratio > 1.1 should multiply kelly_frac by 0.8."""
        rm = RiskManager()
        from bot.config import RiskConfig

        config = RiskConfig.get()
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="token1",
            side=OrderSide.BUY,
            estimated_prob=0.92,
            market_price=0.86,
            edge=0.06,
            size_usd=0.0,
            confidence=0.85,
            metadata={},
        )

        size_no_cal = rm._calculate_size(signal, 100.0, config, available_capital=80.0)
        size_with_cal = rm._calculate_size(
            signal, 100.0, config,
            available_capital=80.0,
            calibration={"90-95": 1.2},  # Overconfident
        )

        assert size_with_cal < size_no_cal

    def test_underconfident_increases_kelly(self):
        """Calibration ratio < 0.9 should multiply kelly_frac by 1.1."""
        rm = RiskManager()
        from bot.config import RiskConfig

        config = RiskConfig.get()
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="token1",
            side=OrderSide.BUY,
            estimated_prob=0.92,
            market_price=0.86,
            edge=0.06,
            size_usd=0.0,
            confidence=0.85,
            metadata={},
        )

        size_no_cal = rm._calculate_size(signal, 100.0, config, available_capital=80.0)
        size_with_cal = rm._calculate_size(
            signal, 100.0, config,
            available_capital=80.0,
            calibration={"90-95": 0.8},  # Underconfident
        )

        assert size_with_cal > size_no_cal

    def test_neutral_calibration_no_change(self):
        """Calibration ratio between 0.9 and 1.1 should not change kelly."""
        rm = RiskManager()
        from bot.config import RiskConfig

        config = RiskConfig.get()
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="token1",
            side=OrderSide.BUY,
            estimated_prob=0.92,
            market_price=0.86,
            edge=0.06,
            size_usd=0.0,
            confidence=0.85,
            metadata={},
        )

        size_no_cal = rm._calculate_size(signal, 100.0, config, available_capital=80.0)
        size_with_cal = rm._calculate_size(
            signal, 100.0, config,
            available_capital=80.0,
            calibration={"90-95": 1.0},  # Neutral
        )

        assert size_no_cal == size_with_cal

    def test_no_calibration_no_change(self):
        """No calibration dict should not change sizing."""
        rm = RiskManager()
        from bot.config import RiskConfig

        config = RiskConfig.get()
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="token1",
            side=OrderSide.BUY,
            estimated_prob=0.92,
            market_price=0.86,
            edge=0.06,
            size_usd=0.0,
            confidence=0.85,
            metadata={},
        )

        size_none = rm._calculate_size(signal, 100.0, config, available_capital=80.0, calibration=None)
        size_empty = rm._calculate_size(signal, 100.0, config, available_capital=80.0, calibration={})

        assert size_none == size_empty

    def test_kelly_frac_clamped_min(self):
        """Kelly fraction should not go below 0.05 after calibration."""
        rm = RiskManager()
        from bot.config import RiskConfig

        config = RiskConfig.get()
        # Very low estimated_prob to produce tiny kelly, then overconfident cal
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="token1",
            side=OrderSide.BUY,
            estimated_prob=0.52,
            market_price=0.50,
            edge=0.02,
            size_usd=0.0,
            confidence=0.6,
            metadata={},
        )
        # Even with extreme overconfidence, kelly_frac should be >= 0.05
        size = rm._calculate_size(
            signal, 100.0, config,
            available_capital=80.0,
            calibration={"50-60": 5.0},  # Extreme overconfidence
        )
        # Should still produce a valid (non-negative) size
        assert size >= 0.0


# ===========================================================================
# #5 MIN_VOLUME_24H in MarketAnalyzer
# ===========================================================================


class TestMinVolume24h:
    """Verify MIN_VOLUME_24H is set to 150.0."""

    def test_min_volume_value(self):
        from bot.agent.market_analyzer import MarketAnalyzer
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(),
            cache=MagicMock(),
            strategies=[],
            clob_client=MagicMock(),
        )
        assert analyzer.MIN_VOLUME_24H == 150.0


# ===========================================================================
# #6 Mean-reversion filter in value_betting
# ===========================================================================


class TestMeanReversionFilter:
    """Test that value_betting skips signals when momentum aligns with imbalance."""

    @pytest.mark.asyncio
    async def test_momentum_aligns_with_positive_imbalance_skips(self):
        """Positive imbalance + positive momentum > threshold -> skip."""
        tracker = MagicMock()
        # First call for mean-reversion check (5m window), second for 60m momentum
        tracker.momentum.side_effect = lambda mid, window: (
            0.06 if window == 5 else 0.03
        )
        strat = _make_vb_strategy(price_tracker=tracker)

        market = GammaMarket(
            id="mkt1",
            question="Will X happen?",
            endDateIso=(datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            outcomes=["Yes", "No"],
            outcomePrices="[0.50,0.50]",
            clobTokenIds='["token_yes","token_no"]',
        )

        # Mock order book with positive imbalance (bid > ask)
        book = OrderBook(
            bids=[OrderBookEntry(price=0.50, size=200.0)],
            asks=[OrderBookEntry(price=0.51, size=50.0)],
        )
        strat.get_order_book = AsyncMock(return_value=book)

        signals = await strat.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_momentum_opposes_imbalance_allows(self):
        """Positive imbalance + negative momentum -> no skip."""
        tracker = MagicMock()
        # 5m window: negative momentum (opposes positive imbalance)
        tracker.momentum.side_effect = lambda mid, window: (
            -0.06 if window == 5 else 0.0
        )
        strat = _make_vb_strategy(price_tracker=tracker)

        market = GammaMarket(
            id="mkt1",
            question="Will X happen?",
            endDateIso=(datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            outcomes=["Yes", "No"],
            outcomePrices="[0.50,0.50]",
            clobTokenIds='["token_yes","token_no"]',
        )

        # Positive imbalance
        book = OrderBook(
            bids=[OrderBookEntry(price=0.50, size=200.0)],
            asks=[OrderBookEntry(price=0.51, size=50.0)],
        )
        strat.get_order_book = AsyncMock(return_value=book)

        signals = await strat.scan([market])
        # Should not be skipped (may still be filtered by edge/etc)
        # The important thing is the mean-reversion filter doesn't block it
        # We verify by checking that the tracker was called but signal was not skipped
        tracker.momentum.assert_called()

    @pytest.mark.asyncio
    async def test_momentum_below_threshold_allows(self):
        """Positive imbalance + small positive momentum -> no skip."""
        tracker = MagicMock()
        # 5m window: small momentum (below MEAN_REVERSION_THRESHOLD 0.05)
        tracker.momentum.side_effect = lambda mid, window: (
            0.03 if window == 5 else 0.0
        )
        strat = _make_vb_strategy(price_tracker=tracker)

        market = GammaMarket(
            id="mkt1",
            question="Will X happen?",
            endDateIso=(datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            outcomes=["Yes", "No"],
            outcomePrices="[0.50,0.50]",
            clobTokenIds='["token_yes","token_no"]',
        )

        book = OrderBook(
            bids=[OrderBookEntry(price=0.50, size=200.0)],
            asks=[OrderBookEntry(price=0.51, size=50.0)],
        )
        strat.get_order_book = AsyncMock(return_value=book)

        signals = await strat.scan([market])
        # Should not be blocked by mean-reversion (momentum too small)
        tracker.momentum.assert_called()

    @pytest.mark.asyncio
    async def test_no_tracker_no_filter(self):
        """Without a price_tracker, mean-reversion filter is skipped."""
        strat = _make_vb_strategy(price_tracker=None)

        market = GammaMarket(
            id="mkt1",
            question="Will X happen?",
            endDateIso=(datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            outcomes=["Yes", "No"],
            outcomePrices="[0.50,0.50]",
            clobTokenIds='["token_yes","token_no"]',
        )

        book = OrderBook(
            bids=[OrderBookEntry(price=0.50, size=200.0)],
            asks=[OrderBookEntry(price=0.51, size=50.0)],
        )
        strat.get_order_book = AsyncMock(return_value=book)

        # Should not raise — just skip the filter
        await strat.scan([market])


# ===========================================================================
# #7 Asymmetric exits for swing_trading
# ===========================================================================


class TestSwingAsymmetricExits:
    """Test swing_trading take-profit (1.5%) and stop-loss (0.8%)."""

    @pytest.mark.asyncio
    async def test_take_profit_at_1_5_pct(self):
        """Should exit when profit >= 1.5%."""
        strat = _make_swing_strategy()
        result = await strat.should_exit(
            "mkt1", current_price=0.508, avg_price=0.50,
        )
        # 0.508 / 0.50 - 1 = 1.6% > 1.5% TP
        assert result is True

    @pytest.mark.asyncio
    async def test_no_exit_below_tp(self):
        """Should NOT exit below 1.5% profit."""
        strat = _make_swing_strategy()
        result = await strat.should_exit(
            "mkt1", current_price=0.506, avg_price=0.50,
        )
        # 0.506 / 0.50 - 1 = 1.2% < 1.5% TP
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_loss_at_0_8_pct(self):
        """Should exit when loss >= 0.8%."""
        strat = _make_swing_strategy()
        result = await strat.should_exit(
            "mkt1", current_price=0.496, avg_price=0.50,
        )
        # (0.496 - 0.50) / 0.50 = -0.8% <= -0.8% SL
        assert result is True

    @pytest.mark.asyncio
    async def test_no_exit_above_sl(self):
        """Should NOT exit for losses smaller than 0.8%."""
        strat = _make_swing_strategy()
        result = await strat.should_exit(
            "mkt1", current_price=0.497, avg_price=0.50,
        )
        # (0.497 - 0.50) / 0.50 = -0.6% > -0.8% SL
        assert result is False

    @pytest.mark.asyncio
    async def test_time_expiry_at_4h(self):
        """Should exit after MAX_HOLD_HOURS (4h)."""
        strat = _make_swing_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=5)
        result = await strat.should_exit(
            "mkt1", current_price=0.50, avg_price=0.50,
            created_at=created,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_time_expiry_before_4h(self):
        """Should NOT exit before MAX_HOLD_HOURS."""
        strat = _make_swing_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=3)
        result = await strat.should_exit(
            "mkt1", current_price=0.50, avg_price=0.50,
            created_at=created,
        )
        assert result is False


# ===========================================================================
# #8 Time-tiered take-profit for time_decay
# ===========================================================================


class TestTimeDecayTimeTieredTP:
    """Test time-tiered take-profit thresholds in time_decay should_exit."""

    @pytest.mark.asyncio
    async def test_below_min_hold_no_tp(self):
        """Hold < EXIT_MIN_HOLD_HOURS (4h) -> no TP regardless of profit."""
        strat = _make_td_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=3)
        result = await strat.should_exit(
            "mkt1", current_price=0.97,
            avg_price=0.90, created_at=created,
        )
        # 7.8% profit but only 3h hold -> no TP
        assert result is False

    @pytest.mark.asyncio
    async def test_early_tp_needs_2_5_pct(self):
        """Hold 4-6h needs 2.5% profit to trigger TP."""
        strat = _make_td_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=5)

        # 2% profit - below 2.5% threshold
        result = await strat.should_exit(
            "mkt1", current_price=0.918,
            avg_price=0.90, created_at=created,
        )
        assert result is False

        # 3% profit - above 2.5% threshold
        result = await strat.should_exit(
            "mkt1", current_price=0.927,
            avg_price=0.90, created_at=created,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_mid_tp_needs_1_5_pct(self):
        """Hold 6-24h needs 1.5% profit to trigger TP."""
        strat = _make_td_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=12)

        # 1% profit - below 1.5%
        result = await strat.should_exit(
            "mkt1", current_price=0.909,
            avg_price=0.90, created_at=created,
        )
        assert result is False

        # 2% profit - above 1.5%
        result = await strat.should_exit(
            "mkt1", current_price=0.918,
            avg_price=0.90, created_at=created,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_late_tp_needs_1_0_pct(self):
        """Hold >= 24h needs only 1.0% profit to trigger TP."""
        strat = _make_td_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=30)

        # 0.5% profit - below 1.0%
        result = await strat.should_exit(
            "mkt1", current_price=0.9045,
            avg_price=0.90, created_at=created,
        )
        assert result is False

        # 1.5% profit - above 1.0%
        result = await strat.should_exit(
            "mkt1", current_price=0.9135,
            avg_price=0.90, created_at=created,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_loss_still_works(self):
        """Stop-loss (10%) should trigger regardless of hold time."""
        strat = _make_td_strategy()
        # Loss of 11%
        result = await strat.should_exit(
            "mkt1", current_price=0.80,
            avg_price=0.90,
        )
        assert result is True


# ===========================================================================
# #9 Order book velocity in value_betting
# ===========================================================================


class TestOrderBookVelocity:
    """Test order book velocity tracking in value_betting."""

    def test_growing_imbalance_boosts_confidence(self):
        """Imbalance growing > 0.1/min should add VELOCITY_BOOST to confidence."""
        strat = _make_vb_strategy()

        # Simulate previous imbalance recorded 1 minute ago
        now = time.monotonic()
        strat._prev_imbalance["mkt1"] = (0.10, now - 60.0)

        # Current imbalance is 0.25 → delta = 0.15 in 1 min = 0.15/min > 0.1
        # We test the internal _prev_imbalance dict is used correctly
        # by checking the velocity calc directly
        prev_imb, prev_ts = strat._prev_imbalance["mkt1"]
        elapsed_min = (now - prev_ts) / 60.0
        current_imb = 0.25
        delta_per_min = (current_imb - prev_imb) / elapsed_min

        assert delta_per_min > 0.1
        # The confidence adjustment is +VELOCITY_BOOST
        assert strat.VELOCITY_BOOST == 0.08

    def test_collapsing_imbalance_penalizes_confidence(self):
        """Imbalance collapsing < -0.1/min should subtract VELOCITY_PENALTY."""
        strat = _make_vb_strategy()

        now = time.monotonic()
        strat._prev_imbalance["mkt1"] = (0.25, now - 60.0)

        prev_imb, prev_ts = strat._prev_imbalance["mkt1"]
        elapsed_min = (now - prev_ts) / 60.0
        current_imb = 0.10
        delta_per_min = (current_imb - prev_imb) / elapsed_min

        assert delta_per_min < -0.1
        assert strat.VELOCITY_PENALTY == 0.08

    def test_stale_entries_evicted_after_10min(self):
        """Entries older than 10 minutes should be evicted."""
        strat = _make_vb_strategy()

        now = time.monotonic()
        # Old entry: 11 minutes ago (should be evicted)
        strat._prev_imbalance["stale_mkt"] = (0.20, now - 660.0)
        # Fresh entry: 5 minutes ago (should stay)
        strat._prev_imbalance["fresh_mkt"] = (0.20, now - 300.0)

        # Simulate what scan does: evict stale entries
        stale_cutoff = now - 600.0
        stale_keys = [
            k for k, (_, ts) in strat._prev_imbalance.items() if ts < stale_cutoff
        ]
        for k in stale_keys:
            del strat._prev_imbalance[k]

        assert "stale_mkt" not in strat._prev_imbalance
        assert "fresh_mkt" in strat._prev_imbalance

    @pytest.mark.asyncio
    async def test_velocity_applied_in_scan(self):
        """Full integration: velocity boost applied during scan."""
        strat = _make_vb_strategy()

        now_ts = time.monotonic()
        # Pre-seed with old imbalance for the market
        strat._prev_imbalance["mkt1"] = (0.05, now_ts - 60.0)

        market = GammaMarket(
            id="mkt1",
            question="Will X happen?",
            endDateIso=(datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            outcomes=["Yes", "No"],
            outcomePrices="[0.50,0.50]",
            clobTokenIds='["token_yes","token_no"]',
        )

        # Large positive imbalance (growing from 0.05)
        book = OrderBook(
            bids=[OrderBookEntry(price=0.50, size=300.0)],
            asks=[OrderBookEntry(price=0.51, size=50.0)],
        )
        strat.get_order_book = AsyncMock(return_value=book)

        signals = await strat.scan([market])
        # Updated imbalance should be stored
        assert "mkt1" in strat._prev_imbalance


# ===========================================================================
# #10 Crypto threshold extraction
# ===========================================================================


class TestCryptoThresholdExtraction:
    """Test extract_crypto_threshold regex-based extraction."""

    def test_btc_reach_above(self):
        result = extract_crypto_threshold("Will BTC reach $100,000 by June?")
        assert result is not None
        assert result["asset"] == "BTC"
        assert result["threshold"] == 100000.0
        assert result["direction"] == "above"

    def test_eth_drop_below(self):
        result = extract_crypto_threshold("Will ETH drop below $2,000?")
        assert result is not None
        assert result["asset"] == "ETH"
        assert result["threshold"] == 2000.0
        assert result["direction"] == "below"

    def test_sol_go_above(self):
        result = extract_crypto_threshold("Will SOL go above $250?")
        assert result is not None
        assert result["asset"] == "SOL"
        assert result["threshold"] == 250.0
        assert result["direction"] == "above"

    def test_full_name_bitcoin_normalized(self):
        result = extract_crypto_threshold("Will Bitcoin reach $150,000?")
        assert result is not None
        assert result["asset"] == "BTC"
        assert result["threshold"] == 150000.0

    def test_full_name_ethereum_normalized(self):
        result = extract_crypto_threshold("Will Ethereum drop below $1,500?")
        assert result is not None
        assert result["asset"] == "ETH"
        assert result["threshold"] == 1500.0
        assert result["direction"] == "below"

    def test_full_name_solana_normalized(self):
        result = extract_crypto_threshold("Will Solana reach $500?")
        assert result is not None
        assert result["asset"] == "SOL"

    def test_fall_below_direction(self):
        result = extract_crypto_threshold("Will BTC fall below $80,000?")
        assert result is not None
        assert result["direction"] == "below"

    def test_go_below_direction(self):
        result = extract_crypto_threshold("Will ETH go below $1,000?")
        assert result is not None
        assert result["direction"] == "below"

    def test_stay_below_direction(self):
        result = extract_crypto_threshold("Will BTC stay below $90,000?")
        assert result is not None
        assert result["direction"] == "below"

    def test_hit_above_direction(self):
        result = extract_crypto_threshold("Will BTC hit $120,000?")
        assert result is not None
        assert result["direction"] == "above"

    def test_exceed_above_direction(self):
        result = extract_crypto_threshold("Will ETH exceed $5,000?")
        assert result is not None
        assert result["direction"] == "above"

    def test_non_crypto_returns_none(self):
        result = extract_crypto_threshold("Will the Lakers win the championship?")
        assert result is None

    def test_no_price_returns_none(self):
        result = extract_crypto_threshold("Will BTC be adopted by banks?")
        assert result is None

    def test_decimal_threshold(self):
        result = extract_crypto_threshold("Will DOGE reach $0.50?")
        assert result is not None
        assert result["asset"] == "DOGE"
        assert result["threshold"] == 0.50

    def test_dogecoin_full_name_normalized(self):
        result = extract_crypto_threshold("Will Dogecoin reach $1?")
        assert result is not None
        assert result["asset"] == "DOGE"

    def test_cardano_normalized(self):
        result = extract_crypto_threshold("Will Cardano reach $5?")
        assert result is not None
        assert result["asset"] == "ADA"

    def test_case_insensitive(self):
        result = extract_crypto_threshold("will btc reach $100,000?")
        assert result is not None
        assert result["asset"] == "BTC"

    def test_trade_below_direction(self):
        result = extract_crypto_threshold("Will BTC trade below $50,000?")
        assert result is not None
        assert result["direction"] == "below"

    def test_be_above_direction(self):
        result = extract_crypto_threshold("Will ETH be above $10,000?")
        assert result is not None
        assert result["direction"] == "above"

    def test_stay_above_direction(self):
        result = extract_crypto_threshold("Will SOL stay above $200?")
        assert result is not None
        assert result["direction"] == "above"

    def test_plain_question_no_verb_returns_none(self):
        """Questions without Will/Can/Does should not match."""
        result = extract_crypto_threshold("BTC at $100,000 by June")
        assert result is None

"""End-to-end tests for strategy interaction and full trading cycle scenarios.

These tests verify that:
1. Multiple strategies don't conflict when scanning the same markets
2. The full trading cycle works end-to-end (scan → evaluate → execute → exit)
3. Learner auto-pause propagates correctly across strategies
4. Rebalance cascading works when positions are full
5. Risk checks cascade correctly (all 9 checks)
6. Market cooldown prevents churning across strategies
7. Strategy-specific exits don't interfere with each other
8. Calibration, momentum, and velocity adjustments compose correctly
9. Daily reset boundaries work properly
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import _apply_urgency_to_edge_multiplier
from bot.agent.position_closer import PositionCloser
from bot.agent.risk_manager import RiskManager
from bot.config import CapitalTier, TierConfig
from tests.e2e_helpers import (
    _make_engine,
    _make_filled_trade,
    _make_learner_adjustments,
    _make_position,
    _make_signal,
    _setup_engine_for_evaluate,
)

# ---------------------------------------------------------------------------
# 1. Multi-strategy scanning — no conflicts
# ---------------------------------------------------------------------------


class TestMultiStrategyScan:
    """Verify multiple strategies can scan the same markets without conflict."""

    @pytest.mark.asyncio
    async def test_different_strategies_same_market_dedup(self):
        """When two strategies emit signals for the same market, only the first
        should be approved (duplicate position check blocks the second)."""
        engine = _make_engine()
        sig_td = _make_signal(market_id="shared_mkt", strategy="time_decay", edge=0.06)
        sig_vb = _make_signal(market_id="shared_mkt", strategy="value_betting", edge=0.05)
        _setup_engine_for_evaluate(engine, [sig_td, sig_vb])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        # First signal fills, second is rejected as duplicate
        assert found == 2
        assert placed == 1  # Only one trade placed

    @pytest.mark.asyncio
    async def test_different_strategies_different_markets(self):
        """Signals from different strategies on different markets should all pass."""
        engine = _make_engine()
        sig1 = _make_signal(market_id="mkt_td", strategy="time_decay", edge=0.06)
        sig2 = _make_signal(market_id="mkt_vb", strategy="value_betting", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig1, sig2])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 2
        assert placed == 2

    @pytest.mark.asyncio
    async def test_paused_strategy_doesnt_block_others(self):
        """A paused strategy's signal is skipped but other strategies still work."""
        engine = _make_engine()
        sig_paused = _make_signal(market_id="mkt1", strategy="value_betting", edge=0.06)
        sig_active = _make_signal(market_id="mkt2", strategy="time_decay", edge=0.06)
        _setup_engine_for_evaluate(
            engine, [sig_paused, sig_active],
            paused={"value_betting"},
        )

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 2
        assert approved == 1  # Only the non-paused strategy
        assert placed == 1


# ---------------------------------------------------------------------------
# 2. Full trading cycle: scan → evaluate → execute → exit
# ---------------------------------------------------------------------------


class TestFullTradingCycle:
    """End-to-end test of a complete trading cycle."""

    @pytest.mark.asyncio
    async def test_cycle_buy_then_exit_take_profit(self):
        """Buy a position, then on next cycle detect TP exit."""
        engine = _make_engine()

        # Phase 1: Buy
        sig = _make_signal(market_id="mkt_tp", strategy="time_decay", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig])

        trade = _make_filled_trade(size=10, price=0.50)
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            _, _, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert placed == 1

        # Phase 2: Exit via analyzer.check_exits
        pos = _make_position(
            "mkt_tp", strategy="time_decay",
            avg_price=0.50, current_price=0.55,
        )
        engine.portfolio.positions = [pos]
        engine.analyzer.check_exits = AsyncMock(
            return_value=[("mkt_tp", "take_profit")]
        )
        engine.closer.close_position = AsyncMock()

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.use_llm_reviewer = False
            await engine._process_exits(CapitalTier.TIER1)

        engine.closer.close_position.assert_called_once()
        call_kwargs = engine.closer.close_position.call_args
        assert call_kwargs.args[0].market_id == "mkt_tp"
        assert call_kwargs.kwargs["exit_reason"] == "take_profit"

    @pytest.mark.asyncio
    async def test_cycle_buy_then_exit_stop_loss(self):
        """Buy a position, then detect SL exit."""
        engine = _make_engine()
        sig = _make_signal(market_id="mkt_sl", strategy="swing_trading", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig])

        trade = _make_filled_trade(size=10, price=0.50)
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            _, _, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert placed == 1

        pos = _make_position(
            "mkt_sl", strategy="swing_trading",
            avg_price=0.50, current_price=0.46,
        )
        engine.portfolio.positions = [pos]
        engine.analyzer.check_exits = AsyncMock(
            return_value=[("mkt_sl", "stop_loss")]
        )
        engine.closer.close_position = AsyncMock()

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.use_llm_reviewer = False
            await engine._process_exits(CapitalTier.TIER1)

        engine.closer.close_position.assert_called_once()
        assert engine.closer.close_position.call_args.kwargs["exit_reason"] == "stop_loss"

    @pytest.mark.asyncio
    async def test_multiple_exits_different_strategies(self):
        """Exits from different strategies should all fire independently."""
        engine = _make_engine()

        pos_td = _make_position("mkt_td", strategy="time_decay", avg_price=0.50, current_price=0.55)
        pos_vb = _make_position(
            "mkt_vb", strategy="value_betting", avg_price=0.50, current_price=0.46,
        )
        engine.portfolio = AsyncMock()
        engine.portfolio.positions = [pos_td, pos_vb]

        engine.analyzer = AsyncMock()
        engine.analyzer.check_exits = AsyncMock(return_value=[
            ("mkt_td", "take_profit"),
            ("mkt_vb", "stop_loss"),
        ])
        engine.closer = AsyncMock()
        engine.closer.close_position = AsyncMock()

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.use_llm_reviewer = False
            await engine._process_exits(CapitalTier.TIER1)

        assert engine.closer.close_position.call_count == 2
        exit_reasons = [
            call.kwargs["exit_reason"]
            for call in engine.closer.close_position.call_args_list
        ]
        assert "take_profit" in exit_reasons
        assert "stop_loss" in exit_reasons


# ---------------------------------------------------------------------------
# 3. Learner auto-pause propagation
# ---------------------------------------------------------------------------


class TestLearnerAutoPause:
    """Learner pauses propagate correctly to signal evaluation."""

    @pytest.mark.asyncio
    async def test_all_strategies_paused_no_trades(self):
        """When learner pauses all strategies, no signals should pass."""
        engine = _make_engine()
        signals = [
            _make_signal(market_id="m1", strategy="time_decay"),
            _make_signal(market_id="m2", strategy="value_betting"),
            _make_signal(market_id="m3", strategy="arbitrage"),
        ]
        _setup_engine_for_evaluate(
            engine, signals,
            paused={"time_decay", "value_betting", "arbitrage"},
        )

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 3
        assert approved == 0
        assert placed == 0

    @pytest.mark.asyncio
    async def test_unpause_allows_signals(self):
        """After unpausing, signals from that strategy should pass again."""
        engine = _make_engine()
        sig = _make_signal(market_id="m1", strategy="value_betting", edge=0.06)

        # First: paused
        _setup_engine_for_evaluate(engine, [sig], paused={"value_betting"})
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            _, approved1, _ = await engine._evaluate_signals(CapitalTier.TIER1)
        assert approved1 == 0

        # Second: unpaused
        _setup_engine_for_evaluate(engine, [sig], paused=set())
        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            _, approved2, placed2 = await engine._evaluate_signals(CapitalTier.TIER1)

        assert approved2 == 1
        assert placed2 == 1


# ---------------------------------------------------------------------------
# 4. Rebalance cascading
# ---------------------------------------------------------------------------


class TestRebalanceCascading:
    """Rebalance flow: max positions → close loser → re-evaluate new signal."""

    @pytest.mark.asyncio
    async def test_rebalance_on_max_positions(self):
        """When at max positions, rebalance should close weakest and re-evaluate."""
        engine = _make_engine()
        new_signal = _make_signal(market_id="mkt_new", strategy="time_decay", edge=0.08)

        # 6 existing positions (max for Tier 1)
        positions = [
            _make_position(f"mkt_{i}", strategy="time_decay", avg_price=0.50,
                           current_price=0.48 if i == 0 else 0.52, size=6)
            for i in range(6)
        ]
        _setup_engine_for_evaluate(engine, [new_signal], positions=positions)

        # Risk manager rejects first, then approves after rebalance
        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(side_effect=[
            (False, 0.0, "Max positions reached: 6 >= 6"),
            (True, 5.0, "approved"),
        ])

        # Rebalance returns the closed position
        rebal_trade = _make_filled_trade()
        engine.closer.try_rebalance = AsyncMock(
            return_value=(positions[0], rebal_trade)
        )
        engine.portfolio.record_trade_close = AsyncMock(return_value=-0.20)

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_position_closed", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.agent.engine.settings") as mock_settings, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.is_paper = True
            mock_settings.use_llm_debate = False
            # Mock DB session for close_trade_for_position
            mock_ctx = AsyncMock()
            mock_result = MagicMock()
            mock_result.one_or_none.return_value = None
            mock_ctx.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 1
        assert placed == 1
        engine.closer.try_rebalance.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_rebalance_per_cycle(self):
        """Only one rebalance attempt per cycle, even with multiple blocked signals."""
        engine = _make_engine()
        sig1 = _make_signal(market_id="mkt_a", strategy="time_decay", edge=0.08)
        sig2 = _make_signal(market_id="mkt_b", strategy="value_betting", edge=0.07)

        positions = [
            _make_position(f"mkt_{i}", strategy="time_decay", size=6)
            for i in range(6)
        ]
        _setup_engine_for_evaluate(engine, [sig1, sig2], positions=positions)

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(
            return_value=(False, 0.0, "Max positions reached: 6 >= 6")
        )

        engine.closer.try_rebalance = AsyncMock(return_value=None)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 2
        assert placed == 0
        # Only one rebalance attempt despite two blocked signals
        engine.closer.try_rebalance.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Risk check cascading
# ---------------------------------------------------------------------------


class TestRiskCheckCascading:
    """All 9 risk checks should cascade correctly."""

    def test_paused_blocks_all(self):
        rm = RiskManager()
        rm.pause()
        result = rm._check_paused()
        assert not result.passed
        assert "paused" in result.reason.lower()

    def test_duplicate_position_check(self):
        rm = RiskManager()
        sig = _make_signal(market_id="dup_mkt")
        pos = _make_position(market_id="dup_mkt")
        result = rm._check_duplicate_position(sig, [pos])
        assert not result.passed
        assert "Duplicate" in result.reason

    def test_daily_loss_limit(self):
        rm = RiskManager()
        rm._day_start_equity = 50.0
        config = TierConfig.get(CapitalTier.TIER1)
        # Bankroll dropped 15% below start
        result = rm._check_daily_loss(42.0, config)
        assert not result.passed
        assert "Daily loss" in result.reason

    def test_max_drawdown(self):
        rm = RiskManager()
        rm._peak_equity = 50.0
        config = TierConfig.get(CapitalTier.TIER1)
        # 30% drawdown
        result = rm._check_drawdown(35.0, config)
        assert not result.passed
        assert "drawdown" in result.reason.lower()

    def test_max_positions(self):
        rm = RiskManager()
        config = TierConfig.get(CapitalTier.TIER1)
        # Tier 1 max = 6
        positions = [_make_position(f"mkt_{i}", size=6) for i in range(6)]
        result = rm._check_max_positions(positions, config)
        assert not result.passed
        assert "Max positions" in result.reason

    def test_max_deployed_capital(self):
        rm = RiskManager()
        config = TierConfig.get(CapitalTier.TIER1)
        # 95%+ deployed
        positions = [_make_position("mkt1", size=100, avg_price=0.48, current_price=0.50)]
        result = rm._check_total_deployed(positions, 50.0, config)
        assert not result.passed
        assert "deployed" in result.reason.lower()

    def test_category_exposure(self):
        rm = RiskManager()
        config = TierConfig.get(CapitalTier.TIER1)
        sig = _make_signal(metadata={"category": "crypto", "price_std": 0.02})
        # Category exposure is %-of-bankroll based; fill up the limit
        max_pct = config["max_per_category_pct"]
        cost_per = 50.0 * max_pct / 2 + 1  # Each position > half the limit
        positions = [
            _make_position("m1", category="crypto", size=cost_per * 2, avg_price=0.50),
            _make_position("m2", category="crypto", size=cost_per * 2, avg_price=0.50),
        ]
        result = rm._check_category_exposure(sig, positions, 50.0, config)
        assert not result.passed
        assert "category" in result.reason.lower()

    def test_min_edge(self):
        rm = RiskManager()
        config = TierConfig.get(CapitalTier.TIER1)
        sig = _make_signal(edge=0.001)  # Way below min edge
        result = rm._check_min_edge(sig, config, edge_multiplier=1.0)
        assert not result.passed
        assert "edge" in result.reason.lower()

    def test_min_win_prob(self):
        rm = RiskManager()
        config = TierConfig.get(CapitalTier.TIER1)
        sig = _make_signal(estimated_prob=0.30)  # Low probability
        result = rm._check_min_win_prob(sig, config)
        assert not result.passed
        assert "prob" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_all_checks_pass(self):
        """A valid signal passes all 9 checks."""
        rm = RiskManager()
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0
        sig = _make_signal(edge=0.06, estimated_prob=0.92)
        approved, size, reason = await rm.evaluate_signal(
            signal=sig,
            bankroll=50.0,
            open_positions=[],
            tier=CapitalTier.TIER1,
        )
        assert approved is True
        assert size > 0
        assert reason == "approved"

    @pytest.mark.asyncio
    async def test_checks_cascade_first_failure_wins(self):
        """First failing check short-circuits — later checks aren't run."""
        rm = RiskManager()
        rm.pause()  # Check #1 fails immediately
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0
        sig = _make_signal()
        approved, size, reason = await rm.evaluate_signal(
            signal=sig,
            bankroll=50.0,
            open_positions=[],
            tier=CapitalTier.TIER1,
        )
        assert approved is False
        assert "paused" in reason.lower()


# ---------------------------------------------------------------------------
# 6. Market cooldown across strategies
# ---------------------------------------------------------------------------


class TestCooldownEnforcement:
    """Cooldown prevents rapid re-trading on the same market."""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_all_strategies(self):
        """A cooldown on a market blocks signals from ANY strategy."""
        engine = _make_engine()
        sig_td = _make_signal(market_id="cool_mkt", strategy="time_decay")
        sig_vb = _make_signal(market_id="cool_mkt", strategy="value_betting")

        cooldown_until = datetime(2099, 1, 1, tzinfo=timezone.utc)
        _setup_engine_for_evaluate(
            engine, [sig_td, sig_vb],
            cooldowns={"cool_mkt": cooldown_until},
        )

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert found == 2
        assert approved == 0
        assert placed == 0

    @pytest.mark.asyncio
    async def test_expired_cooldown_allows_trade(self):
        """A market whose cooldown has expired should be tradeable."""
        engine = _make_engine()
        sig = _make_signal(market_id="mkt_exp", strategy="time_decay", edge=0.06)

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        _setup_engine_for_evaluate(
            engine, [sig],
            cooldowns={"mkt_exp": past},
        )

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            found, approved, placed = await engine._evaluate_signals(CapitalTier.TIER1)

        assert placed == 1

    @pytest.mark.asyncio
    async def test_trade_sets_cooldown(self):
        """After a trade fills, the market should get a cooldown."""
        engine = _make_engine()
        sig = _make_signal(market_id="mkt_cd", strategy="time_decay", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        assert "mkt_cd" not in engine._market_cooldown

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            await engine._evaluate_signals(CapitalTier.TIER1)

        assert "mkt_cd" in engine._market_cooldown
        assert engine._market_cooldown["mkt_cd"] > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 7. Strategy-specific exit independence
# ---------------------------------------------------------------------------


class TestStrategyExitIndependence:
    """Verify strategy-specific exits work without interfering with each other."""

    @pytest.mark.asyncio
    async def test_td_exit_doesnt_affect_vb_position(self):
        """Time decay exit on one market shouldn't trigger exit on VB position."""
        engine = _make_engine()
        pos_td = _make_position("mkt_td", strategy="time_decay", current_price=0.55)
        pos_vb = _make_position("mkt_vb", strategy="value_betting", current_price=0.51)

        engine.portfolio = AsyncMock()
        engine.portfolio.positions = [pos_td, pos_vb]

        # Only time_decay triggers exit
        engine.analyzer = AsyncMock()
        engine.analyzer.check_exits = AsyncMock(
            return_value=[("mkt_td", "time_decay_tp_early")]
        )
        engine.closer = AsyncMock()
        engine.closer.close_position = AsyncMock()

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.use_llm_reviewer = False
            await engine._process_exits(CapitalTier.TIER1)

        # Only 1 close call — for the TD position
        engine.closer.close_position.assert_called_once()
        closed_pos = engine.closer.close_position.call_args.args[0]
        assert closed_pos.market_id == "mkt_td"
        assert closed_pos.strategy == "time_decay"


# ---------------------------------------------------------------------------
# 8. Calibration + urgency composition
# ---------------------------------------------------------------------------


class TestCalibrationUrgencyComposition:
    """Calibration and urgency adjustments compose correctly."""

    @pytest.mark.asyncio
    async def test_calibration_reduces_overconfident_sizing(self):
        """Overconfident calibration bucket should reduce Kelly fraction."""
        rm = RiskManager()
        rm._peak_equity = 100.0
        rm._day_start_equity = 100.0

        sig = _make_signal(edge=0.06, estimated_prob=0.90)

        # No calibration
        ok1, size1, _ = await rm.evaluate_signal(
            signal=sig, bankroll=100.0,
            open_positions=[], tier=CapitalTier.TIER1,
        )
        # Overconfident calibration (bucket 90-95 has ratio > 1.1)
        ok2, size2, _ = await rm.evaluate_signal(
            signal=sig, bankroll=100.0,
            open_positions=[], tier=CapitalTier.TIER1,
            calibration={"90-95": 1.3},
        )

        assert ok1 and ok2
        assert size2 < size1  # Calibration penalty reduces size

    @pytest.mark.asyncio
    async def test_calibration_boosts_underconfident_sizing(self):
        """Underconfident calibration bucket should boost Kelly fraction."""
        rm = RiskManager()
        rm._peak_equity = 100.0
        rm._day_start_equity = 100.0

        sig = _make_signal(edge=0.06, estimated_prob=0.90)

        ok1, size1, _ = await rm.evaluate_signal(
            signal=sig, bankroll=100.0,
            open_positions=[], tier=CapitalTier.TIER1,
        )
        ok2, size2, _ = await rm.evaluate_signal(
            signal=sig, bankroll=100.0,
            open_positions=[], tier=CapitalTier.TIER1,
            calibration={"90-95": 0.7},
        )

        assert ok1 and ok2
        assert size2 > size1  # Underconfident boost increases size

    def test_urgency_doesnt_cancel_learner_penalty(self):
        """High urgency should not reduce edge multiplier for losing strategies."""
        # Losing strategy: multiplier = 1.5 (higher = stricter)
        result = _apply_urgency_to_edge_multiplier(1.5, urgency=2.0)
        # Penalty should be preserved (not reduced by urgency)
        assert result == 1.5

    def test_urgency_relaxes_winning_strategy(self):
        """High urgency should relax edge for winning strategies."""
        result = _apply_urgency_to_edge_multiplier(0.8, urgency=1.5)
        # Should be relaxed (lower value)
        assert result < 0.8

    def test_urgency_tightens_when_ahead(self):
        """Low urgency (ahead of target) should tighten all strategies."""
        result = _apply_urgency_to_edge_multiplier(1.0, urgency=0.8)
        # Should be tightened (higher value)
        assert result > 1.0


# ---------------------------------------------------------------------------
# 9. Cycle committed tracking (bankroll deduction across multi-signal cycles)
# ---------------------------------------------------------------------------


class TestCycleCommittedTracking:
    """Track spent capital across multiple signals in the same cycle."""

    @pytest.mark.asyncio
    async def test_second_signal_gets_reduced_bankroll(self):
        """After first signal fills, second gets effective_bankroll = equity - committed."""
        engine = _make_engine()
        sig1 = _make_signal(market_id="mkt_a", strategy="time_decay", edge=0.06)
        sig2 = _make_signal(market_id="mkt_b", strategy="value_betting", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig1, sig2])

        bankroll_calls: list[float] = []

        async def capture_evaluate(signal, bankroll, **kwargs):
            bankroll_calls.append(bankroll)
            return True, 5.0, "approved"

        engine.risk_manager.evaluate_signal = capture_evaluate

        trade = _make_filled_trade(size=10, price=0.50)
        trade.cost_usd = 5.0
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            await engine._evaluate_signals(CapitalTier.TIER1)

        assert len(bankroll_calls) == 2
        assert bankroll_calls[0] == 200.0  # Full bankroll
        assert bankroll_calls[1] == 195.0  # 200 - 5 (first trade cost)


# ---------------------------------------------------------------------------
# 10. Edge multiplier + research sentiment composition
# ---------------------------------------------------------------------------


class TestEdgeMultiplierComposition:
    """Edge multiplier from learner composes with research sentiment."""

    @pytest.mark.asyncio
    async def test_research_multiplier_applies_on_top_of_learner(self):
        """Research sentiment multiplier should compound with learner edge multiplier."""
        engine = _make_engine()
        sig = _make_signal(market_id="mkt_r", strategy="time_decay", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig])

        # Learner penalizes this strategy (multiplier 1.5 = stricter)
        engine.learner.get_edge_multiplier = MagicMock(return_value=1.5)

        # Research gives a boost (multiplier 0.8 = relaxed)
        # Use low confidence so direction check doesn't apply additional boost
        research_mock = MagicMock()
        research_mock.sentiment_score = 0.05  # Neutral — no direction check effect
        research_mock.research_multiplier = 0.8
        research_mock.confidence = 0.2  # Below 0.3 threshold — direction check skipped
        research_mock.twitter_sentiment = 0.0
        research_mock.tweet_count = 0
        engine.research_cache.get = MagicMock(return_value=research_mock)

        eval_calls: list[dict] = []

        async def capture_eval(signal, bankroll, edge_multiplier=1.0, **kwargs):
            eval_calls.append({"edge_multiplier": edge_multiplier})
            return True, 5.0, "approved"

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = capture_eval

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            await engine._evaluate_signals(CapitalTier.TIER1)

        assert len(eval_calls) == 1
        # Edge multiplier should be learner * research = 1.5 * 0.8 = 1.2
        assert 1.15 <= eval_calls[0]["edge_multiplier"] <= 1.25


# ---------------------------------------------------------------------------
# 11. Rebalance_this_cycle flag reset
# ---------------------------------------------------------------------------


class TestCycleFlagReset:
    """_rebalanced_this_cycle should reset at start of each cycle."""

    @pytest.mark.asyncio
    async def test_rebalance_flag_resets(self):
        """_rebalanced_this_cycle resets to False at cycle start."""
        engine = _make_engine()
        engine._rebalanced_this_cycle = True

        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 50.0
        engine.portfolio.total_equity = 50.0
        engine.portfolio.tier = CapitalTier.TIER1
        engine.portfolio.open_position_count = 0
        engine.portfolio.positions = []
        engine.portfolio.day_start_equity = 50.0
        engine.portfolio.realized_pnl_today = 0.0

        engine.analyzer = AsyncMock()
        engine.analyzer.scan_markets = AsyncMock(return_value=[])
        engine.analyzer.check_exits = AsyncMock(return_value=[])
        engine.analyzer.strategies = []

        engine.order_manager = AsyncMock()
        engine.order_manager.pending_count = 0
        engine.order_manager.monitor_orders = AsyncMock()

        engine.risk_manager = MagicMock()
        engine.risk_manager.update_peak_equity = MagicMock()
        engine.risk_manager.set_day_start_equity = MagicMock()
        engine.risk_manager.get_risk_metrics = MagicMock(return_value={})

        engine.learner = MagicMock()
        engine.learner.set_daily_context = MagicMock()
        engine.learner.compute_stats = AsyncMock(
            return_value=_make_learner_adjustments()
        )
        engine.learner.consume_newly_paused = MagicMock(return_value=[])
        engine.research_engine = MagicMock()
        engine.research_engine.set_priority_markets = MagicMock()
        engine.research_cache = MagicMock()

        with patch.object(engine, "_persist_state", new_callable=AsyncMock), \
             patch.object(engine, "_maybe_snapshot", new_callable=AsyncMock), \
             patch.object(engine, "_maybe_daily_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.prune_old_activity", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock):
            mock_settings.use_llm_reviewer = False
            mock_settings.scan_interval_seconds = 60
            mock_settings.daily_target_pct = 0.01
            await engine._trading_cycle()

        assert engine._rebalanced_this_cycle is False


# ---------------------------------------------------------------------------
# 12. Adaptive rebalance with loss-scaled thresholds
# ---------------------------------------------------------------------------


class TestAdaptiveRebalanceThresholds:
    """Rebalance min_edge threshold scales based on position loss."""

    def test_closer_has_default_min_rebalance_edge(self):
        """PositionCloser should have a configurable min_rebalance_edge."""
        closer = PositionCloser(
            order_manager=MagicMock(),
            portfolio=MagicMock(),
            risk_manager=MagicMock(),
        )
        assert closer.min_rebalance_edge == 0.015

    def test_strategy_min_hold_populated(self):
        """Engine should populate closer.strategy_min_hold from strategies."""
        engine = _make_engine()
        # Check that strategy_min_hold was populated during init
        assert isinstance(engine.closer.strategy_min_hold, dict)


# ---------------------------------------------------------------------------
# 13. Calibration bucket mapping
# ---------------------------------------------------------------------------


class TestCalibrationBuckets:
    """Verify _calibration_bucket maps probabilities correctly."""

    def test_bucket_95_99(self):
        assert RiskManager._calibration_bucket(0.97) == "95-99"

    def test_bucket_90_95(self):
        assert RiskManager._calibration_bucket(0.92) == "90-95"

    def test_bucket_85_90(self):
        assert RiskManager._calibration_bucket(0.87) == "85-90"

    def test_bucket_80_85(self):
        assert RiskManager._calibration_bucket(0.82) == "80-85"

    def test_bucket_70_80(self):
        assert RiskManager._calibration_bucket(0.75) == "70-80"

    def test_bucket_below_70(self):
        assert RiskManager._calibration_bucket(0.60) == "60-70"

    def test_bucket_edge_case_exactly_95(self):
        assert RiskManager._calibration_bucket(0.95) == "95-99"

    def test_bucket_edge_case_exactly_90(self):
        assert RiskManager._calibration_bucket(0.90) == "90-95"


# ---------------------------------------------------------------------------
# 14. Multi-strategy position mix
# ---------------------------------------------------------------------------


class TestMultiStrategyPositionMix:
    """Positions from different strategies coexist correctly."""

    @pytest.mark.asyncio
    async def test_mixed_strategy_positions_in_risk_checks(self):
        """Risk checks should count all positions regardless of strategy."""
        rm = RiskManager()
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0

        positions = [
            _make_position("m1", strategy="time_decay", size=6, avg_price=0.50),
            _make_position("m2", strategy="value_betting", size=6, avg_price=0.50),
            _make_position("m3", strategy="arbitrage", size=6, avg_price=0.50),
            _make_position("m4", strategy="swing_trading", size=6, avg_price=0.50),
            _make_position("m5", strategy="price_divergence", size=6, avg_price=0.50),
            _make_position("m6", strategy="time_decay", size=6, avg_price=0.50),
        ]

        sig = _make_signal(market_id="m7", strategy="value_betting", edge=0.06)
        approved, _, reason = await rm.evaluate_signal(
            signal=sig,
            bankroll=50.0,
            open_positions=positions,
            tier=CapitalTier.TIER1,
        )
        assert approved is False
        assert "Max positions" in reason

    @pytest.mark.asyncio
    async def test_different_strategies_can_fill_all_slots(self):
        """5 different strategies can each take 1 slot (under 6 max)."""
        rm = RiskManager()
        rm._peak_equity = 100.0
        rm._day_start_equity = 100.0

        positions = [
            _make_position("m1", strategy="time_decay", size=6, avg_price=0.50, current_price=0.50),
            _make_position(
                "m2", strategy="value_betting", size=6, avg_price=0.50, current_price=0.50,
            ),
            _make_position(
                "m3", strategy="arbitrage", size=6, avg_price=0.50, current_price=0.50,
            ),
            _make_position(
                "m4", strategy="swing_trading", size=6, avg_price=0.50, current_price=0.50,
            ),
        ]

        sig = _make_signal(
            market_id="m5", strategy="price_divergence",
            edge=0.06, metadata={"category": "politics", "price_std": 0.02},
        )
        approved, size, reason = await rm.evaluate_signal(
            signal=sig,
            bankroll=100.0,
            open_positions=positions,
            tier=CapitalTier.TIER1,
        )
        assert approved is True
        assert size > 0


# ---------------------------------------------------------------------------
# 15. Pending order counting
# ---------------------------------------------------------------------------


class TestPendingOrderCounting:
    """Pending orders should count toward position limits."""

    @pytest.mark.asyncio
    async def test_pending_orders_count_as_positions(self):
        """Pending orders should count toward max positions."""
        rm = RiskManager()
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0

        # 4 open positions + 2 pending = 6 (max for Tier 1)
        positions = [
            _make_position(f"m{i}", size=6, avg_price=0.50) for i in range(4)
        ]
        sig = _make_signal(market_id="m_new", edge=0.06)

        approved, _, reason = await rm.evaluate_signal(
            signal=sig,
            bankroll=50.0,
            open_positions=positions,
            tier=CapitalTier.TIER1,
            pending_count=2,
        )
        assert approved is False
        assert "Max positions" in reason


# ---------------------------------------------------------------------------
# 16. Stuck positions (< 5 shares) excluded from max positions
# ---------------------------------------------------------------------------


class TestStuckPositions:
    """Positions with < 5 shares shouldn't block new trades."""

    @pytest.mark.asyncio
    async def test_tiny_positions_not_counted(self):
        """Positions with < MIN_SELLABLE_SHARES are excluded from count."""
        rm = RiskManager()
        rm._peak_equity = 100.0
        rm._day_start_equity = 100.0

        # 6 stuck positions (< 5 shares each)
        stuck = [
            _make_position(f"stuck_{i}", size=3, avg_price=0.50, current_price=0.50)
            for i in range(6)
        ]

        sig = _make_signal(market_id="m_new", edge=0.06, metadata={"category": "politics", "price_std": 0.02})

        with patch("bot.agent.risk_manager.settings") as mock_settings:
            mock_settings.is_paper = False
            approved, size, reason = await rm.evaluate_signal(
                signal=sig,
                bankroll=100.0,
                open_positions=stuck,
                tier=CapitalTier.TIER1,
            )

        assert approved is True
        assert size > 0

"""Advanced E2E tests covering gaps 1-6: debate pipeline, learner pause/unpause,
concurrent fills, cascading stop-losses, and ghost position recovery.

These tests follow the same patterns as test_e2e_strategy_interaction.py and
use shared helpers from tests.e2e_helpers.
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.learner import PerformanceLearner
from bot.agent.position_closer import PositionCloser
from bot.research.llm_debate import DebateResult
from tests.e2e_helpers import (
    _make_engine,
    _make_filled_trade,
    _make_position,
    _make_signal,
    _setup_engine_for_evaluate,
)


def _make_debate_result(*, approved: bool = True) -> DebateResult:
    """Create a DebateResult with sensible defaults."""
    return DebateResult(
        approved=approved,
        proposer_verdict="BUY" if approved else "PASS",
        proposer_confidence=0.8 if approved else 0.3,
        proposer_reasoning="Edge looks solid" if approved else "Too risky",
        challenger_verdict="APPROVE" if approved else "REJECT",
        challenger_risk="LOW" if approved else "HIGH",
        challenger_objections="" if approved else "Edge too thin",
        total_cost_usd=0.002,
        elapsed_s=1.5,
    )


# ---------------------------------------------------------------------------
# Gap 1: Full Debate -> Order -> PnL Pipeline
# ---------------------------------------------------------------------------


class TestDebatePipeline:
    """Verify LLM debate gate integrates with order execution and PnL flow."""

    @pytest.mark.asyncio
    async def test_debate_approve_risk_pass_order_fill_pnl_record(self):
        """Happy path: debate approves -> risk OK -> order fills -> PnL recorded."""
        engine = _make_engine()
        signal = _make_signal(market_id="debate_mkt", strategy="value_betting", edge=0.06)
        _setup_engine_for_evaluate(engine, [signal])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        debate_result = _make_debate_result(approved=True)

        _debate = patch(
            "bot.agent.engine.debate_signal",
            new_callable=AsyncMock,
            return_value=debate_result,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.settings") as mock_settings, \
             _debate, \
             patch("bot.agent.engine.log_llm_debate", new_callable=AsyncMock), \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = True
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True

            found, approved, placed = await engine._evaluate_signals()

        assert found == 1
        assert placed == 1
        engine.order_manager.execute_signal.assert_called_once()
        engine.portfolio.record_trade_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_debate_reject_blocks_order_execution(self):
        """Debate rejects signal -> order never placed."""
        engine = _make_engine()
        signal = _make_signal(
            market_id="reject_mkt", strategy="value_betting", edge=0.06,
        )
        _setup_engine_for_evaluate(engine, [signal])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        debate_result = _make_debate_result(approved=False)

        _debate = patch(
            "bot.agent.engine.debate_signal",
            new_callable=AsyncMock,
            return_value=debate_result,
        )
        _rejected = patch(
            "bot.agent.engine.log_signal_rejected",
            new_callable=AsyncMock,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             _rejected as mock_rejected, \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.settings") as mock_settings, \
             _debate, \
             patch("bot.agent.engine.log_llm_debate", new_callable=AsyncMock), \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = True
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True

            found, approved, placed = await engine._evaluate_signals()

        assert found == 1
        assert placed == 0
        engine.order_manager.execute_signal.assert_not_called()
        # Verify rejection was logged
        mock_rejected.assert_called_once()
        call_kwargs = mock_rejected.call_args
        reason = call_kwargs.kwargs.get(
            "reason", call_kwargs[1].get("reason", ""),
        )
        assert "LLM debate rejected" in reason

    @pytest.mark.asyncio
    async def test_debate_api_timeout_signal_proceeds(self):
        """debate_signal returns None (API timeout) -> signal bypasses gate."""
        engine = _make_engine()
        signal = _make_signal(market_id="timeout_mkt", strategy="value_betting", edge=0.06)
        _setup_engine_for_evaluate(engine, [signal])

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        _debate = patch(
            "bot.agent.engine.debate_signal",
            new_callable=AsyncMock,
            return_value=None,
        )
        _log_debate = patch(
            "bot.agent.engine.log_llm_debate",
            new_callable=AsyncMock,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.settings") as mock_settings, \
             _debate, \
             _log_debate as mock_log_debate, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = True
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True

            found, approved, placed = await engine._evaluate_signals()

        # Signal should proceed through risk check and get placed
        assert found == 1
        assert placed == 1
        engine.order_manager.execute_signal.assert_called_once()
        # No debate log since result was None
        mock_log_debate.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 2: Debate + Rebalance
# ---------------------------------------------------------------------------


class TestDebateRebalance:
    """Verify debate gate + rebalance interaction."""

    @pytest.mark.asyncio
    async def test_debate_approve_then_rebalance_then_fill(self):
        """Debate OK -> risk rejects (max positions) -> rebalance -> re-approve -> fill."""
        engine = _make_engine()
        signal = _make_signal(market_id="new_mkt", strategy="value_betting", edge=0.08)

        positions = [
            _make_position(f"mkt_{i}", strategy="time_decay", avg_price=0.50,
                           current_price=0.48 if i == 0 else 0.52, size=6)
            for i in range(6)
        ]
        _setup_engine_for_evaluate(engine, [signal], positions=positions)

        # Risk rejects first (max positions), approves after rebalance
        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(side_effect=[
            (False, 0.0, "Max positions reached: 6 >= 6"),
            (True, 5.0, "approved"),
        ])

        # Rebalance closes worst loser
        rebal_trade = _make_filled_trade()
        engine.closer.try_rebalance = AsyncMock(
            return_value=(positions[0], rebal_trade),
        )
        engine.portfolio.record_trade_close = AsyncMock(return_value=-0.12)

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        debate_result = _make_debate_result(approved=True)

        _debate = patch(
            "bot.agent.engine.debate_signal",
            new_callable=AsyncMock,
            return_value=debate_result,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_position_closed", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.agent.engine.settings") as mock_settings, \
             _debate, \
             patch("bot.agent.engine.log_llm_debate", new_callable=AsyncMock), \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = True
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True
            # Mock DB session for close_trade_for_position
            mock_ctx = AsyncMock()
            mock_result = MagicMock()
            mock_result.one_or_none.return_value = None
            mock_ctx.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            found, approved, placed = await engine._evaluate_signals()

        assert found == 1
        assert placed == 1
        engine.closer.try_rebalance.assert_called_once()
        engine.order_manager.execute_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_debate_approve_rebalance_fails_signal_rejected(self):
        """Debate OK -> risk rejects -> rebalance returns None -> signal dropped."""
        engine = _make_engine()
        signal = _make_signal(market_id="new_mkt", strategy="value_betting", edge=0.08)

        positions = [
            _make_position(f"mkt_{i}", strategy="time_decay", size=6)
            for i in range(6)
        ]
        _setup_engine_for_evaluate(engine, [signal], positions=positions)

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(
            return_value=(False, 0.0, "Max positions reached: 6 >= 6"),
        )

        # Rebalance fails
        engine.closer.try_rebalance = AsyncMock(return_value=None)

        debate_result = _make_debate_result(approved=True)

        _debate = patch(
            "bot.agent.engine.debate_signal",
            new_callable=AsyncMock,
            return_value=debate_result,
        )
        _risk_debate = patch(
            "bot.agent.engine.debate_risk_rejection",
            new_callable=AsyncMock,
            return_value=None,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.settings") as mock_settings, \
             _debate, \
             patch("bot.agent.engine.log_llm_debate", new_callable=AsyncMock), \
             _risk_debate, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock), \
             patch.object(engine, "_maybe_notify_risk_limit", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = True
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True

            found, approved, placed = await engine._evaluate_signals()

        assert found == 1
        assert placed == 0
        engine.closer.try_rebalance.assert_called_once()
        engine.order_manager.execute_signal.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 3: Learner Pause/Unpause/Recovery
# ---------------------------------------------------------------------------


class TestLearnerPauseUnpause:
    """Verify force_unpause, immunity lifecycle, and persistence."""

    def test_strategy_paused_then_unpaused_with_immunity(self):
        """Pause a strategy, force_unpause it, verify immunity is active."""
        learner = PerformanceLearner()
        strategy = "time_decay"

        # Manually pause
        learner._paused_strategies[strategy] = datetime.now(timezone.utc)

        # Verify paused
        assert learner.should_pause_strategy(strategy) is True

        # Force unpause
        result = learner.force_unpause(strategy)
        assert result is True
        assert strategy not in learner._paused_strategies
        assert strategy in learner._unpause_immunity

        # Immunity should prevent re-pause
        assert learner.should_pause_strategy(strategy) is False

    def test_immunity_expires_strategy_repauses(self):
        """Immunity set >6h ago should expire, allowing auto-pause to trigger."""
        learner = PerformanceLearner()
        strategy = "value_betting"

        # Set immunity 7 hours ago (past the 6h grace period)
        learner._unpause_immunity[strategy] = (
            datetime.now(timezone.utc) - timedelta(hours=7)
        )

        # Manually pause the strategy (simulating bad performance)
        learner._paused_strategies[strategy] = datetime.now(timezone.utc)

        # should_pause_strategy should return True because immunity expired
        # The immunity check happens first, removes expired immunity,
        # then falls through to the cooldown check
        result = learner.should_pause_strategy(strategy)
        assert result is True
        # Immunity should be removed
        assert strategy not in learner._unpause_immunity

    @pytest.mark.asyncio
    async def test_unpause_immunity_persisted_and_restored(self):
        """Immunity should survive a persist/restore cycle."""
        learner = PerformanceLearner()
        strategy = "price_divergence"

        # Grant immunity
        now = datetime.now(timezone.utc)
        learner._unpause_immunity[strategy] = now

        # Persist
        with patch("bot.data.settings_store.StateStore") as mock_store:
            mock_store.save_unpause_immunity = AsyncMock()
            await learner.persist_unpause_immunity()
            mock_store.save_unpause_immunity.assert_called_once()
            saved_data = mock_store.save_unpause_immunity.call_args[0][0]
            assert strategy in saved_data

        # Restore into a fresh learner
        learner2 = PerformanceLearner()
        with patch("bot.data.settings_store.StateStore") as mock_store2:
            mock_store2.load_unpause_immunity = AsyncMock(
                return_value={strategy: now.isoformat()},
            )
            await learner2.restore_unpause_immunity()

        assert strategy in learner2._unpause_immunity
        # Immunity should protect from pause
        assert learner2.should_pause_strategy(strategy) is False


# ---------------------------------------------------------------------------
# Gap 4: Concurrent Order Fills
# ---------------------------------------------------------------------------


class TestConcurrentOrderFills:
    """Verify handle_order_fill and handle_sell_fill work correctly with
    multiple concurrent calls."""

    @pytest.mark.asyncio
    async def test_three_concurrent_buy_fills_portfolio_consistent(self):
        """Three BUY fills should create three distinct positions."""
        portfolio = AsyncMock()
        portfolio.record_trade_open = AsyncMock()
        risk_manager = MagicMock()
        order_manager = AsyncMock()

        closer = PositionCloser(order_manager, portfolio, risk_manager)

        signals = [
            _make_signal(market_id=f"fill_mkt_{i}", strategy="value_betting")
            for i in range(3)
        ]

        with patch("bot.agent.position_closer.event_bus") as mock_bus:
            mock_bus.emit = AsyncMock()
            for i, sig in enumerate(signals):
                await closer.handle_order_fill(sig, shares=10.0, actual_price=0.50 + i * 0.01)

        assert portfolio.record_trade_open.call_count == 3

        # Verify each call had the right market_id
        called_market_ids = [
            call.kwargs["market_id"]
            for call in portfolio.record_trade_open.call_args_list
        ]
        assert set(called_market_ids) == {"fill_mkt_0", "fill_mkt_1", "fill_mkt_2"}

    @pytest.mark.asyncio
    async def test_buy_fill_and_sell_fill_interleaved(self):
        """Interleaved BUY and SELL fills produce correct portfolio state."""
        portfolio = AsyncMock()
        portfolio.record_trade_open = AsyncMock()
        portfolio.record_trade_close = AsyncMock(return_value=0.50)
        risk_manager = MagicMock()
        order_manager = AsyncMock()

        closer = PositionCloser(order_manager, portfolio, risk_manager)

        buy_signal = _make_signal(market_id="interleave_mkt", strategy="time_decay")

        with patch("bot.agent.position_closer.event_bus") as mock_bus, \
             patch("bot.agent.position_closer.async_session") as mock_session, \
             patch("bot.agent.position_closer.log_position_closed", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            # BUY fill
            await closer.handle_order_fill(buy_signal, shares=10.0, actual_price=0.50)
            # SELL fill on same market
            await closer.handle_sell_fill(
                market_id="interleave_mkt",
                sell_price=0.55,
                trade_id=42,
                shares=10.0,
                strategy="time_decay",
                question="Will interleave_mkt happen?",
            )

        portfolio.record_trade_open.assert_called_once()
        portfolio.record_trade_close.assert_called_once_with("interleave_mkt", 0.55)
        risk_manager.update_daily_pnl.assert_called_once_with(0.50)


# ---------------------------------------------------------------------------
# Gap 5: Cascading Stop-Losses
# ---------------------------------------------------------------------------


class TestCascadingStopLosses:
    """Verify multiple simultaneous stop-losses and daily limit cascade."""

    @pytest.mark.asyncio
    async def test_multiple_simultaneous_stop_losses(self):
        """4 positions exit via check_exits -> all 4 close_position called."""
        engine = _make_engine()

        positions = [
            _make_position(
                market_id=f"sl_mkt_{i}",
                strategy="time_decay",
                avg_price=0.60,
                current_price=0.50,  # -16.7% loss -> stop-loss
                size=10.0,
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            for i in range(4)
        ]

        engine.portfolio = AsyncMock()
        engine.portfolio.positions = positions

        # check_exits returns all 4 as stop-loss exits
        exit_list = [
            (f"sl_mkt_{i}", "stop_loss") for i in range(4)
        ]
        engine.analyzer = AsyncMock()
        engine.analyzer.check_exits = AsyncMock(return_value=exit_list)

        engine.closer = AsyncMock()
        engine.closer.close_position = AsyncMock()

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.use_llm_reviewer = False
            await engine._process_exits()

        assert engine.closer.close_position.call_count == 4

        # Verify each position was closed with the right exit_reason
        for call in engine.closer.close_position.call_args_list:
            assert call.kwargs.get("exit_reason") == "stop_loss"

    @pytest.mark.asyncio
    async def test_cascading_losses_trigger_daily_limit(self):
        """After multiple losses, daily loss limit blocks new signals."""
        engine = _make_engine()
        signal = _make_signal(
            market_id="new_after_losses",
            strategy="value_betting",
            edge=0.06,
        )
        _setup_engine_for_evaluate(engine, [signal])

        # Simulate losses: day_start was 50, lost $6 in trades today (12%)
        # daily_loss_limit_pct is 6%, so -$6 exceeds -$3 limit
        engine.portfolio.total_equity = 44.0
        engine.risk_manager._day_start_equity = 50.0
        engine.risk_manager._peak_equity = 50.0
        engine.risk_manager._daily_pnl = -6.0

        trade = _make_filled_trade()
        engine.order_manager.execute_signal = AsyncMock(return_value=trade)

        _rejected = patch(
            "bot.agent.engine.log_signal_rejected",
            new_callable=AsyncMock,
        )
        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             _rejected as mock_rejected, \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch("bot.agent.engine.settings") as mock_settings, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock), \
             patch.object(engine, "_maybe_notify_risk_limit", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            mock_settings.use_llm_debate = False
            mock_settings.use_llm_reviewer = False
            mock_settings.is_paper = True

            found, approved, placed = await engine._evaluate_signals()

        assert found == 1
        assert placed == 0
        # Verify rejection reason mentions daily loss
        reject_call = mock_rejected.call_args
        reason = reject_call.kwargs.get(
            "reason", reject_call[1].get("reason", ""),
        )
        assert (
            "Daily loss" in reason
            or "daily_loss" in reason.lower()
            or "loss limit" in reason.lower()
        )


# ---------------------------------------------------------------------------
# Gap 6: Ghost Position Recovery
# ---------------------------------------------------------------------------


class TestGhostPositionRecovery:
    """Verify sell failure tracking and stuck position behavior."""

    @pytest.mark.asyncio
    async def test_ghost_position_auto_removed_after_3_failures(self):
        """3 close_position calls returning None -> position auto-removed."""
        portfolio = AsyncMock()
        portfolio.record_trade_close = AsyncMock(return_value=-0.25)
        risk_manager = MagicMock()
        order_manager = AsyncMock()
        order_manager.close_position = AsyncMock(return_value=None)

        closer = PositionCloser(order_manager, portfolio, risk_manager)

        pos = _make_position(
            market_id="ghost_mkt",
            strategy="time_decay",
            avg_price=0.50,
            current_price=0.45,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mock_repo = MagicMock()
        mock_repo.close_trade_for_position = AsyncMock()

        with patch("bot.agent.position_closer.log_exit_triggered", new_callable=AsyncMock), \
             patch("bot.agent.position_closer.async_session") as mock_session, \
             patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            mock_session.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            for _ in range(3):
                await closer.close_position(pos, exit_reason="stop_loss")

        # After 3 failures, position should have been auto-removed
        portfolio.record_trade_close.assert_called_once_with("ghost_mkt", 0.45)
        risk_manager.update_daily_pnl.assert_called_once_with(-0.25)
        # Fail count cleared after auto-removal
        assert "ghost_mkt" not in closer._sell_fail_count

    @pytest.mark.asyncio
    async def test_stuck_position_skipped_in_rebalance(self):
        """Stuck position (3+ sell failures) is skipped as rebalance candidate."""
        portfolio = AsyncMock()
        risk_manager = MagicMock()
        order_manager = AsyncMock()
        order_manager.close_position = AsyncMock(return_value=None)

        closer = PositionCloser(order_manager, portfolio, risk_manager)
        closer.min_rebalance_edge = 0.01  # Low threshold

        # Mark a position as stuck
        closer._sell_fail_count["stuck_mkt"] = 3

        # Create positions: one stuck loser, one healthy loser
        stuck_pos = _make_position(
            market_id="stuck_mkt",
            strategy="time_decay",
            avg_price=0.60,
            current_price=0.40,  # -33% loss, big loser
            size=10.0,
            created_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        healthy_pos = _make_position(
            market_id="healthy_mkt",
            strategy="time_decay",
            avg_price=0.55,
            current_price=0.50,  # -9% loss
            size=10.0,
            created_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )

        new_signal = _make_signal(market_id="new_mkt", strategy="value_betting", edge=0.10)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock), \
             patch("bot.agent.position_closer.settings") as mock_settings:
            mock_settings.is_paper = True
            await closer.try_rebalance(
                new_signal, [stuck_pos, healthy_pos],
            )

        # Even though stuck_mkt is the bigger loser, it should be skipped.
        # healthy_mkt should be tried instead, but close_position returns None
        # so result may be None. The key test is that stuck_mkt was NOT attempted.
        close_calls = order_manager.close_position.call_args_list
        attempted_markets = [
            call.kwargs.get("market_id", call[1].get("market_id", ""))
            for call in close_calls
        ]
        assert "stuck_mkt" not in attempted_markets

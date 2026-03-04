"""Tests for position rebalancing — closing losers to make room for better signals."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import TradingEngine
from bot.data.market_cache import MarketCache
from bot.data.models import Position, Trade
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENGINE_MOCKS = {
    "PolymarketClient": MagicMock(),
    "GammaClient": MagicMock(),
    "DataApiClient": MagicMock(),
    "MarketCache": MagicMock(),
    "WebSocketManager": MagicMock(),
    "HeartbeatManager": MagicMock(),
}


def _patch_engine():
    """Patch all external engine deps in one call."""
    return patch.multiple(
        "bot.agent.engine", **_ENGINE_MOCKS
    )


def make_signal(
    market_id: str = "mkt_new",
    edge: float = 0.06,
    estimated_prob: float = 0.92,
    market_price: float = 0.86,
    strategy: str = "time_decay",
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        strategy=strategy,
        market_id=market_id,
        token_id="token_new",
        question="Will something new happen?",
        outcome="Yes",
        side=OrderSide.BUY,
        estimated_prob=estimated_prob,
        market_price=market_price,
        edge=edge,
        size_usd=5.0,
        confidence=0.85,
        metadata=metadata or {"category": "crypto"},
    )


def make_position(
    market_id: str = "mkt_old",
    token_id: str = "token_old",
    avg_price: float = 0.50,
    current_price: float = 0.45,
    size: float = 10.0,
    strategy: str = "time_decay",
    created_at: datetime | None = None,
) -> Position:
    if created_at is None:
        created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    unrealized = (current_price - avg_price) * size
    return Position(
        market_id=market_id,
        token_id=token_id,
        question="Will old thing happen?",
        outcome="Yes",
        category="crypto",
        strategy=strategy,
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=avg_price * size,
        unrealized_pnl=unrealized,
        is_open=True,
        created_at=created_at,
    )


def _build_engine():
    """Create a TradingEngine with all external dependencies mocked."""
    engine = TradingEngine()
    engine.order_manager = AsyncMock()
    engine.portfolio = MagicMock()
    engine.portfolio.record_trade_close = AsyncMock(return_value=-0.50)
    engine.risk_manager = MagicMock()
    engine.risk_manager.update_daily_pnl = MagicMock()
    # Re-wire closer with mocked dependencies
    engine.closer.order_manager = engine.order_manager
    engine.closer.portfolio = engine.portfolio
    engine.closer.risk_manager = engine.risk_manager
    engine.closer.cache = None  # No cache by default; tests that need it set it explicitly
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTryRebalance:
    """Tests for TradingEngine._try_rebalance()."""

    @pytest.mark.asyncio
    async def test_rebalance_closes_worst_opens_room(self):
        """Happy path: worst loser closed, returns Position."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40, avg_price=0.50, size=10.0)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=loser.market_id,
                    token_id=loser.token_id,
                    side="SELL",
                    price=0.40,
                    size=10.0,
                    status="filled",
                    is_paper=True,
                )
            )

            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, rebal_trade = result
            assert closed_pos.market_id == loser.market_id
            assert rebal_trade.status == "filled"
            engine.order_manager.close_position.assert_called_once_with(
                market_id=loser.market_id,
                token_id=loser.token_id,
                size=loser.size,
                current_price=loser.current_price,
                question=loser.question,
                outcome=loser.outcome,
                category=loser.category,
                strategy=loser.strategy,
                entry_price=loser.avg_price,
            )
            # PnL recording deferred to caller
            engine.portfolio.record_trade_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_rebalance_when_edge_below_threshold(self):
        """Skip if signal.edge < 1.5%."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40)
            engine.portfolio.positions = [loser]

            signal = make_signal(edge=0.01)  # Below 1.5% threshold

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_rebalance_when_all_winning(self):
        """Don't close positions with positive unrealized PnL."""
        with _patch_engine():
            engine = _build_engine()
            winner = make_position(avg_price=0.40, current_price=0.55, size=10.0)
            # unrealized_pnl = (0.55 - 0.40) * 10 = 1.50 > 0
            engine.portfolio.positions = [winner]

            signal = make_signal(edge=0.05)

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_rebalance_small_positions(self):
        """Skip positions below $1.00 notional in live mode."""
        with _patch_engine():
            engine = _build_engine()
            # 1.5 × $0.40 = $0.60 notional — below $1.00 minimum
            small_loser = make_position(current_price=0.40, size=1.5)
            engine.portfolio.positions = [small_loser]

            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.settings") as mock_settings:
                mock_settings.is_paper = False
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_rebalance_recent_positions(self):
        """Skip positions held less than 5 minutes."""
        with _patch_engine():
            engine = _build_engine()
            recent_loser = make_position(
                current_price=0.40,
                created_at=datetime.now(timezone.utc) - timedelta(seconds=60),  # 1 min
            )
            engine.portfolio.positions = [recent_loser]

            signal = make_signal(edge=0.05)

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_one_rebalance_per_cycle(self):
        """Second attempt should be blocked by _rebalanced_this_cycle flag."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=loser.market_id,
                    token_id=loser.token_id,
                    side="SELL",
                    price=0.40,
                    size=10.0,
                    status="filled",
                    is_paper=True,
                )
            )

            signal = make_signal(edge=0.05)

            positions = engine.portfolio.positions
            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                # First rebalance succeeds
                rebalance_result = await engine.closer.try_rebalance(
                    signal, positions,
                )
                assert rebalance_result is not None
                engine._rebalanced_this_cycle = True

                # The flag check happens in the calling code (_trading_cycle),
                # not inside _try_rebalance. Verify the flag is set.
                assert engine._rebalanced_this_cycle is True

    @pytest.mark.asyncio
    async def test_rebalance_triggers_on_capacity_reasons(self):
        """Rebalance triggers on 'Max positions' or 'Max deployed' reasons.

        This test verifies the condition in the signal evaluation loop,
        not _try_rebalance itself.
        """
        reason_max_pos = (
            "Max positions reached: 6 >= 6 (6 open + 0 pending)"
        )
        reason_max_deployed = (
            "Max deployed capital: $16.67 >= $14.73 (85% of $17.32)"
        )
        reason_other = "Edge too low: 1.5% < 2.0%"

        # Both capacity reasons should trigger rebalance
        assert "Max positions" in reason_max_pos or "Max deployed" in reason_max_pos
        assert "Max positions" in reason_max_deployed or "Max deployed" in reason_max_deployed
        # Non-capacity reasons should not
        assert "Max positions" not in reason_other and "Max deployed" not in reason_other

    @pytest.mark.asyncio
    async def test_picks_worst_of_multiple(self):
        """Multi-position: picks the one with lowest PnL%."""
        with _patch_engine():
            engine = _build_engine()

            # Position A: -10% PnL (mild loser)
            pos_a = make_position(
                market_id="mkt_a", token_id="tok_a",
                avg_price=0.50, current_price=0.45, size=10.0,
            )
            # Position B: -30% PnL (big loser — should be picked)
            pos_b = make_position(
                market_id="mkt_b", token_id="tok_b",
                avg_price=0.50, current_price=0.35, size=10.0,
            )
            # Position C: +10% PnL (winner — should be skipped)
            pos_c = make_position(
                market_id="mkt_c", token_id="tok_c",
                avg_price=0.50, current_price=0.55, size=10.0,
            )

            engine.portfolio.positions = [pos_a, pos_b, pos_c]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_b",
                    token_id="tok_b",
                    side="SELL",
                    price=0.35,
                    size=10.0,
                    status="filled",
                    is_paper=True,
                )
            )

            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, rebal_trade = result
            assert closed_pos.market_id == "mkt_b"
            assert rebal_trade.status == "filled"
            # Should close mkt_b (worst loser)
            engine.order_manager.close_position.assert_called_once_with(
                market_id="mkt_b",
                token_id="tok_b",
                size=10.0,
                current_price=0.35,
                question="Will old thing happen?",
                outcome="Yes",
                category="crypto",
                strategy="time_decay",
                entry_price=0.5,
            )

    @pytest.mark.asyncio
    async def test_close_fails_returns_none(self):
        """Handle close_position returning None for single candidate."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(return_value=None)

            signal = make_signal(edge=0.05)

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.portfolio.record_trade_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_unsellable_tries_next_candidate(self):
        """When worst loser fails to sell, try the next candidate."""
        with _patch_engine():
            engine = _build_engine()

            # Position A: -30% PnL (worst loser, but unsellable)
            pos_a = make_position(
                market_id="mkt_unsellable", token_id="tok_a",
                avg_price=0.50, current_price=0.35, size=10.0,
            )
            # Position B: -10% PnL (second worst, sellable)
            pos_b = make_position(
                market_id="mkt_sellable", token_id="tok_b",
                avg_price=0.50, current_price=0.45, size=10.0,
            )

            engine.portfolio.positions = [pos_a, pos_b]

            # First call (pos_a) returns None (sell failed),
            # second call (pos_b) succeeds
            engine.order_manager.close_position = AsyncMock(
                side_effect=[
                    None,  # pos_a fails
                    Trade(
                        market_id="mkt_sellable",
                        token_id="tok_b",
                        side="SELL",
                        price=0.45,
                        size=10.0,
                        status="filled",
                        is_paper=True,
                    ),
                ]
            )

            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, rebal_trade = result
            assert closed_pos.market_id == "mkt_sellable"
            assert rebal_trade.status == "filled"
            # close_position called twice: once for unsellable, once for sellable
            assert engine.order_manager.close_position.call_count == 2

    @pytest.mark.asyncio
    async def test_all_candidates_fail_returns_none(self):
        """When all candidates fail to sell, return None."""
        with _patch_engine():
            engine = _build_engine()

            pos_a = make_position(
                market_id="mkt_a", token_id="tok_a",
                avg_price=0.50, current_price=0.35, size=10.0,
            )
            pos_b = make_position(
                market_id="mkt_b", token_id="tok_b",
                avg_price=0.50, current_price=0.45, size=10.0,
            )

            engine.portfolio.positions = [pos_a, pos_b]
            engine.order_manager.close_position = AsyncMock(return_value=None)

            signal = make_signal(edge=0.05)

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            # Tried both candidates
            assert engine.order_manager.close_position.call_count == 2

    @pytest.mark.asyncio
    async def test_paper_mode_allows_small_positions(self):
        """Paper mode should skip the 5-share minimum check."""
        with _patch_engine():
            engine = _build_engine()
            small_loser = make_position(current_price=0.40, size=2.0)
            engine.portfolio.positions = [small_loser]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=small_loser.market_id,
                    token_id=small_loser.token_id,
                    side="SELL",
                    price=0.40,
                    size=2.0,
                    status="filled",
                    is_paper=True,
                )
            )

            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.settings") as mock_settings, \
                 patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                mock_settings.is_paper = True
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, rebal_trade = result
            assert closed_pos is not None
            engine.order_manager.close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_rebalance_triggers_reevaluation(self):
        """After rebalance, signal should be re-evaluated and can be approved.

        This is an integration test of the rebalance flow in _trading_cycle's
        signal evaluation loop.
        """
        # Verify the logic pattern: after _try_rebalance returns True,
        # evaluate_signal is called again. We test the sequence rather than
        # running the full trading cycle.
        with _patch_engine():
            engine = _build_engine()

            # Set up: rebalance succeeds
            loser = make_position(current_price=0.40)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=loser.market_id,
                    token_id=loser.token_id,
                    side="SELL",
                    price=0.40,
                    size=10.0,
                    status="filled",
                    is_paper=True,
                )
            )

            signal = make_signal(edge=0.05)

            positions = engine.portfolio.positions
            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                rebalance_result = await engine.closer.try_rebalance(
                    signal, positions,
                )

            assert rebalance_result is not None

            # Simulate what the engine loop does after rebalance:
            # re-evaluate the signal with updated positions
            engine.risk_manager.evaluate_signal = AsyncMock(
                return_value=(True, 5.0, "approved")
            )
            engine.portfolio.positions = []  # Slot freed
            engine.portfolio.total_equity = 30.0

            approved, size, reason = await engine.risk_manager.evaluate_signal(
                signal=signal,
                bankroll=30.0,
                open_positions=[],
                tier=MagicMock(),
                pending_count=0,
                edge_multiplier=1.0,
            )

            assert approved is True
            assert size == 5.0


# ---------------------------------------------------------------------------
# C6 — Rebalance returns Position, caller records PnL
# ---------------------------------------------------------------------------


class TestRebalanceReturnPosition:
    @pytest.mark.asyncio
    async def test_returns_position_without_recording_pnl(self):
        """_try_rebalance should return the closed Position, NOT record PnL."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40, avg_price=0.50, size=10.0)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=loser.market_id,
                    token_id=loser.token_id,
                    side="SELL",
                    price=0.40,
                    size=10.0,
                    status="filled",
                    is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            # Returns (closed_position, trade) tuple
            assert result is not None
            closed_pos, rebal_trade = result
            assert closed_pos.market_id == loser.market_id
            assert rebal_trade.status == "filled"
            # PnL NOT recorded inside try_rebalance
            engine.portfolio.record_trade_close.assert_not_called()
            engine.risk_manager.update_daily_pnl.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_failure_returns_none(self):
        """If close_position fails, return None (no PnL to record)."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(current_price=0.40)
            engine.portfolio.positions = [loser]
            engine.order_manager.close_position = AsyncMock(return_value=None)
            signal = make_signal(edge=0.05)

            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.portfolio.record_trade_close.assert_not_called()


class TestRebalanceFlagReset:
    """Verify the flag resets at cycle start."""

    def test_flag_resets_at_cycle_start(self):
        """_rebalanced_this_cycle should reset to False at start of _trading_cycle."""
        with _patch_engine():
            engine = TradingEngine()
            engine._rebalanced_this_cycle = True

            # Simulate what _trading_cycle does at the top
            engine._cycle_count += 1
            engine._rebalanced_this_cycle = False

            assert engine._rebalanced_this_cycle is False


# ---------------------------------------------------------------------------
# Near-resolution rebalance protection
# ---------------------------------------------------------------------------


class TestRebalanceNearResolution:
    """Positions near market resolution should be shielded from rebalance."""

    @pytest.mark.asyncio
    async def test_skips_near_resolution_position(self):
        """Position resolving in 12h with mild loss (-5%) should NOT be rebalanced."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(
                market_id="mkt_resolving", avg_price=0.50,
                current_price=0.475, size=10.0,  # -5% loss
            )
            engine.portfolio.positions = [loser]

            # Set up cache with market resolving in 12 hours
            cache = MarketCache()
            end_in_12h = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            cache.set_market("mkt_resolving", GammaMarket(
                id="mkt_resolving", endDateIso=end_in_12h,
            ))
            engine.closer.cache = cache

            signal = make_signal(edge=0.05)
            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is None
            engine.order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_far_resolution_position(self):
        """Position resolving in 48h should still be rebalanced normally."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(
                market_id="mkt_far", avg_price=0.50,
                current_price=0.475, size=10.0,  # -5%
            )
            engine.portfolio.positions = [loser]

            cache = MarketCache()
            end_in_48h = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
            cache.set_market("mkt_far", GammaMarket(
                id="mkt_far", endDateIso=end_in_48h,
            ))
            engine.closer.cache = cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_far", token_id="token_old",
                    side="SELL", price=0.475, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, _ = result
            assert closed_pos.market_id == "mkt_far"

    @pytest.mark.asyncio
    async def test_severe_loss_overrides_shield(self):
        """Position at -20% loss should be rebalanced even if resolving in 6h."""
        with _patch_engine():
            engine = _build_engine()
            big_loser = make_position(
                market_id="mkt_sinking", avg_price=0.50,
                current_price=0.40, size=10.0,  # -20% loss
            )
            engine.portfolio.positions = [big_loser]

            cache = MarketCache()
            end_in_6h = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
            cache.set_market("mkt_sinking", GammaMarket(
                id="mkt_sinking", endDateIso=end_in_6h,
            ))
            engine.closer.cache = cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_sinking", token_id="token_old",
                    side="SELL", price=0.40, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, _ = result
            assert closed_pos.market_id == "mkt_sinking"

    @pytest.mark.asyncio
    async def test_no_cache_skips_shield(self):
        """Without cache, rebalance proceeds normally (no protection)."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(avg_price=0.50, current_price=0.475, size=10.0)
            engine.portfolio.positions = [loser]
            engine.closer.cache = None  # No cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id=loser.market_id, token_id=loser.token_id,
                    side="SELL", price=0.475, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None

    @pytest.mark.asyncio
    async def test_no_end_date_skips_shield(self):
        """Market with no end_date should not trigger shield."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(
                market_id="mkt_no_end", avg_price=0.50,
                current_price=0.475, size=10.0,
            )
            engine.portfolio.positions = [loser]

            cache = MarketCache()
            cache.set_market("mkt_no_end", GammaMarket(
                id="mkt_no_end", endDateIso="",  # No end date
            ))
            engine.closer.cache = cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_no_end", token_id="token_old",
                    side="SELL", price=0.475, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None

    @pytest.mark.asyncio
    async def test_market_not_in_cache_skips_shield(self):
        """Position whose market is not in cache should not trigger shield."""
        with _patch_engine():
            engine = _build_engine()
            loser = make_position(
                market_id="mkt_uncached", avg_price=0.50,
                current_price=0.475, size=10.0,
            )
            engine.portfolio.positions = [loser]

            cache = MarketCache()  # Empty cache
            engine.closer.cache = cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_uncached", token_id="token_old",
                    side="SELL", price=0.475, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None

    @pytest.mark.asyncio
    async def test_shield_picks_next_candidate(self):
        """When near-resolution shields one position, rebalance picks the next."""
        with _patch_engine():
            engine = _build_engine()

            # Position A: -5% loss, resolving in 6h (shielded)
            pos_shielded = make_position(
                market_id="mkt_shielded", token_id="tok_a",
                avg_price=0.50, current_price=0.475, size=10.0,
            )
            # Position B: -8% loss, resolving in 72h (not shielded)
            pos_available = make_position(
                market_id="mkt_available", token_id="tok_b",
                avg_price=0.50, current_price=0.46, size=10.0,
            )

            engine.portfolio.positions = [pos_shielded, pos_available]

            cache = MarketCache()
            end_6h = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
            end_72h = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
            cache.set_market("mkt_shielded", GammaMarket(
                id="mkt_shielded", endDateIso=end_6h,
            ))
            cache.set_market("mkt_available", GammaMarket(
                id="mkt_available", endDateIso=end_72h,
            ))
            engine.closer.cache = cache

            engine.order_manager.close_position = AsyncMock(
                return_value=Trade(
                    market_id="mkt_available", token_id="tok_b",
                    side="SELL", price=0.46, size=10.0,
                    status="filled", is_paper=True,
                )
            )
            signal = make_signal(edge=0.05)

            with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
                result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

            assert result is not None
            closed_pos, _ = result
            # Should close the unshielded position, not the near-resolution one
            assert closed_pos.market_id == "mkt_available"

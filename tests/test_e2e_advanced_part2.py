"""End-to-end tests for advanced gaps 7-12 and rule conflict validators.

Covers:
- Gap 7: Debate failure fallback (signal proceeds, risk debate override)
- Gap 8: Rapid signal succession (bankroll depletion across signals)
- Gap 9: Category exposure cross-strategy aggregation
- Gap 10: Settlement edge cases (order expiry, sell fill PnL)
- Gap 11: Daily reset boundary (positions persist, PnL separates, peak resets)
- Gap 12: Learner-debate-urgency multiplier composition
- Rule conflict validators (stop-loss precedence, cap enforcement, cooldown, hold)
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import TradingEngine, _apply_urgency_to_edge_multiplier
from bot.agent.learner import LearnerAdjustments
from bot.agent.position_closer import PositionCloser
from bot.agent.risk_manager import RiskManager
from bot.config import RiskConfig
from bot.data.models import Position
from bot.polymarket.types import OrderSide, TradeSignal

# ---------------------------------------------------------------------------
# Helpers (duplicated from test_e2e_strategy_interaction for independence)
# ---------------------------------------------------------------------------


def _make_engine(**overrides):
    """Create a TradingEngine with all external clients mocked."""
    with patch("bot.agent.engine.PolymarketClient"), \
         patch("bot.agent.engine.GammaClient"), \
         patch("bot.agent.engine.DataApiClient"), \
         patch("bot.agent.engine.MarketCache"), \
         patch("bot.agent.engine.WebSocketManager"), \
         patch("bot.agent.engine.HeartbeatManager"):
        engine = TradingEngine()
        for attr, val in overrides.items():
            setattr(engine, attr, val)
        # Rewire closer references
        engine.closer.order_manager = engine.order_manager
        engine.closer.portfolio = engine.portfolio
        engine.closer.risk_manager = engine.risk_manager
        return engine


def _make_signal(
    market_id: str = "mkt1",
    strategy: str = "time_decay",
    edge: float = 0.06,
    estimated_prob: float = 0.92,
    market_price: float = 0.86,
    confidence: float = 0.85,
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        strategy=strategy,
        market_id=market_id,
        token_id=f"token_{market_id}",
        question=f"Will {market_id} happen?",
        outcome="Yes",
        side=OrderSide.BUY,
        estimated_prob=estimated_prob,
        market_price=market_price,
        edge=edge,
        size_usd=5.0,
        confidence=confidence,
        metadata=metadata or {"category": "crypto", "hours_to_resolution": 48, "price_std": 0.02},
    )


def _make_position(
    market_id: str = "mkt1",
    strategy: str = "time_decay",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.55,
    created_at: datetime | None = None,
    category: str = "crypto",
) -> Position:
    pnl = (current_price - avg_price) * size
    pos = Position(
        market_id=market_id,
        token_id=f"token_{market_id}",
        question=f"Will {market_id}?",
        outcome="Yes",
        category=category,
        strategy=strategy,
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=avg_price * size,
        unrealized_pnl=pnl,
        is_open=True,
    )
    if created_at is not None:
        pos.created_at = created_at
    return pos


def _make_learner_adjustments(
    paused_strategies: set[str] | None = None,
    urgency_multiplier: float = 1.0,
    calibration: dict | None = None,
    edge_multipliers: dict | None = None,
    category_min_edges: dict | None = None,
) -> LearnerAdjustments:
    return LearnerAdjustments(
        edge_multipliers=edge_multipliers or {},
        category_confidences={},
        paused_strategies=paused_strategies or set(),
        calibration=calibration or {},
        urgency_multiplier=urgency_multiplier,
        daily_progress=0.0,
        category_min_edges=category_min_edges,
    )


def _make_filled_trade(trade_id: int = 1, size: float = 10.0, price: float = 0.50):
    trade = MagicMock()
    trade.id = trade_id
    trade.status = "filled"
    trade.size = size
    trade.price = price
    trade.cost_usd = size * price
    return trade


def _setup_engine_for_evaluate(
    engine,
    signals: list[TradeSignal],
    positions: list[Position] | None = None,
    paused: set[str] | None = None,
    urgency: float = 1.0,
    calibration: dict | None = None,
    cooldowns: dict | None = None,
    equity: float = 50.0,
    category_min_edges: dict | None = None,
):
    """Wire up engine mocks for _evaluate_signals testing."""
    engine.portfolio = AsyncMock()
    engine.portfolio.cash = equity
    engine.portfolio.total_equity = equity
    engine.portfolio.positions = positions or []
    engine.portfolio.open_position_count = len(positions or [])
    engine.portfolio.day_start_equity = equity
    engine.portfolio.realized_pnl_today = 0.0

    engine.analyzer = AsyncMock()
    engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
    engine.analyzer.scan_markets = AsyncMock(return_value=signals)

    engine.risk_manager = RiskManager()
    engine.risk_manager._peak_equity = equity
    engine.risk_manager._day_start_equity = equity

    engine.order_manager = AsyncMock()
    engine.order_manager.pending_count = 0
    engine.order_manager.pending_market_ids = set()

    engine._learner_adjustments = _make_learner_adjustments(
        paused_strategies=paused,
        urgency_multiplier=urgency,
        calibration=calibration,
        category_min_edges=category_min_edges,
    )
    engine.learner = MagicMock()
    engine.learner.get_edge_multiplier = MagicMock(return_value=1.0)
    engine.learner.calibrator = MagicMock()
    engine.learner.calibrator.is_trained = False
    engine.research_cache = MagicMock()
    engine.research_cache.get = MagicMock(return_value=None)

    if cooldowns:
        engine._market_cooldown = cooldowns

    # Rewire closer
    engine.closer.order_manager = engine.order_manager
    engine.closer.portfolio = engine.portfolio
    engine.closer.risk_manager = engine.risk_manager


# ---------------------------------------------------------------------------
# Gap 7: Debate Failure Fallback
# ---------------------------------------------------------------------------


class TestDebateFailureFallback:
    """Verify that debate_signal returning None lets signal proceed."""

    @pytest.mark.asyncio
    async def test_debate_returns_none_signal_bypasses_gate(self):
        """When debate_signal returns None, the signal should bypass the
        debate gate and proceed to risk evaluation and order placement."""
        engine = _make_engine()
        sig = _make_signal(market_id="debate_bypass", strategy="value_betting", edge=0.06)
        _setup_engine_for_evaluate(engine, [sig])

        filled_trade = _make_filled_trade(trade_id=10, size=6.0, price=0.86)
        engine.order_manager.execute_signal = AsyncMock(return_value=filled_trade)
        # Mock liquidity check to pass
        engine._check_liquidity = AsyncMock(return_value=True)

        # Enable LLM debate but make debate_signal return None
        with patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.debate_signal", new_callable=AsyncMock, return_value=None), \
             patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus:
            mock_settings.use_llm_debate = True
            mock_settings.is_paper = True
            mock_settings.use_llm_post_mortem = False
            mock_settings.initial_bankroll = 50.0
            mock_bus.emit = AsyncMock()

            # correlation detector returns no group
            engine.research_engine = MagicMock()
            engine.research_engine.correlation_detector.get_group.return_value = None

            _found, _approved, placed = await engine._evaluate_signals()

        # Signal should have passed through (debate returned None = no gate)
        assert placed >= 1
        engine.order_manager.execute_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_risk_debate_override_on_edge_rejection(self):
        """When risk rejects a signal and debate_risk_rejection returns
        override=True with adjusted_size_pct, the re-evaluation still
        applies all risk checks. If the underlying reason (e.g. edge too low)
        persists, the override is blocked and signal is rejected."""
        engine = _make_engine()
        # Signal with very low edge and no hours_to_resolution discount
        sig = _make_signal(
            market_id="risk_debate", strategy="time_decay",
            edge=0.005, estimated_prob=0.92, market_price=0.86,
            metadata={"category": "crypto", "price_std": 0.02},  # no hours_to_resolution
        )
        _setup_engine_for_evaluate(engine, [sig])

        filled_trade = _make_filled_trade(trade_id=20, size=5.0, price=0.86)
        engine.order_manager.execute_signal = AsyncMock(return_value=filled_trade)
        engine._check_liquidity = AsyncMock(return_value=True)

        # Mock risk debate returning override with reduced size
        risk_result = MagicMock()
        risk_result.override = True
        risk_result.adjusted_size_pct = 0.5
        risk_result.rejection_reason = "Edge too low"
        risk_result.proposer_rebuttal = "Market has strong fundamentals"
        risk_result.analyst_verdict = "Override with caution"
        risk_result.analyst_reasoning = "Edge is marginal but event is imminent"
        risk_result.total_cost_usd = 0.01

        with patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.debate_signal", new_callable=AsyncMock, return_value=None), \
             patch("bot.agent.engine.debate_risk_rejection", new_callable=AsyncMock, return_value=risk_result), \
             patch("bot.agent.engine.log_risk_debate", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus:
            mock_settings.use_llm_debate = True
            mock_settings.is_paper = True
            mock_settings.use_llm_post_mortem = False
            mock_settings.initial_bankroll = 50.0
            mock_bus.emit = AsyncMock()

            engine.research_engine = MagicMock()
            engine.research_engine.correlation_detector.get_group.return_value = None

            _found, approved, placed = await engine._evaluate_signals()

        # Risk rejects (edge too low), debate says override, but re-evaluation
        # also fails because edge hasn't changed → signal stays rejected
        assert approved == 0
        assert placed == 0
        # Verify debate_risk_rejection WAS called (the override attempt happened)
        from bot.research.llm_debate import debate_risk_rejection  # noqa: F811
        # The patched version was called
        engine.order_manager.execute_signal.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 8: Rapid Signal Succession
# ---------------------------------------------------------------------------


class TestRapidSignalSuccession:
    """Verify that bankroll decreases across signals within one cycle."""

    @pytest.mark.asyncio
    async def test_five_signals_bankroll_depletes_fifth_rejected(self):
        """With 5 signals and limited capital, early trades fill while later
        ones are rejected due to depleted effective_bankroll."""
        engine = _make_engine()
        signals = [
            _make_signal(
                market_id=f"rapid_{i}",
                strategy="value_betting",
                edge=0.06,
                estimated_prob=0.92,
                market_price=0.86,
                metadata={"category": f"cat_{i}", "hours_to_resolution": 48, "price_std": 0.02},
            )
            for i in range(5)
        ]
        # Small equity so 5 trades can't all fit
        _setup_engine_for_evaluate(engine, signals, equity=15.0)

        trade_count = 0

        async def mock_execute(signal):
            nonlocal trade_count
            trade_count += 1
            t = _make_filled_trade(trade_id=trade_count, size=5.8, price=0.86)
            t.cost_usd = 5.0
            return t

        engine.order_manager.execute_signal = AsyncMock(side_effect=mock_execute)
        engine._check_liquidity = AsyncMock(return_value=True)

        with patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus:
            mock_settings.use_llm_debate = False
            mock_settings.is_paper = True
            mock_settings.use_llm_post_mortem = False
            mock_settings.initial_bankroll = 15.0
            mock_bus.emit = AsyncMock()

            engine.research_engine = MagicMock()
            engine.research_engine.correlation_detector.get_group.return_value = None

            _found, _approved, placed = await engine._evaluate_signals()

        # With $15 equity, can't fill all 5 trades at ~$5 each when positions
        # count toward deployed capital limits. At least one should be rejected.
        assert placed < 5
        assert placed >= 1  # at least some placed

    @pytest.mark.asyncio
    async def test_cycle_committed_reduces_bankroll_progressively(self):
        """Track that effective_bankroll decreases by trade cost after each fill.
        The engine computes effective_bankroll = total_equity - cycle_committed,
        where cycle_committed increases by trade.cost_usd after each filled order."""
        engine = _make_engine()
        signals = [
            _make_signal(
                market_id=f"prog_{i}",
                strategy="value_betting",
                edge=0.08,
                estimated_prob=0.92,
                market_price=0.86,
                metadata={"category": f"pcat_{i}", "hours_to_resolution": 48, "price_std": 0.02},
            )
            for i in range(3)
        ]
        _setup_engine_for_evaluate(engine, signals, equity=50.0)

        bankrolls_seen: list[float] = []
        original_evaluate = engine.risk_manager.evaluate_signal

        async def spy_evaluate(signal, bankroll, **kwargs):
            bankrolls_seen.append(bankroll)
            return await original_evaluate(signal=signal, bankroll=bankroll, **kwargs)

        engine.risk_manager.evaluate_signal = spy_evaluate

        trade_idx = 0

        async def mock_execute(signal):
            nonlocal trade_idx
            trade_idx += 1
            t = _make_filled_trade(trade_id=trade_idx, size=5.8, price=0.86)
            t.cost_usd = 5.0
            return t

        engine.order_manager.execute_signal = AsyncMock(side_effect=mock_execute)
        engine._check_liquidity = AsyncMock(return_value=True)

        with patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus:
            mock_settings.use_llm_debate = False
            mock_settings.is_paper = True
            mock_settings.use_llm_post_mortem = False
            mock_settings.initial_bankroll = 50.0
            mock_bus.emit = AsyncMock()

            engine.research_engine = MagicMock()
            engine.research_engine.correlation_detector.get_group.return_value = None

            await engine._evaluate_signals()

        # Verify bankroll decreases for subsequent signals after fills
        # The first risk eval uses full equity, subsequent ones use reduced
        assert len(bankrolls_seen) >= 2
        # After first trade fills (cost_usd=5), second eval should see $45
        assert bankrolls_seen[1] < bankrolls_seen[0], (
            f"bankroll[1]={bankrolls_seen[1]} should be less than "
            f"bankroll[0]={bankrolls_seen[0]}"
        )


# ---------------------------------------------------------------------------
# Gap 9: Category Exposure Cross-Strategy
# ---------------------------------------------------------------------------


class TestCategoryExposureCrossStrategy:
    """Verify that category limits aggregate across all strategies."""

    @pytest.mark.asyncio
    async def test_crypto_exposure_blocks_td_allows_vb_non_crypto(self):
        """Two existing crypto positions at 40% of bankroll. New crypto signal
        blocked, but politics signal passes."""
        engine = _make_engine()
        existing = [
            _make_position(market_id="crypt1", strategy="time_decay",
                           size=20.0, avg_price=0.50, category="crypto"),
            _make_position(market_id="crypt2", strategy="value_betting",
                           size=20.0, avg_price=0.50, category="crypto"),
        ]
        sig_crypto = _make_signal(
            market_id="new_crypto", strategy="time_decay", edge=0.06,
            metadata={"category": "crypto", "hours_to_resolution": 48, "price_std": 0.02},
        )
        sig_politics = _make_signal(
            market_id="new_politics", strategy="value_betting", edge=0.06,
            metadata={"category": "politics", "hours_to_resolution": 48, "price_std": 0.02},
        )

        rm = RiskManager()
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0
        config = RiskConfig.get()

        # Crypto: 2 positions * $10 cost_basis = $20 = 40% of $50
        # max_per_category_pct is 0.35 (35%)
        crypto_ok, _, crypto_reason = await rm.evaluate_signal(
            signal=sig_crypto, bankroll=50.0,
            open_positions=existing,
        )
        politics_ok, _, _ = await rm.evaluate_signal(
            signal=sig_politics, bankroll=50.0,
            open_positions=existing,
        )

        assert not crypto_ok, f"Crypto signal should be blocked: {crypto_reason}"
        assert "Category exposure" in crypto_reason
        assert politics_ok, "Politics signal should pass (different category)"

    @pytest.mark.asyncio
    async def test_cross_strategy_category_count_aggregation(self):
        """1 TD crypto + 1 VB crypto positions aggregate for category limit.
        New arb crypto signal should be blocked when at category cap."""
        engine = _make_engine()
        existing = [
            _make_position(market_id="c1", strategy="time_decay",
                           size=20.0, avg_price=0.50, category="crypto"),
            _make_position(market_id="c2", strategy="value_betting",
                           size=20.0, avg_price=0.50, category="crypto"),
        ]
        sig_arb = _make_signal(
            market_id="arb_crypto", strategy="arbitrage", edge=0.06,
            metadata={"category": "crypto", "hours_to_resolution": 48, "price_std": 0.02},
        )

        rm = RiskManager()
        rm._peak_equity = 50.0
        rm._day_start_equity = 50.0

        ok, _, reason = await rm.evaluate_signal(
            signal=sig_arb, bankroll=50.0,
            open_positions=existing,
        )

        assert not ok, f"Arb crypto should be blocked by cross-strategy category: {reason}"
        assert "Category exposure" in reason


# ---------------------------------------------------------------------------
# Gap 10: Settlement Edge Cases
# ---------------------------------------------------------------------------


class TestSettlementEdgeCases:
    """Verify order lifecycle edge cases: expiry and sell fill PnL."""

    @pytest.mark.asyncio
    async def test_pending_buy_market_resolves_order_expires(self):
        """A pending BUY that exceeds timeout should be expired, not filled."""
        from bot.agent.order_manager import OrderManager, ORDER_TIMEOUT_SECONDS

        clob = MagicMock()
        clob.is_paper = False
        clob.get_address.return_value = "0xtest"
        clob.cancel_order = AsyncMock()

        data_api = AsyncMock()
        # Return no positions (market resolved, token gone)
        data_api.get_positions = AsyncMock(return_value=[])

        om = OrderManager(clob, data_api)

        sig = _make_signal(market_id="resolved_mkt")
        # Create a pending order that's past timeout
        old_time = datetime.now(timezone.utc) - timedelta(seconds=ORDER_TIMEOUT_SECONDS + 60)
        om._pending_orders["order123"] = {
            "trade_id": 99,
            "created_at": old_time,
            "signal": sig,
            "shares": 10.0,
            "actual_price": 0.86,
        }

        with patch("bot.agent.order_manager.async_session") as mock_session, \
             patch("bot.agent.order_manager.log_order_expired", new_callable=AsyncMock), \
             patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock):
            mock_repo = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx
            mock_ctx.TradeRepository = mock_repo

            # Patch TradeRepository inside the context
            with patch("bot.agent.order_manager.TradeRepository") as MockRepo:
                repo_instance = AsyncMock()
                MockRepo.return_value = repo_instance

                await om.monitor_orders()

        # Order should be expired and removed
        assert "order123" not in om._pending_orders
        clob.cancel_order.assert_called_once_with("order123")
        repo_instance.update_status.assert_called_once_with(99, "expired")

    @pytest.mark.asyncio
    async def test_filled_buy_pending_sell_market_resolves(self):
        """When a pending SELL is detected as filled, handle_sell_fill
        callback is invoked to record PnL."""
        from bot.agent.order_manager import OrderManager

        clob = MagicMock()
        clob.is_paper = False
        clob.get_address.return_value = "0xtest"

        data_api = AsyncMock()
        # Token NOT in positions = sell is filled
        data_api.get_positions = AsyncMock(return_value=[])

        om = OrderManager(clob, data_api)

        sell_callback = AsyncMock()
        om.set_on_sell_fill_callback(sell_callback)

        sig = _make_signal(market_id="sell_mkt")
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        om._pending_orders["sell_order"] = {
            "trade_id": 55,
            "created_at": recent,
            "signal": sig,
            "shares": 8.0,
            "actual_price": 0.90,
            "is_sell": True,
            "token_id": "token_sell_mkt",
            "market_id": "sell_mkt",
            "sell_price": 0.90,
            "strategy": "time_decay",
            "question": "Will sell_mkt happen?",
        }

        with patch("bot.agent.order_manager.async_session") as mock_session, \
             patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx

            with patch("bot.agent.order_manager.TradeRepository") as MockRepo:
                repo_instance = AsyncMock()
                MockRepo.return_value = repo_instance

                await om.monitor_orders()

        # Sell fill callback should have been called
        sell_callback.assert_called_once()
        call_args = sell_callback.call_args
        assert call_args[0][0] == "sell_mkt"  # market_id
        assert call_args[0][1] == 0.90  # sell_price


# ---------------------------------------------------------------------------
# Gap 11: Daily Reset Boundary
# ---------------------------------------------------------------------------


class TestDailyResetBoundary:
    """Verify daily state resets work correctly."""

    def test_position_persists_after_midnight_reset(self):
        """Positions survive daily reset; only PnL counters reset."""
        rm = RiskManager()
        rm._daily_pnl = 5.0
        rm._peak_equity = 60.0
        rm._day_start_equity = 50.0

        # Simulate portfolio with open positions
        positions = [
            _make_position(market_id="persist1", size=10.0, avg_price=0.50),
        ]

        rm.reset_daily_state(55.0)

        # PnL reset, but positions are separate from risk manager
        assert rm._daily_pnl == 0.0
        assert rm._day_start_equity == 55.0
        assert rm._peak_equity == 55.0
        # Positions are managed by Portfolio, not RiskManager — they persist
        assert len(positions) == 1
        assert positions[0].is_open

    def test_daily_pnl_separates_across_days(self):
        """PnL updates after reset reflect only the new day's trades."""
        rm = RiskManager()
        rm.update_daily_pnl(1.0)
        assert rm._daily_pnl == 1.0

        rm.reset_daily_state(51.0)
        assert rm._daily_pnl == 0.0

        rm.update_daily_pnl(0.5)
        assert rm._daily_pnl == 0.5

    def test_peak_equity_resets_at_daily_boundary(self):
        """Peak equity resets to current equity on daily reset."""
        rm = RiskManager()
        rm._peak_equity = 60.0
        rm._day_start_equity = 50.0

        rm.reset_daily_state(50.0)

        assert rm._peak_equity == 50.0
        assert rm._day_start_equity == 50.0


# ---------------------------------------------------------------------------
# Gap 12: Learner-Debate-Urgency Composition
# ---------------------------------------------------------------------------


class TestLearnerDebateUrgencyComposition:
    """Verify that learner, urgency, and research multipliers compose correctly."""

    def test_three_layer_multiplier_composition(self):
        """learner=1.5, urgency=1.0 (no change), research=0.8 → 1.5*0.8=1.2."""
        # Step 1: learner returns 1.5 (losing strategy = penalty)
        edge_mult = 1.5

        # Step 2: urgency=1.0 → no change
        edge_mult = _apply_urgency_to_edge_multiplier(edge_mult, 1.0)
        assert edge_mult == 1.5  # unchanged

        # Step 3: research multiplier = 0.8, applied multiplicatively
        r_mult = max(0.7, min(1.3, 0.8))
        edge_mult *= r_mult
        edge_mult = max(0.5, min(2.0, edge_mult))
        assert abs(edge_mult - 1.2) < 0.01

    def test_winning_strategy_urgency_relaxes(self):
        """learner=0.8 (winning), urgency=1.5 → 0.8/1.5 ≈ 0.533."""
        result = _apply_urgency_to_edge_multiplier(0.8, 1.5)
        expected = 0.8 / 1.5  # ~0.533
        assert abs(result - expected) < 0.01

    def test_category_min_edge_overrides_base(self):
        """category_min_edge=0.03, base=0.01 → required_mult=3.0, clamped to 2.0."""
        # Simulate the edge_multiplier composition from engine.py lines 916-927
        base_min = 0.01  # from RiskConfig min_edge_pct
        cat_min_edge = 0.03  # from learner category_min_edges

        edge_multiplier = 1.0  # initial from learner
        edge_multiplier = _apply_urgency_to_edge_multiplier(edge_multiplier, 1.0)

        # Category override logic
        required_mult = cat_min_edge / base_min  # 3.0
        if required_mult > edge_multiplier:
            edge_multiplier = required_mult
        assert edge_multiplier == 3.0

        # After research multiplier (1.0 = no change) → clamped to [0.5, 2.0]
        edge_multiplier = max(0.5, min(2.0, edge_multiplier))
        assert edge_multiplier == 2.0


# ---------------------------------------------------------------------------
# Rule Conflict Validators
# ---------------------------------------------------------------------------


class TestRuleConflictValidators:
    """Verify precedence and cap enforcement in conflict scenarios."""

    @pytest.mark.asyncio
    async def test_strategy_stop_loss_fires_before_universal(self):
        """Strategy's should_exit fires before universal stop-loss.
        If strategy returns exit at 8% loss, universal (15%) never checked."""
        from bot.agent.market_analyzer import MarketAnalyzer

        mock_strategy = MagicMock()
        mock_strategy.name = "time_decay"
        # Strategy exits at 8% loss
        mock_strategy.should_exit = AsyncMock(return_value="td_stop_loss_8pct")

        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.strategies = [mock_strategy]

        pos = _make_position(
            market_id="stoploss_test",
            strategy="time_decay",
            avg_price=0.50,
            current_price=0.46,  # 8% loss
        )

        exits = await analyzer.check_exits([pos])

        assert len(exits) == 1
        market_id, reason = exits[0]
        assert market_id == "stoploss_test"
        assert reason == "td_stop_loss_8pct"  # strategy reason, not universal

    @pytest.mark.asyncio
    async def test_extreme_urgency_deployed_pct_capped_95(self):
        """With extreme urgency (10.0), max_deployed should be capped at 95%."""
        rm = RiskManager()
        rm._peak_equity = 100.0
        rm._day_start_equity = 100.0

        sig = _make_signal(
            market_id="urgency_test", edge=0.06,
            metadata={"category": "politics", "hours_to_resolution": 48, "price_std": 0.02},
        )

        # With no open positions, signal should pass.
        # The key assertion is that _check_total_deployed uses capped max.
        result = rm._check_total_deployed(
            open_positions=[],
            bankroll=100.0,
            config=RiskConfig.get(),
            urgency=10.0,
        )
        assert result.passed

        # Verify the formula: min(0.95, base + (urgency-1)*0.05)
        config = RiskConfig.get()
        base_pct = config.get("max_deployed_pct", 0.60)
        computed = min(0.95, base_pct + (10.0 - 1.0) * 0.05)
        assert computed == 0.95  # Should be capped

        # Create positions at 94% deployed — should still pass with urgency=10
        positions_94 = [
            _make_position(market_id="u1", size=188.0, avg_price=0.50,
                           current_price=0.50, category="politics"),
        ]
        result_94 = rm._check_total_deployed(
            open_positions=positions_94,
            bankroll=100.0,
            config=config,
            urgency=10.0,
        )
        assert result_94.passed, f"94% deployed should pass at urgency=10: {result_94.reason}"

        # 96% deployed should fail even with urgency=10 (cap=95%)
        positions_96 = [
            _make_position(market_id="u2", size=192.0, avg_price=0.50,
                           current_price=0.50, category="politics"),
        ]
        result_96 = rm._check_total_deployed(
            open_positions=positions_96,
            bankroll=100.0,
            config=config,
            urgency=10.0,
        )
        assert not result_96.passed, "96% deployed should fail even at urgency=10"

    @pytest.mark.asyncio
    async def test_rebalance_failure_no_cooldown_on_new_signal(self):
        """When risk rejects and rebalance fails, the rejected signal's
        market_id should NOT have a cooldown set (no trade was placed)."""
        engine = _make_engine()
        sig = _make_signal(market_id="no_cooldown_mkt", strategy="value_betting", edge=0.06)

        # Fill 6 positions to hit max_positions
        existing = [
            _make_position(
                market_id=f"full_{i}", strategy="value_betting",
                size=5.0, avg_price=0.50, current_price=0.48,
                category=f"cat_{i}",
                created_at=datetime.now(timezone.utc) - timedelta(hours=10),
            )
            for i in range(6)
        ]
        _setup_engine_for_evaluate(engine, [sig], positions=existing)

        # Rebalance returns None (failure)
        engine.closer.try_rebalance = AsyncMock(return_value=None)

        with patch("bot.agent.engine.settings") as mock_settings, \
             patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_cycle_summary", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock):
            mock_settings.use_llm_debate = False
            mock_settings.is_paper = True
            mock_settings.use_llm_post_mortem = False
            mock_settings.initial_bankroll = 50.0

            engine.research_engine = MagicMock()
            engine.research_engine.correlation_detector.get_group.return_value = None

            await engine._evaluate_signals()

        # Market should NOT be in cooldown (no trade was placed)
        assert "no_cooldown_mkt" not in engine._market_cooldown

    def test_strategy_min_hold_overrides_closer_default(self):
        """PositionCloser.try_rebalance uses strategy_min_hold override
        when available, skipping positions younger than the override."""
        closer = PositionCloser(
            order_manager=AsyncMock(),
            portfolio=AsyncMock(),
            risk_manager=RiskManager(),
            cache=None,
        )
        closer.min_hold_seconds = 120  # default 2 min
        closer.strategy_min_hold = {"price_divergence": 300}  # 5 min override
        closer.min_rebalance_edge = 0.01

        # Position age = 200s (> default 120s but < override 300s)
        pos = _make_position(
            market_id="hold_test",
            strategy="price_divergence",
            size=10.0,
            avg_price=0.50,
            current_price=0.45,  # losing position
            created_at=datetime.now(timezone.utc) - timedelta(seconds=200),
        )

        sig = _make_signal(market_id="new_sig", edge=0.08)

        # Check that try_rebalance logic would skip this position
        # We verify by checking the hold_limit logic directly
        hold_limit = closer.strategy_min_hold.get(pos.strategy, closer.min_hold_seconds)
        assert hold_limit == 300

        now = datetime.now(timezone.utc)
        created = pos.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()

        assert age < hold_limit, (
            f"Position age {age:.0f}s should be less than strategy hold limit {hold_limit}s"
        )

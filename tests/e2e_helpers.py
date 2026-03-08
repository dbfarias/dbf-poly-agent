"""Shared helpers for E2E strategy interaction tests.

Extracted from test_e2e_strategy_interaction.py so that multiple
E2E test modules can reuse the same engine/signal/position factories.
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bot.agent.engine import TradingEngine
from bot.agent.learner import LearnerAdjustments
from bot.agent.risk_manager import RiskManager
from bot.config import CapitalTier
from bot.data.models import Position
from bot.polymarket.types import OrderSide, TradeSignal


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
        metadata=metadata or {"category": "crypto", "hours_to_resolution": 48},
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
) -> LearnerAdjustments:
    return LearnerAdjustments(
        edge_multipliers=edge_multipliers or {},
        category_confidences={},
        paused_strategies=paused_strategies or set(),
        calibration=calibration or {},
        urgency_multiplier=urgency_multiplier,
        daily_progress=0.0,
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
):
    """Wire up engine mocks for _evaluate_signals testing."""
    engine.portfolio = AsyncMock()
    engine.portfolio.cash = 50.0
    engine.portfolio.total_equity = 50.0
    engine.portfolio.positions = positions or []
    engine.portfolio.tier = CapitalTier.TIER1
    engine.portfolio.open_position_count = len(positions or [])
    engine.portfolio.day_start_equity = 50.0
    engine.portfolio.realized_pnl_today = 0.0

    engine.analyzer = AsyncMock()
    engine.analyzer.scan_markets = AsyncMock(return_value=signals)

    engine.risk_manager = RiskManager()
    engine.risk_manager._peak_equity = 50.0
    engine.risk_manager._day_start_equity = 50.0

    engine.order_manager = AsyncMock()
    engine.order_manager.pending_count = 0
    engine.order_manager.pending_market_ids = set()

    engine._learner_adjustments = _make_learner_adjustments(
        paused_strategies=paused,
        urgency_multiplier=urgency,
        calibration=calibration,
    )
    engine.learner = MagicMock()
    engine.learner.get_edge_multiplier = MagicMock(return_value=1.0)
    engine.research_cache = MagicMock()
    engine.research_cache.get = MagicMock(return_value=None)

    if cooldowns:
        engine._market_cooldown = cooldowns

    # Rewire closer
    engine.closer.order_manager = engine.order_manager
    engine.closer.portfolio = engine.portfolio
    engine.closer.risk_manager = engine.risk_manager

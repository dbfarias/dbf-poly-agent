"""Main trading engine loop."""

import asyncio
from datetime import datetime, timezone

import structlog

from bot.agent.learner import PerformanceLearner
from bot.agent.market_analyzer import MarketAnalyzer
from bot.agent.order_manager import OrderManager
from bot.agent.portfolio import Portfolio
from bot.agent.risk_manager import RiskManager
from bot.config import settings
from bot.data.activity import (
    log_cycle_summary,
    log_daily_target_reached,
    log_exit_triggered,
    log_liquidity_rejected,
    log_position_closed,
    log_rebalance,
    log_risk_limit_hit,
    log_signal_found,
    log_signal_rejected,
    log_strategy_paused,
    prune_old_activity,
)
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import StrategyMetric
from bot.data.repositories import StrategyMetricRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.heartbeat import HeartbeatManager
from bot.polymarket.websocket_manager import WebSocketManager
from bot.research.cache import ResearchCache
from bot.research.engine import ResearchEngine
from bot.utils.notifications import (
    notify_daily_summary,
    notify_daily_target,
    notify_error,
    notify_risk_limit,
    notify_strategy_paused,
)

from .strategies.arbitrage import ArbitrageStrategy
from .strategies.market_making import MarketMakingStrategy
from .strategies.price_divergence import PriceDivergenceStrategy
from .strategies.swing_trading import SwingTradingStrategy
from .strategies.time_decay import TimeDecayStrategy
from .strategies.value_betting import ValueBettingStrategy

logger = structlog.get_logger()


def _apply_urgency_to_edge_multiplier(
    edge_multiplier: float, urgency: float
) -> float:
    """Combine learner edge_multiplier with daily urgency.

    Key insight: urgency should NEVER cancel a learner penalty.
    - urgency > 1.0 (behind target): only relax if strategy is winning (multiplier <= 1.0).
      If strategy has a penalty (>1.0), keep the penalty — don't reward bad strategies.
    - urgency < 1.0 (ahead of target): always tighten (divide by urgency raises the bar).
    - urgency = 1.0: no change.

    Returns clamped to [0.5, 2.0].
    """
    if urgency == 1.0:
        result = edge_multiplier
    elif urgency > 1.0:
        # Behind target — relax ONLY for winning/neutral strategies
        if edge_multiplier <= 1.0:
            result = edge_multiplier / urgency
        else:
            # Losing strategy: keep penalty, don't reduce it
            result = edge_multiplier
    else:
        # Ahead of target — tighten all strategies
        result = edge_multiplier / urgency

    return max(0.5, min(2.0, result))


class TradingEngine:
    """Main trading engine that orchestrates the bot's operation."""

    def __init__(self):
        # Clients
        self.clob_client = PolymarketClient()
        self.gamma_client = GammaClient()
        self.data_api = DataApiClient()
        self.cache = MarketCache(default_ttl=120)

        # Components
        self.portfolio = Portfolio(self.clob_client, self.data_api, self.gamma_client)
        self.risk_manager = RiskManager()
        self.order_manager = OrderManager(self.clob_client, self.data_api)
        self.learner = PerformanceLearner()

        # Research engine
        self.research_cache = ResearchCache(default_ttl=3600)
        self.research_engine = ResearchEngine(self.research_cache, self.cache)

        # WebSocket + Heartbeat
        self.ws_manager = WebSocketManager(self.cache)
        self.heartbeat = HeartbeatManager(self.clob_client)

        # Strategies (ordered by priority)
        strategies = [
            ArbitrageStrategy(self.clob_client, self.gamma_client, self.cache),
            TimeDecayStrategy(self.clob_client, self.gamma_client, self.cache),
            PriceDivergenceStrategy(
                self.clob_client, self.gamma_client, self.cache,
                research_cache=self.research_cache,
            ),
            SwingTradingStrategy(self.clob_client, self.gamma_client, self.cache),
            ValueBettingStrategy(self.clob_client, self.gamma_client, self.cache),
            MarketMakingStrategy(self.clob_client, self.gamma_client, self.cache),
        ]
        self.analyzer = MarketAnalyzer(
            self.gamma_client, self.cache, strategies, self.clob_client
        )

        # State
        self._running = False
        self._cycle_count = 0
        self._last_snapshot: datetime | None = None
        self._last_daily_summary: str = ""
        self._learner_adjustments = None
        self._rebalanced_this_cycle = False
        self.disabled_strategies: set[str] = set()
        self._target_notified_day: str = ""
        self._risk_limit_notified: dict[str, str] = {}  # {limit_type: day_key}

    @property
    def is_running(self) -> bool:
        return self._running

    async def initialize(self) -> None:
        """Initialize all clients and sync state."""
        logger.info(
            "engine_initializing",
            mode=settings.trading_mode.value,
            bankroll=settings.initial_bankroll,
        )

        await self.clob_client.initialize()
        await self.gamma_client.initialize()
        await self.data_api.initialize()
        await self.portfolio.sync()
        await self._seed_strategy_metrics()

        # Restore persisted settings from DB (overrides defaults)
        from bot.data.settings_store import SettingsStore

        restored = await SettingsStore.load_and_apply(self)
        if restored > 0:
            logger.info("settings_restored_from_db", count=restored)

        # Wire up deferred fill callbacks for live orders
        self.order_manager.set_on_fill_callback(self._handle_order_fill)
        self.order_manager.set_on_sell_fill_callback(self._handle_sell_fill)

        logger.info(
            "engine_initialized",
            equity=self.portfolio.total_equity,
            tier=self.portfolio.tier.value,
            positions=self.portfolio.open_position_count,
        )

    async def shutdown(self) -> None:
        """Clean shutdown."""
        self._running = False
        await self.research_engine.stop()
        await self.heartbeat.stop()
        await self.ws_manager.disconnect()
        await self.gamma_client.close()
        await self.data_api.close()
        logger.info("engine_shutdown")

    def _task_exception_handler(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "background_task_failed",
                task=task.get_name(),
                error=str(exc),
                exc_type=type(exc).__name__,
            )

    async def run(self) -> None:
        """Main trading loop."""
        self._running = True
        logger.info("engine_started", scan_interval=settings.scan_interval_seconds)

        # Start background tasks with exception handlers
        hb_task = asyncio.create_task(self.heartbeat.start())
        hb_task.add_done_callback(self._task_exception_handler)
        ws_task = asyncio.create_task(self.ws_manager.connect())
        ws_task.add_done_callback(self._task_exception_handler)
        research_task = asyncio.create_task(self.research_engine.start())
        research_task.add_done_callback(self._task_exception_handler)

        while self._running:
            try:
                await self._trading_cycle()
            except Exception as e:
                logger.error("trading_cycle_error", error=str(e), cycle=self._cycle_count)
                await notify_error("trading_cycle", str(e))

            await asyncio.sleep(settings.scan_interval_seconds)

    async def _trading_cycle(self) -> None:
        """Single trading cycle: scan → evaluate → execute → monitor."""
        self._cycle_count += 1
        self._rebalanced_this_cycle = False
        tier = self.portfolio.tier

        logger.info(
            "cycle_start",
            cycle=self._cycle_count,
            equity=self.portfolio.total_equity,
            tier=tier.value,
            positions=self.portfolio.open_position_count,
        )

        # 1. Sync portfolio state
        await self.portfolio.sync()
        self.risk_manager.update_peak_equity(self.portfolio.total_equity)

        # 2. Update learner stats (lightweight — queries recent trades)
        #    Feed daily context so urgency multiplier reflects target progress
        self.learner.set_daily_context(
            realized_pnl=self.portfolio._realized_pnl_today,
            equity=self.portfolio._day_start_equity,
            target_pct=settings.daily_target_pct,
        )
        try:
            self._learner_adjustments = await self.learner.compute_stats()
            if self._learner_adjustments.paused_strategies:
                logger.info(
                    "learner_paused_strategies",
                    strategies=list(self._learner_adjustments.paused_strategies),
                )
            # Notify on newly paused strategies
            for s_name, s_wr, s_pnl in self.learner._newly_paused:
                await log_strategy_paused(s_name, s_wr, s_pnl)
                await notify_strategy_paused(
                    s_name, f"Win rate {s_wr:.0%}, PnL ${s_pnl:+.2f}"
                )

            # Check if daily target was reached (notify once per day)
            if self._learner_adjustments.urgency_multiplier < 1.0:
                day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self._target_notified_day != day_key:
                    self._target_notified_day = day_key
                    await log_daily_target_reached(
                        self.portfolio.total_equity,
                        self.portfolio._realized_pnl_today,
                        settings.daily_target_pct,
                    )
                    await notify_daily_target(
                        self.portfolio.total_equity,
                        self.portfolio._realized_pnl_today,
                        settings.daily_target_pct,
                    )

            # Apply adjustments to strategies (including urgency for dynamic horizons)
            adj_dict = {
                "edge_multipliers": self._learner_adjustments.edge_multipliers,
                "category_confidences": self._learner_adjustments.category_confidences,
                "calibration": self._learner_adjustments.calibration,
                "urgency_multiplier": self._learner_adjustments.urgency_multiplier,
            }
            for strategy in self.analyzer.strategies:
                strategy.adjust_params(adj_dict)
        except Exception as e:
            logger.error("learner_compute_failed", error=str(e))

        # 3. Check for exits on open positions
        exits = await self.analyzer.check_exits(self.portfolio.positions, tier)
        for market_id in exits:
            pos = next((p for p in self.portfolio.positions if p.market_id == market_id), None)
            if pos:
                await self._close_position(pos)

        # 4. Scan markets for new opportunities
        signals = await self.analyzer.scan_markets(tier)

        # 5. Evaluate signals against risk manager
        # Track capital committed in this cycle to prevent over-allocation
        cycle_committed = 0.0
        pending_count = self.order_manager.pending_count
        pending_markets = self.order_manager.pending_market_ids
        _signals_approved = 0
        _orders_placed = 0

        for signal in signals:
            logger.info(
                "evaluating_signal",
                strategy=signal.strategy,
                market_id=signal.market_id[:20],
                edge=round(signal.edge, 4),
                price=signal.market_price,
                question=signal.question[:50],
            )

            await log_signal_found(
                strategy=signal.strategy,
                market_id=signal.market_id,
                question=signal.question,
                edge=signal.edge,
                price=signal.market_price,
                prob=signal.estimated_prob,
                hours=signal.metadata.get("hours_to_resolution"),
            )

            # Skip strategies paused by learner
            if (
                self._learner_adjustments
                and signal.strategy in self._learner_adjustments.paused_strategies
            ):
                logger.info(
                    "signal_skipped_strategy_paused",
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                )
                await log_signal_rejected(
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    question=signal.question,
                    reason=f"Strategy {signal.strategy} paused by learner",
                    edge=signal.edge,
                    price=signal.market_price,
                )
                continue

            # Skip markets with existing pending orders
            if signal.market_id in pending_markets:
                logger.info(
                    "signal_skipped_pending_order",
                    market_id=signal.market_id[:20],
                    strategy=signal.strategy,
                )
                await log_signal_rejected(
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    question=signal.question,
                    reason="Already has a pending order on this market",
                    edge=signal.edge,
                    price=signal.market_price,
                )
                continue

            effective_bankroll = self.portfolio.total_equity - cycle_committed

            # Get edge multiplier from learner (historical performance)
            category = signal.metadata.get("category", "")
            edge_multiplier = (
                self.learner.get_edge_multiplier(signal.strategy, category)
                if self._learner_adjustments
                else 1.0
            )

            # Combine with daily target urgency (without canceling penalties)
            if self._learner_adjustments:
                urgency = self._learner_adjustments.urgency_multiplier
                edge_multiplier = _apply_urgency_to_edge_multiplier(
                    edge_multiplier, urgency
                )

            # Apply research sentiment multiplier (news-driven edge adjustment)
            research = self.research_cache.get(signal.market_id)
            if research is not None:
                r_mult = max(0.7, min(1.3, research.research_multiplier))
                edge_multiplier *= r_mult
                edge_multiplier = max(0.5, min(2.0, edge_multiplier))
                signal.metadata["research_sentiment"] = research.sentiment_score
                signal.metadata["research_multiplier"] = r_mult

            approved, size, reason = await self.risk_manager.evaluate_signal(
                signal=signal,
                bankroll=effective_bankroll,
                open_positions=self.portfolio.positions,
                tier=tier,
                pending_count=pending_count,
                edge_multiplier=edge_multiplier,
            )

            if not approved:
                # Try rebalancing: close weakest loser to make room for a better signal
                closed_pos = None
                if (
                    "Max positions" in reason
                    and not self._rebalanced_this_cycle
                ):
                    closed_pos = await self._try_rebalance(signal)

                if closed_pos is not None:
                    self._rebalanced_this_cycle = True

                    # Record PnL for the closed position (sell already on CLOB)
                    pnl = await self.portfolio.record_trade_close(
                        closed_pos.market_id, closed_pos.current_price
                    )
                    self.risk_manager.update_daily_pnl(pnl)
                    await log_position_closed(
                        market_id=closed_pos.market_id,
                        question=closed_pos.question,
                        strategy=closed_pos.strategy,
                        pnl=pnl,
                        exit_reason="rebalance",
                    )

                    # Re-evaluate with updated positions (one slot freed)
                    approved, size, reason = await self.risk_manager.evaluate_signal(
                        signal=signal,
                        bankroll=self.portfolio.total_equity - cycle_committed,
                        open_positions=self.portfolio.positions,
                        tier=tier,
                        pending_count=pending_count,
                        edge_multiplier=edge_multiplier,
                    )
                    if not approved:
                        logger.warning(
                            "signal_rejected_after_rebalance",
                            strategy=signal.strategy,
                            reason=reason,
                            closed_market=closed_pos.market_id,
                        )
                        await log_signal_rejected(
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                            question=signal.question,
                            reason=f"Post-rebalance: {reason}",
                            edge=signal.edge,
                            price=signal.market_price,
                        )
                        continue
                    # Falls through to liquidity check + execution below
                else:
                    logger.debug("signal_rejected", strategy=signal.strategy, reason=reason)
                    await log_signal_rejected(
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        question=signal.question,
                        reason=reason,
                        edge=signal.edge,
                        price=signal.market_price,
                    )
                    # Notify on risk limit breaches (once per day per type)
                    await self._maybe_notify_risk_limit(reason)
                    continue

            # 5b. Check order book liquidity before executing
            if not await self._check_liquidity(signal):
                continue

            _signals_approved += 1

            # Update signal with approved size
            signal.size_usd = size

            # 6. Execute trade
            trade = await self.order_manager.execute_signal(signal)
            if trade and trade.status == "filled":
                _orders_placed += 1
                # Mark scan as traded for signal quality feedback
                await self._mark_scan_traded(signal)
                # Immediately filled (paper mode or CLOB matched)
                await self.portfolio.record_trade_open(
                    market_id=signal.market_id,
                    token_id=signal.token_id,
                    question=signal.question,
                    outcome=signal.outcome,
                    category=signal.metadata.get("category", ""),
                    strategy=signal.strategy,
                    side=signal.side.value,
                    size=trade.size,
                    price=trade.price,
                )
                cycle_committed += trade.cost_usd
            elif trade:
                _orders_placed += 1
                # Mark scan as traded for signal quality feedback
                await self._mark_scan_traded(signal)
                # Pending order — track committed capital for this cycle
                cycle_committed += trade.cost_usd
                pending_count += 1
                pending_markets.add(signal.market_id)
                logger.info(
                    "order_pending",
                    trade_id=trade.id,
                    market_id=signal.market_id,
                    status=trade.status,
                    cycle_committed=cycle_committed,
                )

        # 7. Monitor pending orders
        await self.order_manager.monitor_orders()

        # 8. Take periodic snapshot
        await self._maybe_snapshot()

        # 9. Daily summary
        await self._maybe_daily_summary()

        _urgency = (
            round(self._learner_adjustments.urgency_multiplier, 2)
            if self._learner_adjustments else 1.0
        )
        _progress = (
            round(self._learner_adjustments.daily_progress, 2)
            if self._learner_adjustments else 0.0
        )

        logger.info(
            "cycle_complete",
            cycle=self._cycle_count,
            equity=self.portfolio.total_equity,
            pending_orders=self.order_manager.pending_count,
            daily_urgency=_urgency,
            daily_progress=_progress,
        )

        # Log activity summary (every 5th cycle to avoid noise)
        if self._cycle_count % 5 == 0 or _orders_placed > 0:
            await log_cycle_summary(
                cycle=self._cycle_count,
                equity=self.portfolio.total_equity,
                signals_found=len(signals),
                signals_approved=_signals_approved,
                orders_placed=_orders_placed,
                pending_orders=self.order_manager.pending_count,
                urgency=_urgency,
                daily_progress=_progress,
            )

        # Prune old activity rows periodically (every 50 cycles)
        if self._cycle_count % 50 == 0:
            await prune_old_activity()

    async def _try_rebalance(self, signal):
        """Close the weakest losing position to make room for a better signal.

        Returns the closed Position if successful, None if no rebalance happened.
        PnL recording is deferred to the caller — the sell already happened on
        CLOB regardless of whether the replacement signal gets approved.
        """
        min_rebalance_edge = 0.03   # Only rebalance for high-edge signals
        min_hold_seconds = 300      # Don't sell positions held < 5 minutes
        min_sell_shares = 5.0       # Polymarket CLOB minimum

        if signal.edge < min_rebalance_edge:
            return None

        # Find candidates: losing positions that can actually be sold
        candidates = []
        now = datetime.now(timezone.utc)
        for pos in self.portfolio.positions:
            # Skip positions too small to sell on CLOB (paper mode exempt)
            if not settings.is_paper and pos.size < min_sell_shares:
                continue
            # Skip positions held less than 5 minutes
            created = pos.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and (now - created).total_seconds() < min_hold_seconds:
                continue
            # Never close winners
            if pos.unrealized_pnl > 0:
                continue
            # Score by unrealized PnL percentage (more negative = worse)
            pnl_pct = (
                (pos.current_price - pos.avg_price) / pos.avg_price
                if pos.avg_price > 0 else 0.0
            )
            candidates.append((pos, pnl_pct))

        if not candidates:
            return None

        # Pick the worst performer (lowest PnL%)
        candidates.sort(key=lambda x: x[1])
        worst_pos, worst_pnl_pct = candidates[0]

        logger.info(
            "rebalance_attempt",
            closing_market=worst_pos.market_id[:20],
            closing_strategy=worst_pos.strategy,
            closing_pnl_pct=round(worst_pnl_pct, 4),
            new_signal_strategy=signal.strategy,
            new_signal_edge=round(signal.edge, 4),
        )

        # Close the worst position
        close_result = await self.order_manager.close_position(
            market_id=worst_pos.market_id,
            token_id=worst_pos.token_id,
            size=worst_pos.size,
            current_price=worst_pos.current_price,
            question=worst_pos.question,
            outcome=worst_pos.outcome,
            category=worst_pos.category,
            strategy=worst_pos.strategy,
        )
        if close_result is None:
            logger.warning("rebalance_close_failed", market_id=worst_pos.market_id)
            return None

        await log_rebalance(
            closed_market_id=worst_pos.market_id,
            closed_question=worst_pos.question,
            closed_strategy=worst_pos.strategy,
            closed_pnl=0.0,  # PnL deferred to caller
            new_market_id=signal.market_id,
            new_question=signal.question,
            new_strategy=signal.strategy,
            new_edge=signal.edge,
        )

        logger.info(
            "rebalance_closed_position",
            closed_market=worst_pos.market_id[:20],
            new_signal=signal.market_id[:20],
        )
        return worst_pos

    async def _maybe_notify_risk_limit(self, reason: str) -> None:
        """Send a one-time daily notification when a risk limit is breached."""
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if "Daily loss limit" in reason:
            if self._risk_limit_notified.get("daily_loss") != day_key:
                self._risk_limit_notified["daily_loss"] = day_key
                config = self.risk_manager.get_risk_metrics(self.portfolio.total_equity)
                await log_risk_limit_hit(
                    "daily_loss",
                    abs(self.risk_manager._daily_pnl / self.portfolio.total_equity)
                    if self.portfolio.total_equity > 0 else 0.0,
                    config["daily_loss_limit_pct"],
                )
                await notify_risk_limit(
                    "daily_loss",
                    abs(self.risk_manager._daily_pnl / self.portfolio.total_equity)
                    if self.portfolio.total_equity > 0 else 0.0,
                    config["daily_loss_limit_pct"],
                )
        elif "Max drawdown" in reason:
            if self._risk_limit_notified.get("max_drawdown") != day_key:
                self._risk_limit_notified["max_drawdown"] = day_key
                config = self.risk_manager.get_risk_metrics(self.portfolio.total_equity)
                await log_risk_limit_hit(
                    "max_drawdown",
                    config["current_drawdown_pct"],
                    config["max_drawdown_limit_pct"],
                )
                await notify_risk_limit(
                    "max_drawdown",
                    config["current_drawdown_pct"],
                    config["max_drawdown_limit_pct"],
                )

    async def _close_position(self, pos) -> None:
        """Close a position and record PnL if immediately filled.

        In paper mode, the sell is always "filled" so we record immediately.
        In live mode, the sell may be "pending" on the CLOB — PnL recording
        is deferred to _handle_sell_fill() callback when the order confirms.
        """
        await log_exit_triggered(
            market_id=pos.market_id,
            question=pos.question,
            strategy=pos.strategy,
            current_price=pos.current_price,
        )
        trade = await self.order_manager.close_position(
            market_id=pos.market_id,
            token_id=pos.token_id,
            size=pos.size,
            current_price=pos.current_price,
            question=pos.question,
            outcome=pos.outcome,
            category=pos.category,
            strategy=pos.strategy,
        )
        if trade is None:
            return

        if trade.status == "filled":
            # Paper mode or immediate CLOB match — record now
            pnl = await self.portfolio.record_trade_close(pos.market_id, pos.current_price)
            self.risk_manager.update_daily_pnl(pnl)
            await log_position_closed(
                market_id=pos.market_id,
                question=pos.question,
                strategy=pos.strategy,
                pnl=pnl,
                exit_reason="strategy_exit",
            )
        else:
            # Live pending — will be recorded via _handle_sell_fill callback
            logger.info(
                "sell_pending_on_clob",
                market_id=pos.market_id,
                strategy=pos.strategy,
            )

    async def _handle_sell_fill(
        self, market_id: str, sell_price: float, trade_id: int, shares: float,
    ) -> None:
        """Callback when a pending live SELL order is confirmed filled.

        Calculates PnL via portfolio, then writes it back to the Trade record
        so the dashboard shows accurate profit/loss for sell orders.
        """
        logger.info(
            "deferred_sell_fill",
            market_id=market_id,
            sell_price=sell_price,
            trade_id=trade_id,
        )
        pnl = await self.portfolio.record_trade_close(market_id, sell_price)
        self.risk_manager.update_daily_pnl(pnl)

        # Write PnL back to the Trade record (fixes stale $0 PnL on sells)
        from bot.data.repositories import TradeRepository

        async with async_session() as session:
            repo = TradeRepository(session)
            await repo.update_status(trade_id, "filled", pnl=pnl)

        await log_position_closed(
            market_id=market_id,
            question="",
            strategy="",
            pnl=pnl,
            exit_reason="deferred_sell_fill",
        )

    async def _handle_order_fill(self, signal, shares: float) -> None:
        """Callback when a pending live BUY order is confirmed filled."""
        logger.info(
            "deferred_fill_creating_position",
            market_id=signal.market_id,
            strategy=signal.strategy,
            shares=shares,
        )
        await self.portfolio.record_trade_open(
            market_id=signal.market_id,
            token_id=signal.token_id,
            question=signal.question,
            outcome=signal.outcome,
            category=signal.metadata.get("category", ""),
            strategy=signal.strategy,
            side=signal.side.value,
            size=shares,
            price=signal.market_price,
        )

    async def _mark_scan_traded(self, signal) -> None:
        """Mark the most recent scan for this signal as traded."""
        try:
            async with async_session() as session:
                from bot.data.repositories import TradeRepository

                repo = TradeRepository(session)
                await repo.mark_scan_traded(signal.market_id, signal.strategy)
        except Exception as e:
            logger.debug("mark_scan_traded_failed", error=str(e))

    async def _check_liquidity(self, signal) -> bool:
        """Check order book has reasonable exit liquidity before trading.

        Verifies:
        1. Spread is within limits (5 cents)
        2. Best bid is near fair price (can actually sell if needed)
        """
        max_spread = 0.05  # 5 cents max spread (CLOB pre-trade check)
        min_bid_ratio = MarketAnalyzer.MIN_BID_RATIO

        try:
            book = await self.clob_client.get_order_book(signal.token_id)
            spread = book.spread
            if spread is None or spread > max_spread:
                logger.info(
                    "illiquid_market_skipped",
                    market_id=signal.market_id,
                    spread=spread,
                    max_spread=max_spread,
                )
                await log_liquidity_rejected(
                    market_id=signal.market_id,
                    reason=f"Spread too wide: {spread} > {max_spread} max",
                    spread=spread,
                )
                return False

            # Ensure we can exit: best bid must be near fair price
            if book.best_bid is not None:
                fair_price = signal.market_price
                if fair_price > 0.10 and book.best_bid < fair_price * min_bid_ratio:
                    logger.info(
                        "no_exit_liquidity",
                        market_id=signal.market_id,
                        best_bid=book.best_bid,
                        fair_price=fair_price,
                    )
                    await log_liquidity_rejected(
                        market_id=signal.market_id,
                        reason=(
                            f"No exit liquidity: bid ${book.best_bid:.3f}"
                            f" too far from price ${fair_price:.3f}"
                        ),
                        best_bid=book.best_bid,
                    )
                    return False

            return True
        except Exception as e:
            logger.warning("liquidity_check_failed", error=str(e))
            return False

    async def _maybe_snapshot(self) -> None:
        """Take a snapshot if enough time has passed."""
        now = datetime.now(timezone.utc)
        if (
            self._last_snapshot is None
            or (now - self._last_snapshot).total_seconds() >= settings.snapshot_interval_seconds
        ):
            await self.portfolio.take_snapshot()
            self._last_snapshot = now

    async def _maybe_daily_summary(self) -> None:
        """Send daily summary at midnight UTC."""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if self._last_daily_summary == today:
            return
        if now.hour == 0 and now.minute < 2:
            self._last_daily_summary = today
            overview = self.portfolio.get_overview()

            # Compute real daily stats from trade history
            try:
                async with async_session() as session:
                    from bot.data.repositories import TradeRepository

                    repo = TradeRepository(session)
                    today_stats = await repo.get_today_stats()
            except Exception:
                today_stats = {"trades_today": 0, "win_rate_today": 0.0}

            equity = overview["total_equity"]
            daily_pnl = overview["realized_pnl_today"]
            day_start = overview.get("day_start_equity", equity)
            daily_return = daily_pnl / day_start if day_start > 0 else 0.0

            await notify_daily_summary(
                equity=equity,
                daily_pnl=daily_pnl,
                daily_return=daily_return,
                trades=today_stats["trades_today"],
                win_rate=today_stats["win_rate_today"],
            )

    async def _seed_strategy_metrics(self) -> None:
        """Ensure all strategies have a StrategyMetric record so the page is never empty."""
        strategy_names = [s.name for s in self.analyzer.strategies]
        async with async_session() as session:
            repo = StrategyMetricRepository(session)
            existing = await repo.get_all_latest()
            existing_names = {m.strategy for m in existing}
            for name in strategy_names:
                if name not in existing_names:
                    await repo.upsert(StrategyMetric(strategy=name))
                    logger.debug("strategy_metric_seeded", strategy=name)

    def register_strategy(self, strategy) -> None:
        """Dynamically register a new strategy."""
        self.analyzer.strategies.append(strategy)
        logger.info("strategy_registered", strategy=strategy.name)

    def get_status(self) -> dict:
        """Get engine status for the dashboard."""
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "mode": settings.trading_mode.value,
            "portfolio": self.portfolio.get_overview(),
            "risk": self.risk_manager.get_risk_metrics(self.portfolio.total_equity),
            "pending_orders": self.order_manager.pending_count,
            "cache_stats": self.cache.stats,
            "research_stats": self.research_cache.stats,
        }

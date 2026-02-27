"""Main trading engine loop."""

import asyncio
from datetime import datetime

import structlog

from bot.agent.learner import PerformanceLearner
from bot.agent.market_analyzer import MarketAnalyzer
from bot.agent.order_manager import OrderManager
from bot.agent.portfolio import Portfolio
from bot.agent.risk_manager import RiskManager
from bot.config import settings
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import StrategyMetric
from bot.data.repositories import StrategyMetricRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.heartbeat import HeartbeatManager
from bot.polymarket.websocket_manager import WebSocketManager
from bot.utils.notifications import notify_daily_summary, notify_error

from .strategies.arbitrage import ArbitrageStrategy
from .strategies.market_making import MarketMakingStrategy
from .strategies.time_decay import TimeDecayStrategy
from .strategies.value_betting import ValueBettingStrategy

logger = structlog.get_logger()


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

        # WebSocket + Heartbeat
        self.ws_manager = WebSocketManager(self.cache)
        self.heartbeat = HeartbeatManager(self.clob_client)

        # Strategies (ordered by priority)
        strategies = [
            ArbitrageStrategy(self.clob_client, self.gamma_client, self.cache),
            TimeDecayStrategy(self.clob_client, self.gamma_client, self.cache),
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

        # Wire up deferred fill callback for live orders
        self.order_manager.set_on_fill_callback(self._handle_order_fill)

        logger.info(
            "engine_initialized",
            equity=self.portfolio.total_equity,
            tier=self.portfolio.tier.value,
            positions=self.portfolio.open_position_count,
        )

    async def shutdown(self) -> None:
        """Clean shutdown."""
        self._running = False
        await self.heartbeat.stop()
        await self.ws_manager.disconnect()
        await self.gamma_client.close()
        await self.data_api.close()
        logger.info("engine_shutdown")

    async def run(self) -> None:
        """Main trading loop."""
        self._running = True
        logger.info("engine_started", scan_interval=settings.scan_interval_seconds)

        # Start background tasks
        asyncio.create_task(self.heartbeat.start())
        asyncio.create_task(self.ws_manager.connect())

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
            equity=self.portfolio.total_equity,
            target_pct=settings.daily_target_pct,
        )
        try:
            self._learner_adjustments = await self.learner.compute_stats()
            if self._learner_adjustments.paused_strategies:
                logger.info(
                    "learner_paused_strategies",
                    strategies=list(self._learner_adjustments.paused_strategies),
                )
            # Apply adjustments to strategies
            adj_dict = {
                "edge_multipliers": self._learner_adjustments.edge_multipliers,
                "category_confidences": self._learner_adjustments.category_confidences,
                "calibration": self._learner_adjustments.calibration,
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
                await self.order_manager.close_position(
                    market_id=pos.market_id,
                    token_id=pos.token_id,
                    size=pos.size,
                    current_price=pos.current_price,
                )
                pnl = await self.portfolio.record_trade_close(market_id, pos.current_price)
                self.risk_manager.update_daily_pnl(pnl)

        # 4. Scan markets for new opportunities
        signals = await self.analyzer.scan_markets(tier)

        # 5. Evaluate signals against risk manager
        # Track capital committed in this cycle to prevent over-allocation
        cycle_committed = 0.0
        pending_count = self.order_manager.pending_count
        pending_markets = self.order_manager.pending_market_ids

        for signal in signals:
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
                continue

            # Skip markets with existing pending orders
            if signal.market_id in pending_markets:
                logger.debug(
                    "signal_skipped_pending_order",
                    market_id=signal.market_id,
                    strategy=signal.strategy,
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

            # Combine with daily target urgency:
            # urgency > 1 = behind target → lower edge requirement → more trades
            # urgency < 1 = ahead of target → raise edge requirement → fewer trades
            if self._learner_adjustments:
                urgency = self._learner_adjustments.urgency_multiplier
                edge_multiplier = edge_multiplier / urgency
                edge_multiplier = max(0.5, min(2.0, edge_multiplier))

            approved, size, reason = await self.risk_manager.evaluate_signal(
                signal=signal,
                bankroll=effective_bankroll,
                open_positions=self.portfolio.positions,
                tier=tier,
                pending_count=pending_count,
                edge_multiplier=edge_multiplier,
            )

            if not approved:
                logger.debug("signal_rejected", strategy=signal.strategy, reason=reason)
                continue

            # 5b. Check order book liquidity before executing
            if not await self._check_liquidity(signal):
                continue

            # Update signal with approved size
            signal.size_usd = size

            # 6. Execute trade
            trade = await self.order_manager.execute_signal(signal)
            if trade and trade.status == "filled":
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

        logger.info(
            "cycle_complete",
            cycle=self._cycle_count,
            equity=self.portfolio.total_equity,
            pending_orders=self.order_manager.pending_count,
            daily_urgency=(
                round(self._learner_adjustments.urgency_multiplier, 2)
                if self._learner_adjustments else 1.0
            ),
            daily_progress=(
                round(self._learner_adjustments.daily_progress, 2)
                if self._learner_adjustments else 0.0
            ),
        )

    async def _handle_order_fill(self, signal, shares: float) -> None:
        """Callback when a pending live order is confirmed filled."""
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
        """Check order book spread before trading. Skip illiquid markets."""
        max_spread = 0.05  # 5 cents max spread
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
                return False
            return True
        except Exception as e:
            logger.warning("liquidity_check_failed", error=str(e))
            return False

    async def _maybe_snapshot(self) -> None:
        """Take a snapshot if enough time has passed."""
        now = datetime.utcnow()
        if (
            self._last_snapshot is None
            or (now - self._last_snapshot).total_seconds() >= settings.snapshot_interval_seconds
        ):
            await self.portfolio.take_snapshot()
            self._last_snapshot = now

    async def _maybe_daily_summary(self) -> None:
        """Send daily summary at midnight UTC."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_daily_summary == today:
            return
        if datetime.utcnow().hour == 0 and datetime.utcnow().minute < 2:
            self._last_daily_summary = today
            overview = self.portfolio.get_overview()
            await notify_daily_summary(
                equity=overview["total_equity"],
                daily_pnl=overview["realized_pnl_today"],
                daily_return=0.0,
                trades=0,
                win_rate=0.0,
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
        }

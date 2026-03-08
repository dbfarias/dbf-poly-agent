"""Main trading engine loop."""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from bot.agent.events import event_bus
from bot.agent.learner import PerformanceLearner
from bot.agent.market_analyzer import MarketAnalyzer
from bot.agent.order_manager import OrderManager
from bot.agent.portfolio import Portfolio
from bot.agent.position_closer import PositionCloser
from bot.agent.risk_manager import RiskManager
from bot.config import TierConfig, settings, trading_day
from bot.data.activity import (
    log_cycle_summary,
    log_daily_target_reached,
    log_liquidity_rejected,
    log_llm_debate,
    log_llm_review,
    log_position_closed,
    log_risk_debate,
    log_risk_limit_hit,
    log_signal_found,
    log_signal_rejected,
    log_strategy_paused,
    prune_old_activity,
)
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import StrategyMetric
from bot.data.price_tracker import PriceTracker
from bot.data.repositories import StrategyMetricRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.heartbeat import HeartbeatManager
from bot.polymarket.websocket_manager import WebSocketManager
from bot.research.cache import ResearchCache
from bot.research.engine import ResearchEngine
from bot.research.llm_debate import cost_tracker as llm_cost_tracker
from bot.research.llm_debate import debate_risk_rejection, debate_signal, review_position
from bot.research.market_report import generate_daily_report
from bot.utils.notifications import (
    close_telegram_client,
    notify_daily_summary,
    notify_daily_target,
    notify_error,
    notify_market_report,
    notify_risk_limit,
    notify_strategy_paused,
)
from bot.utils.push_notifications import (
    push_notify_daily_summary,
    push_notify_error,
    push_notify_risk_limit,
    push_notify_strategy_paused,
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
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio(
            self.clob_client, self.data_api, self.gamma_client,
            risk_manager=self.risk_manager,
        )
        self.order_manager = OrderManager(self.clob_client, self.data_api)
        self.learner = PerformanceLearner()
        self.closer = PositionCloser(
            self.order_manager, self.portfolio,
            self.risk_manager, self.cache,
        )

        # Research engine
        self.research_cache = ResearchCache(default_ttl=3600)
        self.research_engine = ResearchEngine(self.research_cache, self.cache)

        # WebSocket + Heartbeat
        self.ws_manager = WebSocketManager(self.cache)
        self.heartbeat = HeartbeatManager(self.clob_client)

        # Shared price tracker (in-memory, ~6h history per market)
        self.price_tracker = PriceTracker()

        # Wire price tracker and whale detector into WebSocket + research
        self.ws_manager.price_tracker = self.price_tracker
        self.research_engine.whale_detector = self.ws_manager.whale_detector

        # Strategies (ordered by priority)
        strategies = [
            ArbitrageStrategy(self.clob_client, self.gamma_client, self.cache),
            TimeDecayStrategy(
                self.clob_client, self.gamma_client, self.cache,
                price_tracker=self.price_tracker,
            ),
            PriceDivergenceStrategy(
                self.clob_client, self.gamma_client, self.cache,
                research_cache=self.research_cache,
            ),
            SwingTradingStrategy(
                self.clob_client, self.gamma_client, self.cache,
                price_tracker=self.price_tracker,
            ),
            ValueBettingStrategy(
                self.clob_client, self.gamma_client, self.cache,
                price_tracker=self.price_tracker,
            ),
            MarketMakingStrategy(self.clob_client, self.gamma_client, self.cache),
        ]
        self.analyzer = MarketAnalyzer(
            self.gamma_client, self.cache, strategies, self.clob_client,
            price_tracker=self.price_tracker,
            correlation_detector=self.research_engine.correlation_detector,
        )

        # Populate per-strategy hold times on closer
        for strat in strategies:
            hold = getattr(strat, "MIN_HOLD_SECONDS", None)
            if hold is not None:
                self.closer.strategy_min_hold[strat.name] = hold

        # LLM cost tracker budget sync
        llm_cost_tracker.daily_budget = settings.llm_daily_budget

        # State
        self._running = False
        self._cycle_count = 0
        self._last_snapshot: datetime | None = None
        self._last_daily_summary: str = ""
        self._learner_adjustments = None
        self._rebalanced_this_cycle = False
        self.disabled_strategies: set[str] = set()
        self._last_report_date: str = ""
        self._target_notified_day: str = ""
        self._risk_limit_notified: dict[str, str] = {}  # {limit_type: day_key}
        self._market_cooldown: dict[str, datetime] = {}  # {market_id: tradeable_after}
        self.market_cooldown_hours: float = 1.0  # configurable via admin API

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

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

        # Run settings migrations before restoring (fixes stale DB values)
        from bot.data.settings_store import SettingsStore

        await SettingsStore.run_migrations()

        # Restore persisted settings from DB (overrides defaults)
        restored = await SettingsStore.load_and_apply(self)
        if restored > 0:
            logger.info("settings_restored_from_db", count=restored)

        # Restore ephemeral state (daily PnL, cooldowns, paused strategies)
        await self._restore_state()

        # Reconstruct LLM cost tracker from DB (survives restarts)
        from bot.data.activity import get_today_llm_cost

        today_cost = await get_today_llm_cost()
        if today_cost > 0:
            llm_cost_tracker.add(today_cost)
            logger.info(
                "llm_cost_restored", today_cost=round(today_cost, 4),
            )

        # Wire up deferred fill callbacks for live orders
        self.order_manager.set_on_fill_callback(self.closer.handle_order_fill)
        self.order_manager.set_on_sell_fill_callback(self.closer.handle_sell_fill)

        # Expire stale pending orders orphaned by previous container restarts
        await self._expire_stale_pending_orders()

        logger.info(
            "engine_initialized",
            equity=self.portfolio.total_equity,
            tier=self.portfolio.tier.value,
            positions=self.portfolio.open_position_count,
        )

    async def _expire_stale_pending_orders(self) -> None:
        """Expire pending orders orphaned by previous container restarts.

        The in-memory _pending_orders dict is lost on restart, leaving DB
        records stuck as 'pending' forever. This sweeps them on startup.
        """
        try:
            async with async_session() as session:
                from bot.data.repositories import TradeRepository

                repo = TradeRepository(session)
                count = await repo.expire_stale_pending(max_age_seconds=600)
                if count > 0:
                    logger.info("stale_pending_orders_expired", count=count)
        except Exception as e:
            logger.error("expire_stale_pending_failed", error=str(e))

    async def _restore_state(self) -> None:
        """Restore ephemeral state from DB after restart."""
        await self.risk_manager.restore_daily_pnl()

        # Sync portfolio realized PnL with risk_manager (both track the same value)
        self.portfolio.restore_realized_pnl(
            self.risk_manager._daily_pnl,
            self.risk_manager._daily_pnl_date,
        )

        # Restore day_start_equity so daily PnL survives restarts
        try:
            from bot.data.settings_store import StateStore

            equity, date = await StateStore.load_day_start_equity()
            if equity > 0:
                self.portfolio.restore_day_start_equity(equity, date)
            else:
                # First run or no persisted value — save current equity
                await StateStore.save_day_start_equity(
                    self.portfolio.day_start_equity, trading_day(),
                )
            self.risk_manager.set_day_start_equity(
                self.portfolio.day_start_equity,
            )
        except Exception as e:
            logger.error("restore_day_start_equity_failed", error=str(e))

        await self.learner.restore_paused_strategies()
        await self.learner.restore_unpause_immunity()

        # Restore market cooldowns
        try:
            from bot.data.settings_store import StateStore

            cooldowns = await StateStore.load_market_cooldowns()
            now = datetime.now(timezone.utc)
            for market_id, iso_str in cooldowns.items():
                expires = datetime.fromisoformat(iso_str)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires > now:
                    self._market_cooldown[market_id] = expires
            if self._market_cooldown:
                logger.info(
                    "market_cooldowns_restored",
                    count=len(self._market_cooldown),
                )
        except Exception as e:
            logger.error("restore_cooldowns_failed", error=str(e))

    async def _persist_state(self) -> None:
        """Persist ephemeral state to DB (called after trades)."""
        await self.risk_manager.persist_daily_pnl()
        await self.learner.persist_paused_strategies()
        await self.learner.persist_unpause_immunity()

        # Persist active cooldowns
        try:
            from bot.data.settings_store import StateStore

            now = datetime.now(timezone.utc)
            active = {
                mid: dt.isoformat()
                for mid, dt in self._market_cooldown.items()
                if dt > now
            }
            await StateStore.save_market_cooldowns(active)
        except Exception as e:
            logger.error("persist_cooldowns_failed", error=str(e))

    async def shutdown(self) -> None:
        """Clean shutdown."""
        self._running = False
        await self._persist_state()
        await self.research_engine.stop()
        await self.heartbeat.stop()
        await self.ws_manager.disconnect()
        await self.clob_client.close()
        await self.gamma_client.close()
        await self.data_api.close()
        await close_telegram_client()
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

        try:
            while self._running:
                try:
                    await self._trading_cycle()
                except Exception as e:
                    logger.error("trading_cycle_error", error=str(e), cycle=self._cycle_count)
                    await notify_error("trading_cycle", str(e))
                    await push_notify_error("trading_cycle", str(e))

                await asyncio.sleep(settings.scan_interval_seconds)
        finally:
            await self._persist_state()

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
        self.risk_manager.set_day_start_equity(self.portfolio.day_start_equity)

        # 1b. Subscribe open position tokens to WebSocket for real-time data
        for pos in self.portfolio.positions:
            if pos.token_id:
                await self.ws_manager.subscribe(pos.token_id)

        # 2. Update learner
        await self._update_learner()

        # 3. Check for exits on open positions
        await self._process_exits(tier)

        # 4-6. Scan, evaluate, and execute signals
        signals_found, signals_approved, orders_placed = (
            await self._evaluate_signals(tier)
        )

        # 7. Monitor pending orders
        await self.order_manager.monitor_orders()

        # 8. Persist state (daily PnL, cooldowns, paused strategies)
        if orders_placed > 0 or self._cycle_count % 10 == 0:
            await self._persist_state()

        # 9. Take periodic snapshot
        await self._maybe_snapshot()

        # 10. Update research priorities (open positions + recent signals)
        priority_ids = {p.market_id for p in self.portfolio.positions}
        self.research_engine.set_priority_markets(priority_ids)

        # 11. Daily summary
        await self._maybe_daily_summary()

        # 12. Daily market report (sent once at ~23:00 UTC)
        await self._maybe_daily_report()

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
        if self._cycle_count % 5 == 0 or orders_placed > 0:
            await log_cycle_summary(
                cycle=self._cycle_count,
                equity=self.portfolio.total_equity,
                signals_found=signals_found,
                signals_approved=signals_approved,
                orders_placed=orders_placed,
                pending_orders=self.order_manager.pending_count,
                urgency=_urgency,
                daily_progress=_progress,
            )

        # Prune old activity rows periodically (every 50 cycles)
        if self._cycle_count % 50 == 0:
            await prune_old_activity()

    async def _update_learner(self) -> None:
        """Update learner stats and apply adjustments to strategies."""
        # Use equity-based PnL (not inflated accumulated realized_pnl_today)
        equity_pnl = (
            self.portfolio.total_equity - self.portfolio.day_start_equity
        )
        self.learner.set_daily_context(
            realized_pnl=equity_pnl,
            equity=self.portfolio.day_start_equity,
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
            for s_name, s_wr, s_pnl in self.learner.consume_newly_paused():
                await log_strategy_paused(s_name, s_wr, s_pnl)
                await notify_strategy_paused(
                    s_name, f"Win rate {s_wr:.0%}, PnL ${s_pnl:+.2f}"
                )
                await push_notify_strategy_paused(
                    s_name, f"Win rate {s_wr:.0%}, PnL ${s_pnl:+.2f}"
                )

            # Check if daily target was reached (notify once per day)
            if self._learner_adjustments.urgency_multiplier < 1.0:
                day_key = trading_day()
                if self._target_notified_day != day_key:
                    self._target_notified_day = day_key
                    await log_daily_target_reached(
                        self.portfolio.total_equity,
                        self.portfolio.realized_pnl_today,
                        settings.daily_target_pct,
                    )
                    await notify_daily_target(
                        self.portfolio.total_equity,
                        self.portfolio.realized_pnl_today,
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

    async def _process_exits(self, tier) -> None:
        """Check and close positions that meet exit criteria."""
        exits = await self.analyzer.check_exits(self.portfolio.positions, tier)
        exited_ids = set()
        for market_id, exit_reason in exits:
            pos = next(
                (p for p in self.portfolio.positions if p.market_id == market_id),
                None,
            )
            if pos:
                await self.closer.close_position(pos, exit_reason=exit_reason)
                exited_ids.add(market_id)

        # LLM position reviewer (runs on positions not already exiting)
        if settings.use_llm_reviewer:
            await self._llm_review_positions(exited_ids)

    async def _llm_review_positions(self, already_exiting: set[str]) -> None:
        """Ask LLM to review open positions and recommend exits."""
        now = datetime.now(timezone.utc)
        for pos in self.portfolio.positions:
            if pos.market_id in already_exiting:
                continue

            created = getattr(pos, "created_at", None)
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = (
                (now - created).total_seconds() / 3600
                if created is not None else 0.0
            )

            # Only review positions older than 2 hours (save API calls)
            if age_hours < 2.0:
                continue

            # Only review every ~30 min (use cycle count as rough timer)
            # Position hash + cycle modulo to stagger reviews across cycles
            pos_hash = hash(pos.market_id) % 30
            if self._cycle_count % 30 != pos_hash:
                continue

            hours_res = None
            market = self.cache.get_market(pos.market_id)
            if market and market.end_date:
                end = market.end_date
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                hours_res = max(0, (end - now).total_seconds() / 3600)

            research = self.research_cache.get(pos.market_id)
            sentiment = (
                research.sentiment_score if research is not None else None
            )

            result = await review_position(
                question=pos.question,
                strategy=pos.strategy,
                entry_price=pos.avg_price,
                current_price=pos.current_price,
                size=pos.size,
                age_hours=age_hours,
                unrealized_pnl=pos.unrealized_pnl,
                hours_to_resolution=hours_res,
                sentiment_score=sentiment,
            )
            if result is not None:
                await log_llm_review(
                    market_id=pos.market_id,
                    question=pos.question,
                    strategy=pos.strategy,
                    verdict=result.verdict,
                    urgency=result.urgency,
                    reasoning=result.reasoning,
                    entry_price=pos.avg_price,
                    current_price=pos.current_price,
                    unrealized_pnl=pos.unrealized_pnl,
                    cost_usd=result.cost_usd,
                )
                if result.verdict == "EXIT":
                    logger.info(
                        "llm_reviewer_exit",
                        market_id=pos.market_id[:20],
                        urgency=result.urgency,
                        reasoning=result.reasoning[:80],
                    )
                    # Only act on HIGH urgency exits (MEDIUM = log only)
                    if result.urgency == "HIGH":
                        await self.closer.close_position(
                            pos, exit_reason=f"llm_review ({result.reasoning[:60]})",
                        )
                elif result.verdict == "REDUCE" and result.urgency in ("HIGH", "MEDIUM"):
                    # Partial exit: sell half the position
                    half_size = pos.size / 2
                    if half_size >= 5:  # Min 5 shares for CLOB
                        logger.info(
                            "llm_reviewer_reduce",
                            market_id=pos.market_id[:20],
                            half_size=round(half_size, 1),
                            reasoning=result.reasoning[:80],
                        )
                        await self.order_manager.close_position(
                            market_id=pos.market_id,
                            token_id=pos.token_id,
                            size=half_size,
                            current_price=pos.current_price,
                            question=pos.question,
                            outcome=pos.outcome,
                            category=pos.category,
                            strategy=pos.strategy,
                            entry_price=pos.avg_price,
                            exit_reason=f"llm_reduce ({result.reasoning[:60]})",
                        )
                elif result.verdict == "INCREASE":
                    # Log recommendation only — adding to positions
                    # requires full signal flow (risk checks, Kelly sizing)
                    logger.info(
                        "llm_reviewer_increase_recommended",
                        market_id=pos.market_id[:20],
                        urgency=result.urgency,
                        reasoning=result.reasoning[:80],
                    )

    async def _evaluate_signals(self, tier) -> tuple[int, int, int]:
        """Scan markets, evaluate signals, and execute approved trades.

        Returns (signals_found, signals_approved, orders_placed).
        """
        signals = await self.analyzer.scan_markets(tier)

        cycle_committed = 0.0
        pending_count = self.order_manager.pending_count
        pending_markets = self.order_manager.pending_market_ids
        signals_approved = 0
        orders_placed = 0

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

            # Skip markets in cooldown (prevents rapid same-market churning)
            cooldown_until = self._market_cooldown.get(signal.market_id)
            if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                logger.info(
                    "signal_skipped_market_cooldown",
                    market_id=signal.market_id[:20],
                    strategy=signal.strategy,
                )
                await log_signal_rejected(
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    question=signal.question,
                    reason="Market in cooldown (1h after last trade)",
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

            # Cross-market correlation check: skip if correlated with open position
            corr_detector = self.research_engine.correlation_detector
            corr_group = corr_detector.get_group(signal.market_id)
            if corr_group is not None:
                correlated_skip = False
                for pos in self.portfolio.positions:
                    if corr_detector.are_correlated(signal.market_id, pos.market_id):
                        logger.info(
                            "signal_skipped_correlated",
                            market_id=signal.market_id[:20],
                            correlated_with=pos.market_id[:16],
                            strategy=signal.strategy,
                        )
                        await log_signal_rejected(
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                            question=signal.question,
                            reason=f"Correlated with open position {pos.market_id[:16]}",
                            edge=signal.edge,
                            price=signal.market_price,
                        )
                        correlated_skip = True
                        break
                if correlated_skip:
                    continue

            # LLM debate gate: Proposer vs Challenger
            if settings.use_llm_debate:
                research = self.research_cache.get(signal.market_id)
                sentiment = (
                    research.sentiment_score if research is not None else None
                )
                hours_res = signal.metadata.get("hours_to_resolution")
                res_condition = (
                    research.resolution_condition if research is not None else ""
                )
                res_source = (
                    research.resolution_source if research is not None else ""
                )

                # Add volume anomaly flag to signal metadata
                if research is not None and research.is_volume_anomaly:
                    signal.metadata["is_volume_anomaly"] = True

                whale_flag = (
                    research.whale_activity
                    if research is not None
                    else False
                )

                # Format whale summary from WhaleDetector if available
                whale_summary_text = ""
                whale_det = getattr(
                    self.ws_manager, "whale_detector", None,
                )
                if (
                    whale_det is not None
                    and hasattr(whale_det, "get_whale_summary")
                    and signal.token_id
                    and isinstance(signal.token_id, str)
                ):
                    try:
                        ws_summary = whale_det.get_whale_summary(
                            signal.token_id,
                        )
                        if (
                            isinstance(ws_summary, dict)
                            and "count" in ws_summary
                        ):
                            whale_summary_text = (
                                f"{ws_summary['count']} whale orders, "
                                f"${ws_summary['total_usd']:,.0f} total, "
                                f"net bias: {ws_summary['net_side']}"
                            )
                    except (TypeError, KeyError):
                        pass

                debate_result = await debate_signal(
                    question=signal.question,
                    strategy=signal.strategy,
                    edge=signal.edge,
                    price=signal.market_price,
                    estimated_prob=signal.estimated_prob,
                    confidence=signal.confidence,
                    reasoning=signal.reasoning,
                    sentiment_score=sentiment,
                    hours_to_resolution=hours_res,
                    resolution_condition=res_condition,
                    resolution_source=res_source,
                    whale_activity=whale_flag,
                    whale_summary=whale_summary_text,
                )
                if debate_result is not None:
                    debate_meta = {
                        "proposer": debate_result.proposer_verdict,
                        "proposer_confidence": debate_result.proposer_confidence,
                        "proposer_reasoning": debate_result.proposer_reasoning,
                        "edge_valid": debate_result.edge_valid,
                        "challenger": debate_result.challenger_verdict,
                        "challenger_risk": debate_result.challenger_risk,
                        "challenger_objections": debate_result.challenger_objections,
                        "cost_usd": debate_result.total_cost_usd,
                    }
                    if debate_result.counter_rebuttal:
                        debate_meta = {
                            **debate_meta,
                            "counter_rebuttal": debate_result.counter_rebuttal,
                            "counter_conviction": debate_result.counter_conviction,
                            "final_verdict": debate_result.final_verdict,
                            "final_reasoning": debate_result.final_reasoning,
                        }
                    signal.metadata["llm_debate"] = debate_meta
                    await log_llm_debate(
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        question=signal.question,
                        approved=debate_result.approved,
                        proposer_verdict=debate_result.proposer_verdict,
                        proposer_confidence=debate_result.proposer_confidence,
                        proposer_reasoning=debate_result.proposer_reasoning,
                        challenger_verdict=debate_result.challenger_verdict,
                        challenger_risk=debate_result.challenger_risk,
                        challenger_objections=debate_result.challenger_objections,
                        edge=signal.edge,
                        price=signal.market_price,
                        cost_usd=debate_result.total_cost_usd,
                        counter_rebuttal=debate_result.counter_rebuttal,
                        counter_conviction=debate_result.counter_conviction,
                        final_verdict=debate_result.final_verdict,
                        final_reasoning=debate_result.final_reasoning,
                    )
                    if not debate_result.approved:
                        logger.info(
                            "signal_rejected_llm_debate",
                            strategy=signal.strategy,
                            market_id=signal.market_id[:20],
                            proposer=debate_result.proposer_verdict,
                            challenger=debate_result.challenger_verdict,
                        )
                        await log_signal_rejected(
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                            question=signal.question,
                            reason=(
                                f"LLM debate rejected: "
                                f"P={debate_result.proposer_verdict}, "
                                f"C={debate_result.challenger_verdict} "
                                f"({debate_result.challenger_objections[:80]})"
                            ),
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

            # Apply category-specific min edge from learner (dynamic calibration)
            if self._learner_adjustments and category:
                cat_min_edge = (
                    self._learner_adjustments.category_min_edges.get(category)
                )
                if cat_min_edge is not None:
                    # Scale edge_multiplier so that base_min_edge * multiplier
                    # is at least category_min_edge
                    base_min = TierConfig.get(tier).get("min_edge_pct", 0.01)
                    if base_min > 0:
                        required_mult = cat_min_edge / base_min
                        if required_mult > edge_multiplier:
                            edge_multiplier = required_mult

            # Apply research sentiment multiplier (news-driven edge adjustment)
            research = self.research_cache.get(signal.market_id)
            if research is not None:
                r_mult = max(0.7, min(1.3, research.research_multiplier))
                edge_multiplier *= r_mult
                edge_multiplier = max(0.5, min(2.0, edge_multiplier))
                signal.metadata["research_sentiment"] = research.sentiment_score
                signal.metadata["research_multiplier"] = r_mult

                # Wire historical base rate into confidence scoring
                br_raw = getattr(research, "historical_base_rate", 0.0)
                if isinstance(br_raw, (int, float)) and br_raw > 0:
                    # If pattern analyzer found similar past trades,
                    # blend the base rate with signal confidence.
                    # High base rate (>60%) → boost confidence up to +10%
                    # Low base rate (<40%) → penalize confidence up to -10%
                    br_adjustment = (br_raw - 0.5) * 0.2  # maps [0,1] → [-0.1, +0.1]
                    signal.confidence = max(
                        0.3, min(0.95, signal.confidence + br_adjustment),
                    )
                    signal.metadata["historical_base_rate"] = br_raw

            # Calibrate estimated probability using historical accuracy
            if self.learner.calibrator.is_trained:
                calibrated = self.learner.calibrator.calibrate(
                    signal.estimated_prob,
                )
                signal.metadata["calibrated_prob"] = calibrated

            _urgency = (
                self._learner_adjustments.urgency_multiplier
                if self._learner_adjustments
                else 1.0
            )

            _calibration = (
                self._learner_adjustments.calibration
                if self._learner_adjustments
                else None
            )

            approved, size, reason = await self.risk_manager.evaluate_signal(
                signal=signal,
                bankroll=effective_bankroll,
                open_positions=self.portfolio.positions,
                tier=tier,
                pending_count=pending_count,
                edge_multiplier=edge_multiplier,
                urgency=_urgency,
                calibration=_calibration,
            )

            if not approved:
                # Try rebalancing: close weakest loser to make room
                rebalance_result = None
                if (
                    ("Max positions" in reason or "Max deployed" in reason)
                    and not self._rebalanced_this_cycle
                ):
                    rebalance_result = await self.closer.try_rebalance(
                        signal, self.portfolio.positions, urgency=_urgency
                    )
                    # Mark attempted even if it fails, to avoid retrying
                    # multiple times in the same cycle
                    self._rebalanced_this_cycle = True

                if rebalance_result is not None:
                    closed_pos, rebal_trade = rebalance_result

                    if rebal_trade.status == "filled":
                        # Paper mode or instantly matched — record PnL now
                        pnl = await self.portfolio.record_trade_close(
                            closed_pos.market_id, closed_pos.current_price
                        )
                        self.risk_manager.update_daily_pnl(pnl)

                        from bot.data.repositories import TradeRepository

                        async with async_session() as session:
                            repo = TradeRepository(session)
                            await repo.close_trade_for_position(
                                closed_pos.market_id, pnl, "rebalance",
                            )

                        await log_position_closed(
                            market_id=closed_pos.market_id,
                            question=closed_pos.question,
                            strategy=closed_pos.strategy,
                            pnl=pnl,
                            exit_reason="rebalance",
                        )
                    else:
                        # Live pending — handle_sell_fill will record PnL on fill
                        logger.info(
                            "rebalance_sell_pending",
                            market_id=closed_pos.market_id,
                            strategy=closed_pos.strategy,
                        )

                    # Brief pause for CLOB balance settlement after sell
                    if not settings.is_paper:
                        await asyncio.sleep(3)

                    # Re-evaluate with updated positions (one slot freed)
                    approved, size, reason = await self.risk_manager.evaluate_signal(
                        signal=signal,
                        bankroll=self.portfolio.total_equity - cycle_committed,
                        open_positions=self.portfolio.positions,
                        tier=tier,
                        pending_count=pending_count,
                        edge_multiplier=edge_multiplier,
                        urgency=_urgency,
                        calibration=_calibration,
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
                else:
                    # Try LLM risk debate on debatable rejections
                    risk_override = False
                    if settings.use_llm_debate and reason:
                        risk_result = await debate_risk_rejection(
                            question=signal.question,
                            strategy=signal.strategy,
                            rejection_reason=reason,
                            edge=signal.edge,
                            price=signal.market_price,
                            estimated_prob=signal.estimated_prob,
                            size_usd=size,
                            hours_to_resolution=signal.metadata.get(
                                "hours_to_resolution",
                            ),
                        )
                        if risk_result is not None:
                            await log_risk_debate(
                                strategy=signal.strategy,
                                market_id=signal.market_id,
                                question=signal.question,
                                rejection_reason=risk_result.rejection_reason,
                                override=risk_result.override,
                                proposer_rebuttal=risk_result.proposer_rebuttal,
                                analyst_verdict=risk_result.analyst_verdict,
                                analyst_reasoning=risk_result.analyst_reasoning,
                                adjusted_size_pct=risk_result.adjusted_size_pct,
                                edge=signal.edge,
                                price=signal.market_price,
                                cost_usd=risk_result.total_cost_usd,
                            )
                            if risk_result.override:
                                # Apply size adjustment: Kelly * adjusted %
                                adjusted_size = size * risk_result.adjusted_size_pct
                                min_notional = max(1.0, 5.0 * signal.market_price)
                                available = (
                                    self.portfolio.total_equity
                                    - cycle_committed
                                ) * 0.95
                                adjusted_size = max(
                                    min_notional,
                                    min(adjusted_size, available),
                                )
                                # Re-validate hard limits with adjusted size
                                re_ok, re_size, re_reason = (
                                    await self.risk_manager.evaluate_signal(
                                        signal=signal,
                                        bankroll=self.portfolio.total_equity
                                        - cycle_committed,
                                        open_positions=self.portfolio.positions,
                                        tier=tier,
                                        pending_count=pending_count,
                                        edge_multiplier=edge_multiplier,
                                        urgency=_urgency,
                                    )
                                )
                                if re_ok:
                                    size = min(adjusted_size, re_size)
                                    approved = True
                                    risk_override = True
                                    logger.info(
                                        "risk_debate_override",
                                        strategy=signal.strategy,
                                        question=signal.question[:60],
                                        original_reason=reason,
                                        adjusted_size=round(size, 2),
                                        size_pct=risk_result.adjusted_size_pct,
                                    )
                                else:
                                    logger.info(
                                        "risk_debate_override_blocked",
                                        strategy=signal.strategy,
                                        reason=re_reason,
                                    )

                    if not risk_override:
                        logger.debug(
                            "signal_rejected",
                            strategy=signal.strategy,
                            reason=reason,
                        )
                        await log_signal_rejected(
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                            question=signal.question,
                            reason=reason,
                            edge=signal.edge,
                            price=signal.market_price,
                        )
                        await self._maybe_notify_risk_limit(reason)
                        continue

            # Check order book liquidity before executing
            if not await self._check_liquidity(signal):
                continue

            signals_approved += 1
            signal.size_usd = size

            # Execute trade
            trade = await self.order_manager.execute_signal(signal)
            if trade and trade.status == "filled":
                orders_placed += 1
                await self._mark_scan_traded(signal)
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
                await event_bus.emit(
                    "trade_filled",
                    trade_event="buy_filled",
                    market_id=signal.market_id,
                    question=signal.question,
                    strategy=signal.strategy,
                    side="BUY",
                    price=trade.price,
                    size=trade.size,
                )
                cooldown_hours = self.market_cooldown_hours
                self._market_cooldown[signal.market_id] = (
                    datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                )
            elif trade:
                orders_placed += 1
                await self._mark_scan_traded(signal)
                cycle_committed += trade.cost_usd
                pending_count += 1
                pending_markets.add(signal.market_id)
                cooldown_hours = self.market_cooldown_hours
                self._market_cooldown[signal.market_id] = (
                    datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                )
                logger.info(
                    "order_pending",
                    trade_id=trade.id,
                    market_id=signal.market_id,
                    status=trade.status,
                    cycle_committed=cycle_committed,
                )

        return len(signals), signals_approved, orders_placed

    async def _maybe_notify_risk_limit(self, reason: str) -> None:
        """Send a one-time daily notification when a risk limit is breached."""
        day_key = trading_day()

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
                daily_loss_pct = (
                    abs(self.risk_manager._daily_pnl / self.portfolio.total_equity)
                    if self.portfolio.total_equity > 0 else 0.0
                )
                limit_pct = config["daily_loss_limit_pct"]
                await notify_risk_limit("daily_loss", daily_loss_pct, limit_pct)
                await push_notify_risk_limit("daily_loss", daily_loss_pct, limit_pct)
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
                await push_notify_risk_limit(
                    "max_drawdown",
                    config["current_drawdown_pct"],
                    config["max_drawdown_limit_pct"],
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
        """Send daily summary at local midnight."""
        now = datetime.now(timezone.utc)
        today = trading_day()
        if self._last_daily_summary == today:
            return
        local_hour = (now.hour + settings.timezone_offset_hours) % 24
        if local_hour == 0 and now.minute < 2:
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
            day_start = overview.get("day_start_equity", equity)
            daily_pnl = equity - day_start  # Equity-based (not inflated)
            daily_return = daily_pnl / day_start if day_start > 0 else 0.0

            await notify_daily_summary(
                equity=equity,
                daily_pnl=daily_pnl,
                daily_return=daily_return,
                trades=today_stats["trades_today"],
                win_rate=today_stats["win_rate_today"],
            )
            await push_notify_daily_summary(
                equity=equity,
                daily_pnl=daily_pnl,
                daily_return=daily_return,
                trades=today_stats["trades_today"],
                win_rate=today_stats["win_rate_today"],
            )

    async def _maybe_daily_report(self) -> None:
        """Send daily market report via Telegram at ~23:00 UTC."""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if self._last_report_date == today:
            return
        if now.hour < 23:
            return

        self._last_report_date = today
        try:
            report = await generate_daily_report(
                research_cache=self.research_cache,
                portfolio=self.portfolio,
                learner=self.learner,
                research_engine=self.research_engine,
            )
            await notify_market_report(report)
            logger.info("daily_report_sent")
        except Exception as e:
            logger.error("daily_report_failed", error=str(e))

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

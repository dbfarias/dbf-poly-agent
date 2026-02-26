"""WatcherManager — lifecycle management for Trade Watcher agents."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, text

from bot.data.database import async_session
from bot.data.models import Watcher, WatcherDecision
from bot.polymarket.types import OrderSide, TradeSignal

if TYPE_CHECKING:
    from bot.agent.watcher import TradeWatcher

logger = structlog.get_logger()

MAX_WATCHERS = 5
MAX_WATCHER_EQUITY_PCT = 0.50  # 50% of total equity


class WatcherManager:
    """Lifecycle management for Trade Watcher agents."""

    def __init__(self, engine: object | None = None):
        self._engine = engine
        self._tasks: dict[int, asyncio.Task] = {}  # watcher_id -> task
        self._watchers: dict[int, Watcher] = {}  # watcher_id -> model
        self._trade_watchers: dict[int, TradeWatcher] = {}  # watcher_id -> TradeWatcher

    @property
    def active_watchers(self) -> list[Watcher]:
        return [w for w in self._watchers.values() if w.status == "active"]

    @property
    def active_count(self) -> int:
        return len(self.active_watchers)

    async def create_watcher(
        self,
        market_id: str,
        token_id: str,
        question: str,
        outcome: str,
        keywords: list[str],
        thesis: str,
        current_price: float,
        current_exposure: float = 0.0,
        source_strategy: str = "",
        auto_created: bool = False,
        max_exposure_usd: float = 20.0,
        stop_loss_pct: float = 0.25,
        max_age_hours: float = 168.0,
        end_date: datetime | None = None,
        event_slug: str = "",
    ) -> Watcher | None:
        """Create a new watcher. Returns None if rejected."""
        if self.active_count >= MAX_WATCHERS:
            logger.warning("watcher_rejected_max_count", count=self.active_count)
            return None

        # Check duplicate
        for w in self.active_watchers:
            if w.market_id == market_id:
                logger.info("watcher_rejected_duplicate", market_id=market_id)
                return None

        # Create DB record
        watcher = Watcher(
            market_id=market_id,
            token_id=token_id,
            question=question[:200],
            outcome=outcome,
            keywords=json.dumps(keywords),
            thesis=thesis[:500],
            max_exposure_usd=max_exposure_usd,
            stop_loss_pct=stop_loss_pct,
            max_age_hours=max_age_hours,
            status="active",
            current_exposure=current_exposure,
            avg_entry_price=current_price,
            highest_price=current_price,
            source_strategy=source_strategy,
            auto_created=auto_created,
            end_date=end_date,
            event_slug=event_slug,
        )

        async with async_session() as session:
            session.add(watcher)
            await session.commit()
            await session.refresh(watcher)

        self._watchers[watcher.id] = watcher
        logger.info(
            "watcher_created",
            watcher_id=watcher.id,
            market_id=market_id,
            question=question[:50],
            event_slug=event_slug or "(none)",
        )

        self._spawn_task(watcher)
        return watcher

    async def kill_watcher(self, watcher_id: int, reason: str = "manual") -> bool:
        """Kill a watcher and cancel its task."""
        watcher = self._watchers.get(watcher_id)
        if not watcher or watcher.status != "active":
            return False

        # Cancel task if running
        task = self._tasks.pop(watcher_id, None)
        if task and not task.done():
            task.cancel()

        self._trade_watchers.pop(watcher_id, None)

        # Update DB
        async with async_session() as session:
            watcher.status = "killed"
            watcher.updated_at = datetime.now(timezone.utc)
            await session.merge(watcher)
            await session.commit()

            # Log decision
            decision = WatcherDecision(
                watcher_id=watcher_id,
                decision="exit",
                reasoning=f"Killed: {reason}",
                action_taken="killed",
                price_at_decision=watcher.highest_price,
            )
            session.add(decision)
            await session.commit()

        logger.info("watcher_killed", watcher_id=watcher_id, reason=reason)
        return True

    async def process_pending_actions(self) -> None:
        """Check all active watchers for pending actions and execute them."""
        for watcher_id, tw in list(self._trade_watchers.items()):
            watcher = self._watchers.get(watcher_id)
            if watcher is None or watcher.status != "active":
                continue

            if tw.pending_scale_level is not None:
                await self._execute_level_scale(tw, watcher)

            if tw.pending_scale_up is not None:
                await self._execute_scale_up(tw, watcher)

            if tw.pending_exit is not None:
                await self._execute_exit(tw, watcher)

    async def _execute_level_scale(
        self, tw: TradeWatcher, watcher: Watcher
    ) -> None:
        """Execute a level-scale trade: sell current level, buy new level."""
        req = tw.pending_scale_level
        if req is None:
            return

        tw.clear_pending_scale_level()

        closer = getattr(self._engine, "position_closer", None)
        portfolio = getattr(self._engine, "portfolio", None)
        order_mgr = getattr(self._engine, "order_manager", None)
        if closer is None or portfolio is None or order_mgr is None:
            logger.warning("watcher_level_scale_no_deps", watcher_id=watcher.id)
            return

        # Step 1: Sell current position
        sold = await self._sell_current_level(closer, portfolio, req, watcher)
        if not sold:
            return

        # Step 2: Buy the new level
        filled = await self._buy_new_level(order_mgr, req, watcher)

        # Step 3: Update watcher to track the new market
        if filled:
            self._update_watcher_for_new_level(watcher, req, filled)

        await self._log_action(
            watcher.id,
            f"scale_level_{req.direction}",
            "filled" if filled else "partial_sell_only",
            req.reasoning,
        )

    async def _sell_current_level(
        self, closer, portfolio, req, watcher: Watcher
    ) -> bool:
        """Sell the current position for a level scale. Returns success."""
        pos = None
        for p in portfolio.positions:
            if p.market_id == req.sell_market_id and p.is_open:
                pos = p
                break

        if pos is None:
            logger.info(
                "watcher_level_scale_no_position",
                watcher_id=watcher.id,
                market_id=req.sell_market_id,
            )
            return False

        await closer.close_position(
            pos, exit_reason=f"watcher level scale {req.direction}"
        )
        logger.info(
            "watcher_level_scale_sold",
            watcher_id=watcher.id,
            market_id=req.sell_market_id,
        )
        return True

    async def _buy_new_level(self, order_mgr, req, watcher: Watcher):
        """Buy the new level market. Returns trade or None."""
        size_usd = min(watcher.max_exposure_usd, 5.0)
        if size_usd < 1.0:
            return None

        signal = TradeSignal(
            strategy="watcher",
            market_id=req.buy_market_id,
            token_id=req.buy_token_id,
            question=req.buy_question,
            side=OrderSide.BUY,
            outcome=req.buy_outcome,
            estimated_prob=req.buy_price + 0.05,
            market_price=req.buy_price,
            edge=0.03,
            size_usd=size_usd,
            confidence=0.7,
            reasoning=f"Watcher level scale: {req.reasoning[:200]}",
            metadata={"watcher_id": watcher.id},
        )

        trade = await order_mgr.execute_signal(signal)
        if trade and trade.status == "filled":
            return trade
        return None

    def _update_watcher_for_new_level(
        self, watcher: Watcher, req, trade
    ) -> None:
        """Update watcher state to track the new price level."""
        watcher.market_id = req.buy_market_id
        watcher.token_id = req.buy_token_id
        watcher.question = req.buy_question[:200]
        watcher.outcome = req.buy_outcome
        watcher.avg_entry_price = trade.price
        watcher.current_exposure = trade.cost_usd
        watcher.highest_price = trade.price
        watcher.current_price = trade.price
        watcher.scale_count += 1

    async def _execute_scale_up(self, tw: TradeWatcher, watcher: Watcher) -> None:
        """Execute a scale-up trade for a watcher."""
        req = tw.pending_scale_up
        if req is None:
            return

        tw.clear_pending_scale_up()

        order_mgr = getattr(self._engine, "order_manager", None)
        if order_mgr is None:
            logger.warning("watcher_no_order_manager", watcher_id=watcher.id)
            return

        # Calculate size: remaining exposure budget, capped at $5 per scale
        remaining = watcher.max_exposure_usd - watcher.current_exposure
        size_usd = min(remaining, 5.0)
        if size_usd < 1.0:
            logger.info(
                "watcher_scale_up_too_small",
                watcher_id=watcher.id,
                remaining=remaining,
            )
            return

        signal = TradeSignal(
            strategy="watcher",
            market_id=req.market_id,
            token_id=req.token_id,
            question=req.question,
            side=OrderSide.BUY,
            outcome=req.outcome,
            estimated_prob=req.current_price + 0.05,
            market_price=req.current_price,
            edge=0.03,
            size_usd=size_usd,
            confidence=req.confidence,
            reasoning=f"Watcher scale-up: {req.reasoning[:200]}",
            metadata={"watcher_id": watcher.id},
        )

        trade = await order_mgr.execute_signal(signal)
        action_taken = "placed_order" if trade else "blocked_by_risk"

        if trade and trade.status == "filled":
            action_taken = "filled"
            self._update_watcher_after_scale(watcher, trade.price, trade.cost_usd)

        await self._log_action(watcher.id, "scale_up", action_taken, req.reasoning, size_usd)

        logger.info(
            "watcher_scale_up_result",
            watcher_id=watcher.id,
            action_taken=action_taken,
            size_usd=round(size_usd, 2),
        )

    def _update_watcher_after_scale(
        self, watcher: Watcher, fill_price: float, cost_usd: float
    ) -> None:
        """Update watcher state after a successful scale-up fill."""
        old_cost = watcher.current_exposure
        new_cost = old_cost + cost_usd
        if new_cost > 0:
            watcher.avg_entry_price = (
                (old_cost * watcher.avg_entry_price) + (cost_usd * fill_price)
            ) / new_cost
        watcher.current_exposure = new_cost
        watcher.scale_count += 1

    async def _execute_exit(self, tw: TradeWatcher, watcher: Watcher) -> None:
        """Execute an exit for a watcher's position."""
        req = tw.pending_exit
        if req is None:
            return

        tw.clear_pending_exit()

        closer = getattr(self._engine, "position_closer", None)
        portfolio = getattr(self._engine, "portfolio", None)
        if closer is None or portfolio is None:
            logger.warning("watcher_no_closer", watcher_id=watcher.id)
            return

        # Find the open position for this market
        pos = None
        for p in portfolio.positions:
            if p.market_id == req.market_id and p.is_open:
                pos = p
                break

        if pos is None:
            logger.info(
                "watcher_exit_no_position",
                watcher_id=watcher.id,
                market_id=req.market_id,
            )
            # No position to close — mark watcher as completed
            await self._complete_watcher(watcher, req.reasoning)
            return

        await closer.close_position(pos, exit_reason=f"watcher: {req.reasoning[:50]}")
        await self._complete_watcher(watcher, req.reasoning)

    async def _complete_watcher(self, watcher: Watcher, reason: str) -> None:
        """Mark watcher as completed and clean up."""
        watcher.status = "completed"
        watcher.updated_at = datetime.now(timezone.utc)

        async with async_session() as session:
            await session.merge(watcher)
            await session.commit()

        await self._log_action(watcher.id, "exit", "exited", reason)

        # Cancel the task
        task = self._tasks.pop(watcher.id, None)
        if task and not task.done():
            task.cancel()
        self._trade_watchers.pop(watcher.id, None)

        logger.info("watcher_completed", watcher_id=watcher.id, reason=reason[:80])

    async def _log_action(
        self,
        watcher_id: int,
        decision: str,
        action_taken: str,
        reasoning: str,
        size_usd: float = 0.0,
    ) -> None:
        """Log a watcher action decision to the database."""
        watcher = self._watchers.get(watcher_id)
        price = watcher.highest_price if watcher else 0.0

        record = WatcherDecision(
            watcher_id=watcher_id,
            decision=decision,
            reasoning=reasoning[:500],
            action_taken=action_taken,
            size_usd=size_usd,
            price_at_decision=price,
        )
        async with async_session() as session:
            session.add(record)
            await session.commit()

    async def get_watcher(self, watcher_id: int) -> Watcher | None:
        return self._watchers.get(watcher_id)

    async def get_all_watchers(self) -> list[Watcher]:
        """Get all watchers (active + completed + killed)."""
        async with async_session() as session:
            result = await session.execute(
                select(Watcher).order_by(Watcher.created_at.desc()).limit(50)
            )
            return list(result.scalars().all())

    async def get_decisions(
        self, watcher_id: int, limit: int = 20
    ) -> list[WatcherDecision]:
        async with async_session() as session:
            result = await session.execute(
                select(WatcherDecision)
                .where(WatcherDecision.watcher_id == watcher_id)
                .order_by(WatcherDecision.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def restore_from_db(self) -> None:
        """Restore active watchers from DB after restart."""
        await self._migrate_event_slug_column()

        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Watcher).where(Watcher.status == "active")
                )
                watchers = list(result.scalars().all())
        except Exception as e:
            logger.warning("watcher_restore_failed", error=str(e))
            return

        for w in watchers:
            self._watchers[w.id] = w
            self._spawn_task(w)

        if watchers:
            logger.info("watchers_restored", count=len(watchers))

    async def _migrate_event_slug_column(self) -> None:
        """Ensure event_slug column exists (migration for existing DBs)."""
        try:
            async with async_session() as session:
                await session.execute(
                    text(
                        "ALTER TABLE watchers "
                        "ADD COLUMN event_slug VARCHAR(256) DEFAULT ''"
                    )
                )
                await session.commit()
                logger.info("watcher_migration_event_slug_added")
        except Exception:
            pass  # Column already exists

    def _spawn_task(self, watcher: Watcher) -> None:
        """Spawn an asyncio task for a TradeWatcher."""
        from bot.agent.watcher import TradeWatcher

        price_tracker = getattr(self._engine, "price_tracker", None)
        research_engine = getattr(self._engine, "research_engine", None)
        news_fetcher = (
            getattr(research_engine, "news_fetcher", None)
            if research_engine
            else None
        )
        data_api = getattr(self._engine, "data_api", None)

        tw = TradeWatcher(
            watcher=watcher,
            price_tracker=price_tracker,
            news_fetcher=news_fetcher,
            data_api=data_api,
        )
        self._trade_watchers[watcher.id] = tw
        task = asyncio.create_task(tw.run(), name=f"watcher-{watcher.id}")
        self._tasks[watcher.id] = task

    async def shutdown(self) -> None:
        """Cancel all running watcher tasks."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._trade_watchers.clear()
        logger.info("watchers_shutdown")

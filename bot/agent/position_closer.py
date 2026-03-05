"""Extracted position closing logic — handles exits, sell fills, and rebalancing."""

from datetime import datetime, timezone

import structlog

from bot.agent.events import event_bus
from bot.config import settings
from bot.data.activity import log_exit_triggered, log_position_closed, log_rebalance
from bot.data.database import async_session
from bot.data.market_cache import MarketCache

logger = structlog.get_logger()


class PositionCloser:
    """Encapsulates all position closing and fill-handling logic.

    Separated from TradingEngine to reduce engine.py size and improve cohesion.
    """

    def __init__(self, order_manager, portfolio, risk_manager, cache: MarketCache | None = None):
        self.order_manager = order_manager
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.cache = cache
        # Rebalance params (configurable via admin)
        self.min_rebalance_edge = 0.015  # 1.5% edge minimum
        self.min_hold_seconds = 120  # 2 min default (fallback)
        # Per-strategy hold overrides: strategy_name → seconds
        # Populated from strategy.MIN_HOLD_SECONDS by engine at init
        self.strategy_min_hold: dict[str, int] = {}
        # Near-resolution protection: skip rebalance if market resolves
        # within this many hours (unless loss is severe)
        self.rebalance_resolution_shield_hours = 24.0
        self.rebalance_resolution_max_loss_pct = 0.15  # 15% loss overrides shield

    async def close_position(self, pos, *, exit_reason: str = "strategy_exit") -> None:
        """Close a position and record PnL if immediately filled.

        In paper mode, the sell is always "filled" so we record immediately.
        In live mode, the sell may be "pending" on the CLOB — PnL recording
        is deferred to handle_sell_fill() callback when the order confirms.
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
            entry_price=pos.avg_price,
        )
        if trade is None:
            return

        if trade.status == "filled":
            pnl = await self.portfolio.record_trade_close(pos.market_id, pos.current_price)
            self.risk_manager.update_daily_pnl(pnl)

            from bot.data.repositories import TradeRepository

            try:
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(
                        trade.id, "filled", pnl=pnl, filled_size=pos.size,
                    )
                    await repo.close_trade_for_position(
                        pos.market_id, pnl, exit_reason,
                    )
            except Exception as e:
                logger.error("close_position_db_error", error=str(e))

            await log_position_closed(
                market_id=pos.market_id,
                question=pos.question,
                strategy=pos.strategy,
                pnl=pnl,
                exit_reason=exit_reason,
            )
            await event_bus.emit(
                "trade_filled",
                trade_event="sell_filled",
                market_id=pos.market_id,
                question=pos.question,
                strategy=pos.strategy,
                side="SELL",
                price=pos.current_price,
                size=pos.size,
                pnl=pnl,
            )
        else:
            logger.info(
                "sell_pending_on_clob",
                market_id=pos.market_id,
                strategy=pos.strategy,
            )

    async def handle_sell_fill(
        self,
        market_id: str,
        sell_price: float,
        trade_id: int,
        shares: float,
        strategy: str = "",
        question: str = "",
    ) -> None:
        """Callback when a pending live SELL order is confirmed filled."""
        logger.info(
            "deferred_sell_fill",
            market_id=market_id,
            sell_price=sell_price,
            trade_id=trade_id,
            strategy=strategy,
        )
        pnl = await self.portfolio.record_trade_close(market_id, sell_price)
        self.risk_manager.update_daily_pnl(pnl)

        from bot.data.repositories import TradeRepository

        try:
            async with async_session() as session:
                repo = TradeRepository(session)
                await repo.update_status(trade_id, "filled", pnl=pnl)
                await repo.close_trade_for_position(
                    market_id, pnl, "deferred_sell_fill",
                )
        except Exception as e:
            logger.error("sell_fill_db_error", error=str(e))

        await log_position_closed(
            market_id=market_id,
            question=question,
            strategy=strategy,
            pnl=pnl,
            exit_reason="deferred_sell_fill",
        )
        await event_bus.emit(
            "trade_filled",
            trade_event="sell_filled",
            market_id=market_id,
            question=question,
            strategy=strategy,
            side="SELL",
            price=sell_price,
            size=shares,
            pnl=pnl,
        )

    async def handle_order_fill(self, signal, shares: float, actual_price: float) -> None:
        """Callback when a pending live BUY order is confirmed filled.

        Uses actual_price (the CLOB fill price) instead of signal.market_price
        to ensure accurate avg_price, cost_basis, and downstream PnL calculations.
        """
        logger.info(
            "deferred_fill_creating_position",
            market_id=signal.market_id,
            strategy=signal.strategy,
            shares=shares,
            actual_price=actual_price,
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
            price=actual_price,
        )
        await event_bus.emit(
            "trade_filled",
            trade_event="buy_filled",
            market_id=signal.market_id,
            question=signal.question,
            strategy=signal.strategy,
            side="BUY",
            price=actual_price,
            size=shares,
        )

    async def try_rebalance(self, signal, positions, *, urgency: float = 1.0):
        """Close the weakest losing position to make room for a better signal.

        Returns (closed_position, close_trade) if successful, None otherwise.
        The caller should check close_trade.status to decide whether to record
        PnL immediately (filled) or defer to handle_sell_fill (pending).

        When urgency > 1.0 (behind daily target), the minimum edge threshold is
        lowered proportionally to allow more aggressive capital rotation.
        """
        min_sell_notional = 1.0  # Match CLOB minimum notional

        effective_min_edge = self.min_rebalance_edge / max(urgency, 1.0)
        if signal.edge < effective_min_edge:
            return None

        candidates = []
        now = datetime.now(timezone.utc)
        for pos in positions:
            if not settings.is_paper and pos.size * pos.current_price < min_sell_notional:
                continue
            created = pos.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            hold_limit = self.strategy_min_hold.get(pos.strategy, self.min_hold_seconds)
            if created and (now - created).total_seconds() < hold_limit:
                continue
            if pos.unrealized_pnl > 0:
                continue
            pnl_pct = (
                (pos.current_price - pos.avg_price) / pos.avg_price
                if pos.avg_price > 0 else 0.0
            )
            # Near-resolution protection: skip if market resolves soon
            # unless loss is severe enough to override
            if self.cache and abs(pnl_pct) < self.rebalance_resolution_max_loss_pct:
                market = self.cache.get_market(pos.market_id)
                if market is not None:
                    end = market.end_date
                    if end is not None:
                        if end.tzinfo is None:
                            end = end.replace(tzinfo=timezone.utc)
                        hours_left = (end - now).total_seconds() / 3600
                        if hours_left <= self.rebalance_resolution_shield_hours:
                            logger.info(
                                "rebalance_skipped_near_resolution",
                                market_id=pos.market_id[:40],
                                hours_left=round(hours_left, 1),
                                pnl_pct=round(pnl_pct, 4),
                            )
                            continue
            candidates.append((pos, pnl_pct))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1])

        for idx, (candidate_pos, candidate_pnl_pct) in enumerate(candidates):
            logger.info(
                "rebalance_attempt",
                closing_market=candidate_pos.market_id[:20],
                closing_strategy=candidate_pos.strategy,
                closing_pnl_pct=round(candidate_pnl_pct, 4),
                new_signal_strategy=signal.strategy,
                new_signal_edge=round(signal.edge, 4),
            )

            close_trade = await self.order_manager.close_position(
                market_id=candidate_pos.market_id,
                token_id=candidate_pos.token_id,
                size=candidate_pos.size,
                current_price=candidate_pos.current_price,
                question=candidate_pos.question,
                outcome=candidate_pos.outcome,
                category=candidate_pos.category,
                strategy=candidate_pos.strategy,
                entry_price=candidate_pos.avg_price,
            )
            if close_trade is None:
                logger.warning(
                    "rebalance_close_failed_trying_next",
                    market_id=candidate_pos.market_id,
                    candidates_remaining=len(candidates) - idx - 1,
                )
                continue

            await log_rebalance(
                closed_market_id=candidate_pos.market_id,
                closed_question=candidate_pos.question,
                closed_strategy=candidate_pos.strategy,
                closed_pnl=0.0,
                new_market_id=signal.market_id,
                new_question=signal.question,
                new_strategy=signal.strategy,
                new_edge=signal.edge,
            )

            logger.info(
                "rebalance_closed_position",
                closed_market=candidate_pos.market_id[:20],
                new_signal=signal.market_id[:20],
                trade_status=close_trade.status,
            )
            return candidate_pos, close_trade

        logger.warning("rebalance_all_candidates_failed", candidates=len(candidates))
        return None

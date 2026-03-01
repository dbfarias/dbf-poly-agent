"""Extracted position closing logic — handles exits, sell fills, and rebalancing."""

from datetime import datetime, timezone

import structlog

from bot.agent.events import event_bus
from bot.config import settings
from bot.data.activity import log_exit_triggered, log_position_closed, log_rebalance
from bot.data.database import async_session

logger = structlog.get_logger()


class PositionCloser:
    """Encapsulates all position closing and fill-handling logic.

    Separated from TradingEngine to reduce engine.py size and improve cohesion.
    """

    def __init__(self, order_manager, portfolio, risk_manager):
        self.order_manager = order_manager
        self.portfolio = portfolio
        self.risk_manager = risk_manager

    async def close_position(self, pos) -> None:
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
                        pos.market_id, pnl, "strategy_exit",
                    )
            except Exception as e:
                logger.error("close_position_db_error", error=str(e))

            await log_position_closed(
                market_id=pos.market_id,
                question=pos.question,
                strategy=pos.strategy,
                pnl=pnl,
                exit_reason="strategy_exit",
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
        self, market_id: str, sell_price: float, trade_id: int, shares: float,
    ) -> None:
        """Callback when a pending live SELL order is confirmed filled."""
        logger.info(
            "deferred_sell_fill",
            market_id=market_id,
            sell_price=sell_price,
            trade_id=trade_id,
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
            question="",
            strategy="",
            pnl=pnl,
            exit_reason="deferred_sell_fill",
        )
        await event_bus.emit(
            "trade_filled",
            trade_event="sell_filled",
            market_id=market_id,
            question="",
            strategy="",
            side="SELL",
            price=sell_price,
            size=shares,
            pnl=pnl,
        )

    async def handle_order_fill(self, signal, shares: float) -> None:
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
        await event_bus.emit(
            "trade_filled",
            trade_event="buy_filled",
            market_id=signal.market_id,
            question=signal.question,
            strategy=signal.strategy,
            side="BUY",
            price=signal.market_price,
            size=shares,
        )

    async def try_rebalance(self, signal, positions):
        """Close the weakest losing position to make room for a better signal.

        Returns the closed Position if successful, None if no rebalance happened.
        """
        min_rebalance_edge = 0.03
        min_hold_seconds = 300
        min_sell_shares = 5.0

        if signal.edge < min_rebalance_edge:
            return None

        candidates = []
        now = datetime.now(timezone.utc)
        for pos in positions:
            if not settings.is_paper and pos.size < min_sell_shares:
                continue
            created = pos.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and (now - created).total_seconds() < min_hold_seconds:
                continue
            if pos.unrealized_pnl > 0:
                continue
            pnl_pct = (
                (pos.current_price - pos.avg_price) / pos.avg_price
                if pos.avg_price > 0 else 0.0
            )
            candidates.append((pos, pnl_pct))

        if not candidates:
            return None

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
            closed_pnl=0.0,
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

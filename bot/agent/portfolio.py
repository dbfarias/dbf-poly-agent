"""Portfolio state tracker with sync from blockchain and paper mode."""

from datetime import datetime

import structlog

from bot.config import CapitalTier, settings
from bot.data.database import async_session
from bot.data.models import PortfolioSnapshot, Position
from bot.data.repositories import PortfolioSnapshotRepository, PositionRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.gamma import GammaClient

logger = structlog.get_logger()


class Portfolio:
    """Tracks portfolio state: cash, positions, equity."""

    def __init__(
        self,
        clob_client: PolymarketClient,
        data_api: DataApiClient,
        gamma_client: GammaClient | None = None,
    ):
        self.clob = clob_client
        self.data_api = data_api
        self.gamma = gamma_client

        # State
        self._cash: float = settings.initial_bankroll
        self._polymarket_balance: float | None = None
        self._positions: list[Position] = []
        self._peak_equity: float = settings.initial_bankroll
        self._realized_pnl_today: float = 0.0
        self._last_snapshot: datetime | None = None

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> list[Position]:
        return [p for p in self._positions if p.is_open]

    @property
    def positions_value(self) -> float:
        return sum(p.size * p.current_price for p in self.positions)

    @property
    def total_equity(self) -> float:
        return self._cash + self.positions_value

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions)

    @property
    def tier(self) -> CapitalTier:
        return CapitalTier.from_bankroll(self.total_equity)

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    async def sync(self) -> None:
        """Sync portfolio state from blockchain / paper state.

        Always fetches real Polymarket balance if connected (even in paper mode)
        so the dashboard shows accurate account data.
        """
        # Sync positions from Polymarket first
        if self.clob.is_connected and not settings.is_paper:
            await self._sync_from_polymarket()

        async with async_session() as session:
            pos_repo = PositionRepository(session)
            self._positions = await pos_repo.get_open()

        # Fetch real balance from Polymarket (works in both modes)
        if self.clob.is_connected:
            try:
                real_balance = await self.clob.get_balance()
                if real_balance is not None:
                    self._polymarket_balance = real_balance
                    # In live mode, use real balance as cash
                    if not settings.is_paper:
                        self._cash = real_balance
            except Exception as e:
                logger.error("balance_sync_failed", error=str(e))

        # Update peak equity
        equity = self.total_equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        logger.debug(
            "portfolio_synced",
            cash=self._cash,
            polymarket_balance=self._polymarket_balance,
            positions=self.open_position_count,
            equity=equity,
            tier=self.tier.value,
        )

    async def _sync_from_polymarket(self) -> None:
        """Fetch positions from Polymarket and sync into local DB."""
        address = self.clob.get_address()
        if not address:
            return

        try:
            remote_positions = await self.data_api.get_positions(address)
        except Exception as e:
            logger.error("polymarket_position_sync_failed", error=str(e))
            return

        remote_market_ids: set[str] = set()

        async with async_session() as session:
            pos_repo = PositionRepository(session)

            for rp in remote_positions:
                if rp.size <= 0:
                    continue

                remote_market_ids.add(rp.market_id)

                position = Position(
                    market_id=rp.market_id,
                    token_id=rp.token_id,
                    question=rp.question[:200],
                    outcome=rp.outcome,
                    category="",
                    strategy="external",  # Default for new; upsert preserves existing
                    side="BUY",
                    size=rp.size,
                    avg_price=rp.avg_price,
                    current_price=rp.current_price,
                    cost_basis=rp.size * rp.avg_price,
                    unrealized_pnl=rp.unrealized_pnl,
                    is_open=True,
                    is_paper=False,
                )
                await pos_repo.upsert(position)

            # Close local positions no longer on Polymarket
            local_positions = await pos_repo.get_open()
            for lp in local_positions:
                if lp.market_id in remote_market_ids:
                    continue

                if lp.strategy == "external":
                    # External positions: close immediately (no longer on chain)
                    await pos_repo.close(lp.market_id)
                    logger.info(
                        "external_position_closed",
                        market_id=lp.market_id,
                    )
                else:
                    # Bot-opened positions: check if market has resolved
                    await self._close_if_resolved(lp, pos_repo)

        logger.debug(
            "polymarket_positions_synced",
            remote_count=len(remote_market_ids),
        )

    async def _close_if_resolved(
        self, position: Position, pos_repo: "PositionRepository"
    ) -> None:
        """Close a bot-opened position if the market has resolved on Polymarket.

        When a market resolves, winning shares are redeemed at $1.00 and
        disappear from the remote positions list. We check the market's
        closed/archived status to confirm resolution before closing locally.
        """
        if not self.gamma:
            return

        try:
            market = await self.gamma.get_market(position.market_id)
        except Exception as e:
            logger.debug(
                "resolution_check_failed",
                market_id=position.market_id,
                error=str(e),
            )
            return

        if market is None:
            # Market not found — likely resolved and removed. Close at last known price.
            settlement_price = position.current_price
            resolved = True
        elif market.closed or market.archived or not market.active:
            # Market confirmed resolved — determine win/loss from outcome prices
            settlement_price = self._get_settlement_price(market, position)
            resolved = True
        else:
            # Market still active — position may have been sold externally
            # Don't auto-close to avoid false positives
            resolved = False

        if resolved:
            pnl = (settlement_price - position.avg_price) * position.size
            await pos_repo.close(position.market_id)
            self._realized_pnl_today += pnl
            logger.info(
                "position_resolved",
                market_id=position.market_id,
                strategy=position.strategy,
                outcome=position.outcome,
                avg_price=round(position.avg_price, 4),
                settlement=round(settlement_price, 4),
                size=position.size,
                pnl=round(pnl, 4),
            )

    @staticmethod
    def _get_settlement_price(market, position: Position) -> float:
        """Determine settlement price based on market outcome prices.

        After resolution, winning outcome = $1.00, losing = $0.00.
        If prices aren't decisive, fall back to the position's last known price.
        """
        prices = market.outcome_price_list
        outcomes = market.outcomes
        if not prices or not outcomes:
            return position.current_price

        for i, outcome in enumerate(outcomes):
            if i < len(prices) and outcome == position.outcome:
                price = prices[i]
                # After resolution: $1.00 = win, $0.00 = loss
                if price >= 0.95:
                    return 1.0
                elif price <= 0.05:
                    return 0.0
                return price

        return position.current_price

    async def record_trade_open(
        self, market_id: str, token_id: str, question: str, outcome: str,
        category: str, strategy: str, side: str, size: float, price: float,
    ) -> None:
        """Record a new position opening."""
        cost = size * price
        self._cash -= cost

        position = Position(
            market_id=market_id,
            token_id=token_id,
            question=question,
            outcome=outcome,
            category=category,
            strategy=strategy,
            side=side,
            size=size,
            avg_price=price,
            current_price=price,
            cost_basis=cost,
            unrealized_pnl=0.0,
            is_open=True,
            is_paper=settings.is_paper,
        )

        async with async_session() as session:
            repo = PositionRepository(session)
            await repo.upsert(position)

        await self.sync()

    async def record_trade_close(self, market_id: str, close_price: float) -> float:
        """Record a position closing. Returns realized PnL."""
        position = next((p for p in self._positions if p.market_id == market_id), None)
        if not position:
            logger.warning("close_position_not_found", market_id=market_id)
            return 0.0

        pnl = (close_price - position.avg_price) * position.size
        self._cash += position.size * close_price
        self._realized_pnl_today += pnl

        async with async_session() as session:
            repo = PositionRepository(session)
            await repo.close(market_id)

        await self.sync()
        return pnl

    async def update_position_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for open positions."""
        for position in self._positions:
            if position.token_id in prices:
                position.current_price = prices[position.token_id]
                position.unrealized_pnl = (
                    (position.current_price - position.avg_price) * position.size
                )

        async with async_session() as session:
            repo = PositionRepository(session)
            for p in self._positions:
                if p.is_open:
                    await repo.upsert(p)

    async def take_snapshot(self) -> PortfolioSnapshot:
        """Take and persist a portfolio snapshot."""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.utcnow(),
            total_equity=self.total_equity,
            cash_balance=self._cash,
            positions_value=self.positions_value,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl_today=self._realized_pnl_today,
            open_positions=self.open_position_count,
            daily_return_pct=0.0,
            max_drawdown_pct=(
                (self._peak_equity - self.total_equity) / self._peak_equity
                if self._peak_equity > 0 else 0.0
            ),
        )

        async with async_session() as session:
            repo = PortfolioSnapshotRepository(session)
            latest = await repo.get_latest()
            if latest:
                snapshot.daily_return_pct = (
                    (self.total_equity - latest.total_equity) / latest.total_equity
                    if latest.total_equity > 0 else 0.0
                )
            await repo.create(snapshot)

        self._last_snapshot = snapshot.timestamp
        return snapshot

    def get_overview(self) -> dict:
        """Get portfolio overview for the dashboard."""
        return {
            "total_equity": self.total_equity,
            "cash_balance": self._cash,
            "polymarket_balance": self._polymarket_balance,
            "positions_value": self.positions_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl_today": self._realized_pnl_today,
            "open_positions": self.open_position_count,
            "peak_equity": self._peak_equity,
            "tier": self.tier.value,
            "is_paper": settings.is_paper,
            "wallet_address": self.clob.get_address(),
        }

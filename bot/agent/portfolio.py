"""Portfolio state tracker with sync from blockchain and paper mode."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from bot.config import settings, trading_day
from bot.data.database import async_session
from bot.data.models import CapitalFlow, PortfolioSnapshot, Position
from bot.data.repositories import (
    CapitalFlowRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
)
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.gamma import GammaClient

if TYPE_CHECKING:
    from bot.agent.risk_manager import RiskManager

logger = structlog.get_logger()


class Portfolio:
    """Tracks portfolio state: cash, positions, equity."""

    def __init__(
        self,
        clob_client: PolymarketClient,
        data_api: DataApiClient,
        gamma_client: GammaClient | None = None,
        risk_manager: RiskManager | None = None,
    ):
        self.clob = clob_client
        self.data_api = data_api
        self.gamma = gamma_client
        self._risk_manager = risk_manager

        # State
        self._cash: float = settings.initial_bankroll
        self._polymarket_balance: float | None = None
        self._positions: list[Position] = []
        self._peak_equity: float = settings.initial_bankroll
        self._realized_pnl_today: float = 0.0
        self._pnl_date: str = ""  # Track which UTC day the PnL belongs to
        self._skip_next_flow: bool = True  # Suppress first flow detection (stale cash vs real balance)
        self._auto_removed_ids: set[str] = set()  # Markets auto-removed from stuck; skip in sync
        self._day_start_equity: float = settings.initial_bankroll  # Equity at 00:00 UTC
        self._last_snapshot: datetime | None = None
        self._redeemer = None
        self._peak_equity_loaded: bool = False  # Load from DB only once at startup

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
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def realized_pnl_today(self) -> float:
        return self._realized_pnl_today

    @property
    def day_start_equity(self) -> float:
        return self._day_start_equity

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def realized_equity(self) -> float:
        """Equity based only on realized PnL (for peak tracking).

        Unrealized gains from open positions are excluded to prevent
        temporary spikes (e.g. crypto 5-min markets) from inflating
        peak equity and triggering false drawdown blocks.
        """
        return self._day_start_equity + self._realized_pnl_today

    def reset_daily_state(self, equity: float) -> None:
        """Reset daily PnL counters at midnight boundary.

        Sets day_start_equity to the equity at midnight — this anchors
        the daily return % calculation for the new day.
        Does NOT touch peak equity (independent concern).
        Should only be called at day boundary, not mid-day.
        """
        self._realized_pnl_today = 0.0
        self._day_start_equity = equity
        self._skip_next_flow = True

    def reset_realized_pnl(self) -> None:
        """Zero out realized PnL counter without touching day_start_equity.

        Use for mid-day corrections when PnL accumulator is wrong
        but day_start_equity is correct (captured at midnight).
        """
        self._realized_pnl_today = 0.0

    def reset_peak_equity(self, equity: float) -> None:
        """Reset peak equity to unblock drawdown gate.

        Call this when peak is stale (e.g. after sustained losses)
        and the drawdown limit is permanently blocking trades.
        Does NOT touch daily P&L — today's numbers stay intact.
        """
        self._peak_equity = equity

    def restore_realized_pnl(self, pnl: float, date: str) -> None:
        """Restore realized PnL from persisted state after restart.

        Only restores if date matches today (stale data is ignored).
        """
        today = trading_day()
        if date == today and pnl != 0.0:
            self._realized_pnl_today = pnl
            self._pnl_date = date

    def restore_day_start_equity(self, equity: float, date: str) -> None:
        """Restore start-of-day equity from persisted state after restart.

        Prevents polymarket_pnl_today from resetting to $0 after deploys.
        """
        today = trading_day()
        if date == today and equity > 0:
            self._day_start_equity = equity
            self._pnl_date = date  # Prevent sync from resetting

    async def sync(self) -> None:
        """Sync portfolio state from blockchain / paper state.

        Always fetches real Polymarket balance if connected (even in paper mode)
        so the dashboard shows accurate account data.

        Resets daily PnL and captures day-start equity at local midnight.
        """
        # Check if this is a new trading day (reset PnL, but defer equity capture)
        today = trading_day()
        need_daily_reset = self._pnl_date != today
        if need_daily_reset:
            if self._pnl_date:
                logger.info(
                    "daily_pnl_reset",
                    previous_date=self._pnl_date,
                    previous_pnl=round(self._realized_pnl_today, 4),
                )
            self._realized_pnl_today = 0.0
            self._pnl_date = today

        # Sync positions from Polymarket first
        if self.clob.is_connected and not settings.is_paper:
            await self._sync_from_polymarket()
        elif settings.is_paper:
            await self._update_paper_prices()

        async with async_session() as session:
            pos_repo = PositionRepository(session)
            self._positions = await pos_repo.get_open()

        # Paper mode: restore cash from persisted state to survive restarts
        if settings.is_paper:
            await self._restore_paper_cash()

        # Fetch real balance from Polymarket (works in both modes)
        if self.clob.is_connected:
            try:
                real_balance = await self.clob.get_balance()
                if real_balance is not None:
                    self._polymarket_balance = real_balance
                    # In live mode, detect deposit/withdrawal before overwriting cash
                    if not settings.is_paper:
                        await self._detect_capital_flow(real_balance)
                        self._cash = real_balance
            except Exception as e:
                logger.error("balance_sync_failed", error=str(e))

        # Capture start-of-day equity AFTER sync (so balance is accurate)
        if need_daily_reset:
            try:
                from bot.data.settings_store import StateStore

                existing, existing_date = await StateStore.load_day_start_equity()
                if existing_date == today and existing > 0:
                    # Restore persisted midnight value (don't overwrite on restart)
                    self._day_start_equity = existing
                    logger.info(
                        "day_start_equity_restored",
                        equity=round(existing, 4),
                        date=today,
                    )
                else:
                    # First sync of the day — capture and persist
                    self._day_start_equity = self.total_equity
                    await StateStore.save_day_start_equity(
                        self._day_start_equity, today,
                    )
                    logger.info(
                        "day_start_equity_captured",
                        equity=round(self._day_start_equity, 4),
                        date=today,
                    )
            except Exception as e:
                # Fallback: use current equity
                self._day_start_equity = self.total_equity
                logger.error("day_start_equity_persist_failed", error=str(e))

        # Restore peak equity from DB once at startup (not on every sync)
        if not self._peak_equity_loaded:
            try:
                from bot.data.settings_store import StateStore

                saved_peak = await StateStore.load_peak_equity()
                if saved_peak is not None:
                    self._peak_equity = saved_peak
                    logger.info(
                        "peak_equity_restored",
                        peak=round(saved_peak, 4),
                    )
                self._peak_equity_loaded = True
            except Exception as e:
                logger.error("peak_equity_restore_failed", error=str(e))

        # Update peak equity using realized PnL only (unrealized excluded to
        # prevent crypto 5-min positions from inflating peak temporarily)
        adjusted_peak = self.realized_equity
        if adjusted_peak > self._peak_equity:
            self._peak_equity = adjusted_peak
            # Persist so it survives restarts
            try:
                from bot.data.settings_store import StateStore

                await StateStore.save_peak_equity(self._peak_equity)
            except Exception:
                pass  # Non-critical

        # Persist paper cash so it survives restarts
        if settings.is_paper:
            try:
                from bot.data.settings_store import StateStore

                await StateStore.save_paper_cash(
                    self._cash, settings.initial_bankroll,
                )
            except Exception:
                pass  # Non-critical, will retry next cycle

        logger.debug(
            "portfolio_synced",
            cash=self._cash,
            polymarket_balance=self._polymarket_balance,
            positions=self.open_position_count,
            equity=self.total_equity,
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

                # Skip resolved positions worth $0 (e.g. losing side of
                # binary market, still "redeemable" on-chain but valueless)
                if rp.current_price <= 0:
                    logger.debug(
                        "skipping_zero_value_position",
                        market_id=rp.market_id,
                        outcome=rp.outcome,
                        size=rp.size,
                    )
                    continue

                # Skip markets that were auto-removed as stuck/worthless
                if rp.market_id in self._auto_removed_ids:
                    logger.debug(
                        "sync_skipping_auto_removed",
                        market_id=rp.market_id,
                    )
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
            now = datetime.now(timezone.utc)
            for lp in local_positions:
                if lp.market_id in remote_market_ids:
                    continue

                # Grace period: skip recently created positions to avoid
                # race condition where Data API hasn't processed the order yet
                created = lp.created_at
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_seconds = (
                    (now - created).total_seconds()
                    if created else 0
                )
                if age_seconds < 600:  # 10 minutes
                    logger.info(
                        "skipping_recent_position",
                        market_id=lp.market_id,
                        age_seconds=round(age_seconds),
                        strategy=lp.strategy,
                    )
                    continue

                if lp.strategy == "external":
                    # External positions: close immediately (no longer on chain)
                    # Calculate PnL and update realized tracking
                    settlement = lp.current_price * lp.size
                    pnl = (lp.current_price - lp.avg_price) * lp.size
                    self._cash += settlement
                    self._realized_pnl_today += pnl
                    if self._risk_manager and pnl != 0:
                        self._risk_manager.update_daily_pnl(pnl)
                    await pos_repo.close(lp.market_id)
                    logger.info(
                        "external_position_closed",
                        market_id=lp.market_id,
                        settlement=round(settlement, 4),
                        pnl=round(pnl, 4),
                    )
                else:
                    # Bot-opened positions: check if market has resolved
                    await self._close_if_resolved(lp, pos_repo, session)

        logger.debug(
            "polymarket_positions_synced",
            remote_count=len(remote_market_ids),
        )

    async def _update_paper_prices(self) -> None:
        """Fetch current prices for paper-mode positions via Gamma API.

        Paper mode skips _sync_from_polymarket() (no on-chain positions),
        but prices still need updating so PnL isn't frozen at entry.
        Does NOT create or remove positions — only updates current_price.
        """
        if not self.gamma or not self._positions:
            return

        prices: dict[str, float] = {}
        for pos in self._positions:
            if not pos.is_open:
                continue
            try:
                market = await self.gamma.get_market(pos.market_id)
                if market is None:
                    continue
                token_ids = market.token_ids
                outcome_prices = market.outcome_price_list
                for i, tid in enumerate(token_ids):
                    if tid == pos.token_id and i < len(outcome_prices):
                        prices[tid] = outcome_prices[i]
                        break
            except Exception as e:
                logger.warning(
                    "paper_price_update_failed",
                    market_id=pos.market_id,
                    error=str(e),
                )

        if prices:
            await self.update_position_prices(prices)

    async def _detect_capital_flow(self, new_balance: float) -> None:
        """Detect deposit/withdrawal by comparing new balance to expected cash.

        If the difference is significant (>$0.50), record a CapitalFlow and
        adjust day_start_equity so PnL calculations remain deposit-immune.
        """
        # After mode switch, the first balance read is the real Polymarket
        # balance which differs from paper cash — skip to avoid phantom flow
        if self._skip_next_flow:
            self._skip_next_flow = False
            logger.info(
                "capital_flow_skipped_mode_switch",
                old_cash=round(self._cash, 4),
                new_balance=round(new_balance, 4),
            )
            return

        flow = new_balance - self._cash
        if abs(flow) < 0.50:
            return

        flow_type = "deposit" if flow > 0 else "withdrawal"
        logger.info(
            "capital_flow_detected",
            flow_type=flow_type,
            amount=round(flow, 4),
            old_cash=round(self._cash, 4),
            new_balance=round(new_balance, 4),
        )

        # Record to DB
        try:
            async with async_session() as session:
                repo = CapitalFlowRepository(session)
                await repo.create(CapitalFlow(
                    amount=flow,
                    flow_type=flow_type,
                    source="polymarket",
                    note=f"Auto-detected: ${flow:+.2f}",
                    is_paper=False,
                ))
        except Exception as e:
            logger.error("capital_flow_record_failed", error=str(e))

        # Adjust day_start_equity so PnL stays immune
        self._day_start_equity += flow
        try:
            from bot.data.settings_store import StateStore

            await StateStore.save_day_start_equity(
                self._day_start_equity, trading_day(),
            )
        except Exception as e:
            logger.error("day_start_equity_adjust_failed", error=str(e))

        # Propagate to risk manager
        if self._risk_manager:
            self._risk_manager.set_day_start_equity(self._day_start_equity)

    async def _restore_paper_cash(self) -> None:
        """Restore paper cash from persisted state.

        On first run, persists initial_bankroll. On subsequent restarts,
        restores persisted cash instead of resetting to initial_bankroll.
        Detects initial_bankroll config changes and records as capital flow.
        """
        try:
            from bot.data.settings_store import StateStore

            saved_cash, saved_bankroll = await StateStore.load_paper_cash()
            if saved_cash is None:
                # First run ever — persist current state
                await StateStore.save_paper_cash(
                    self._cash, settings.initial_bankroll,
                )
                return

            # Check if initial_bankroll config changed since last run
            bankroll_changed = (
                saved_bankroll is not None
                and abs(settings.initial_bankroll - saved_bankroll) > 0.01
            )

            if bankroll_changed:
                # Config changed — compute the flow and record it
                flow = settings.initial_bankroll - saved_bankroll
                flow_type = "deposit" if flow > 0 else "withdrawal"
                logger.info(
                    "paper_bankroll_change_detected",
                    flow_type=flow_type,
                    amount=round(flow, 4),
                    old_bankroll=round(saved_bankroll, 4),
                    new_bankroll=round(settings.initial_bankroll, 4),
                )
                try:
                    async with async_session() as session:
                        repo = CapitalFlowRepository(session)
                        await repo.create(CapitalFlow(
                            amount=flow,
                            flow_type=flow_type,
                            source="config",
                            note=(
                                f"INITIAL_BANKROLL changed: "
                                f"${saved_bankroll:.2f} -> "
                                f"${settings.initial_bankroll:.2f}"
                            ),
                            is_paper=True,
                        ))
                except Exception as e:
                    logger.error("paper_capital_flow_failed", error=str(e))

                # Adjust cash by the bankroll delta (not reset to bankroll)
                self._cash = saved_cash + flow
                self._day_start_equity += flow
                await StateStore.save_paper_cash(
                    self._cash, settings.initial_bankroll,
                )
            else:
                # Normal restart — restore persisted cash
                self._cash = saved_cash
        except Exception as e:
            logger.error("restore_paper_cash_failed", error=str(e))

    async def _close_if_resolved(
        self, position: Position, pos_repo: "PositionRepository",
        session=None,
    ) -> None:
        """Close a bot-opened position that no longer exists on Polymarket.

        Possible reasons:
        1. Market resolved → winning shares redeemed at $1.00
        2. Position sold externally (via web UI or another tool)

        In both cases, the position is gone from the chain, so we close locally.
        """
        if not self.gamma:
            # Without gamma client, can't verify — close at last known price
            pnl = 0.0
            await pos_repo.close(position.market_id)
            self._realized_pnl_today += pnl
            if self._risk_manager and pnl != 0:
                self._risk_manager.update_daily_pnl(pnl)
            logger.info(
                "position_closed_no_gamma",
                market_id=position.market_id,
                strategy=position.strategy,
            )
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
            # Market not found — likely resolved and removed
            settlement_price = position.current_price
        elif market.closed or market.archived or not market.active:
            # Market confirmed resolved — determine win/loss from outcome prices
            settlement_price = self._get_settlement_price(market, position)
        else:
            # Market still active but position gone → sold externally
            settlement_price = position.current_price
            logger.info(
                "position_sold_externally",
                market_id=position.market_id,
                strategy=position.strategy,
                outcome=position.outcome,
                size=position.size,
            )

        pnl = (settlement_price - position.avg_price) * position.size
        await pos_repo.close(position.market_id)
        self._realized_pnl_today += pnl

        # Update cash with settlement proceeds so _detect_capital_flow
        # doesn't see the returning funds as a false deposit
        settlement_proceeds = settlement_price * position.size
        self._cash += settlement_proceeds

        if self._risk_manager and pnl != 0:
            self._risk_manager.update_daily_pnl(pnl)

        # Write PnL to the original BUY trade (enables learner to learn)
        exit_reason = "resolution" if (
            market is None or market.closed or market.archived or not market.active
        ) else "external_close"

        # Auto-claim: redeem resolved positions on-chain
        if (
            exit_reason == "resolution"
            and settings.use_auto_claim
            and settings.trading_mode.value == "live"
        ):
            await self._try_redeem(position)

        try:
            from bot.data.repositories import TradeRepository

            if session is not None:
                repo = TradeRepository(session)
                await repo.close_trade_for_position(
                    position.market_id, pnl, exit_reason,
                    close_price=settlement_price,
                    position_size=position.size,
                )
            else:
                async with async_session() as s:
                    repo = TradeRepository(s)
                    await repo.close_trade_for_position(
                        position.market_id, pnl, exit_reason,
                        close_price=settlement_price,
                        position_size=position.size,
                    )
        except Exception as e:
            logger.warning(
                "close_trade_pnl_update_failed",
                market_id=position.market_id,
                error=str(e),
            )

        logger.info(
            "position_closed",
            market_id=position.market_id,
            strategy=position.strategy,
            outcome=position.outcome,
            avg_price=round(position.avg_price, 4),
            settlement=round(settlement_price, 4),
            size=position.size,
            pnl=round(pnl, 4),
        )

    async def _try_redeem(self, position: Position) -> None:
        """Attempt to redeem resolved position on-chain (fire-and-forget)."""
        try:
            if self._redeemer is None:
                from bot.polymarket.redeemer import PositionRedeemer

                self._redeemer = PositionRedeemer()

            condition_id = getattr(position, "condition_id", None)
            if not condition_id:
                logger.debug(
                    "redeem_skip_no_condition_id",
                    market_id=position.market_id,
                )
                return

            tx_hash = await self._redeemer.redeem(condition_id)
            if tx_hash:
                logger.info(
                    "position_redeemed",
                    market_id=position.market_id,
                    tx_hash=tx_hash,
                )
        except Exception as e:
            logger.warning(
                "redeem_attempt_failed",
                market_id=position.market_id,
                error=str(e),
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

        # Reload positions from DB (without full Polymarket sync to avoid
        # race condition where Data API hasn't processed the order yet)
        async with async_session() as session:
            repo = PositionRepository(session)
            self._positions = await repo.get_open()

    async def record_trade_close(self, market_id: str, close_price: float) -> float:
        """Record a position closing. Returns realized PnL (gross, before fees).

        Fee-adjusted PnL is computed in close_trade_for_position() where the
        BUY trade's fee_rate_bps is available.
        """
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

        # Reload positions from DB (full sync deferred to _trading_cycle)
        async with async_session() as session:
            repo = PositionRepository(session)
            self._positions = await repo.get_open()

        return pnl

    def mark_auto_removed(self, market_id: str) -> None:
        """Mark a market as auto-removed so sync won't re-create it."""
        self._auto_removed_ids.add(market_id)

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
        """Take and persist a portfolio snapshot.

        trading_pnl is deposit-immune (realized + unrealized from trades only).
        daily_return_pct is computed from trading_pnl / day_start_equity.
        """
        trading_pnl = self._realized_pnl_today + self.unrealized_pnl
        daily_return_pct = (
            trading_pnl / self._day_start_equity
            if self._day_start_equity > 0 else 0.0
        )
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(timezone.utc),
            total_equity=self.total_equity,
            cash_balance=self._cash,
            positions_value=self.positions_value,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl_today=self._realized_pnl_today,
            open_positions=self.open_position_count,
            daily_return_pct=daily_return_pct,
            trading_pnl=trading_pnl,
            max_drawdown_pct=(
                (self._peak_equity - self.total_equity) / self._peak_equity
                if self._peak_equity > 0 else 0.0
            ),
        )

        async with async_session() as session:
            repo = PortfolioSnapshotRepository(session)
            await repo.create(snapshot)

        self._last_snapshot = snapshot.timestamp
        return snapshot

    def get_overview(self) -> dict:
        """Get portfolio overview for the dashboard.

        Daily target is based on start-of-day equity (fixed at midnight UTC),
        not current equity, so the goalpost doesn't move during the day.

        polymarket_pnl_today = trade-based PnL (realized + unrealized),
        inherently immune to deposits/withdrawals.
        """
        equity = self.total_equity
        target_pct = settings.daily_target_pct
        target_usd = self._day_start_equity * target_pct

        # Trade-based PnL: immune to deposits/withdrawals
        trading_pnl = self._realized_pnl_today + self.unrealized_pnl

        # Use trading_pnl for progress (includes both realized + unrealized)
        progress = (
            trading_pnl / target_usd if target_usd > 0 else 0.0
        )
        return {
            "total_equity": equity,
            "cash_balance": self._cash,
            "polymarket_balance": self._polymarket_balance,
            "positions_value": self.positions_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl_today": self._realized_pnl_today,
            "polymarket_pnl_today": round(trading_pnl, 4),
            "open_positions": self.open_position_count,
            "peak_equity": self._peak_equity,
            "day_start_equity": round(self._day_start_equity, 4),
            "is_paper": settings.is_paper,
            "wallet_address": self.clob.get_address(),
            "daily_target_pct": target_pct,
            "daily_target_usd": round(target_usd, 4),
            "daily_progress_pct": round(progress, 4),
        }

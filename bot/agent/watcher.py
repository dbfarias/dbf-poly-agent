"""TradeWatcher — a live agent monitoring a single trade."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from bot.agent.watcher_signals import (
    PriceMomentum,
    VolumeSignal,
    WatcherVerdict,
    aggregate_signals,
    compute_news_signal,
    compute_price_momentum,
)
from bot.data.database import async_session
from bot.data.models import WatcherDecision

if TYPE_CHECKING:
    from bot.data.models import Watcher
    from bot.data.price_tracker import PriceTracker
    from bot.polymarket.data_api import DataApiClient
    from bot.research.news_fetcher import NewsFetcher

logger = structlog.get_logger()


@dataclass(frozen=True)
class PendingScaleUp:
    """Immutable request for the WatcherManager to execute a scale-up."""

    watcher_id: int
    token_id: str
    market_id: str
    question: str
    outcome: str
    current_price: float
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class PendingExit:
    """Immutable request for the WatcherManager to execute an exit."""

    watcher_id: int
    market_id: str
    token_id: str
    reasoning: str


class TradeWatcher:
    """A live, temporary agent monitoring a specific trade position."""

    def __init__(
        self,
        watcher: Watcher,
        price_tracker: PriceTracker | None = None,
        news_fetcher: NewsFetcher | None = None,
        data_api: DataApiClient | None = None,
    ):
        self._watcher = watcher
        self._price_tracker = price_tracker
        self._news_fetcher = news_fetcher
        self._data_api = data_api
        self._running = False
        self._pending_scale_up: PendingScaleUp | None = None
        self._pending_exit: PendingExit | None = None

    @property
    def watcher_id(self) -> int:
        return self._watcher.id

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def pending_scale_up(self) -> PendingScaleUp | None:
        return self._pending_scale_up

    @property
    def pending_exit(self) -> PendingExit | None:
        return self._pending_exit

    def clear_pending_scale_up(self) -> None:
        self._pending_scale_up = None

    def clear_pending_exit(self) -> None:
        self._pending_exit = None

    async def run(self) -> None:
        """Main watcher loop — runs until killed or auto-terminated."""
        self._running = True
        logger.info(
            "watcher_started",
            watcher_id=self.watcher_id,
            market=self._watcher.question[:50],
        )

        try:
            while self._running and self._watcher.status == "active":
                await self._check_cycle()
                await asyncio.sleep(self._watcher.check_interval_sec)
        except asyncio.CancelledError:
            logger.info("watcher_cancelled", watcher_id=self.watcher_id)
        except Exception as e:
            logger.error("watcher_error", watcher_id=self.watcher_id, error=str(e))
        finally:
            self._running = False

    async def _check_cycle(self) -> None:
        """Single check cycle: gather signals, compute verdict, log decision."""
        momentum = self._get_price_momentum()
        volume = await self._get_volume_signal()
        news = await self._get_news_signal()

        current = self._get_current_price()
        verdict = aggregate_signals(
            momentum=momentum,
            volume=volume,
            news=news,
            current_price=current,
            avg_entry=self._watcher.avg_entry_price,
            stop_loss_pct=self._watcher.stop_loss_pct,
        )

        # Update highest price for trailing stop
        if current > self._watcher.highest_price:
            self._watcher.highest_price = current

        # Check auto-termination conditions
        termination_reason = self._check_termination(current)
        if termination_reason:
            verdict = WatcherVerdict(
                action="exit",
                confidence=1.0,
                confirming_signals=0,
                reasoning=termination_reason,
            )

        await self._log_decision(verdict, momentum, volume, news)
        self._apply_verdict(verdict, current)
        self._watcher.last_check_at = datetime.now(timezone.utc)

    def _apply_verdict(self, verdict: WatcherVerdict, current_price: float) -> None:
        """Set pending action flags based on the verdict."""
        w = self._watcher
        if verdict.action == "scale_up" and self._can_scale_up():
            self._pending_scale_up = PendingScaleUp(
                watcher_id=self.watcher_id,
                token_id=w.token_id,
                market_id=w.market_id,
                question=w.question,
                outcome=w.outcome,
                current_price=current_price,
                confidence=verdict.confidence,
                reasoning=verdict.reasoning,
            )
            logger.info(
                "watcher_scale_up_requested",
                watcher_id=self.watcher_id,
                confidence=round(verdict.confidence, 2),
            )
        elif verdict.action == "exit":
            self._pending_exit = PendingExit(
                watcher_id=self.watcher_id,
                market_id=w.market_id,
                token_id=w.token_id,
                reasoning=verdict.reasoning,
            )
            logger.info(
                "watcher_exit_requested",
                watcher_id=self.watcher_id,
                reason=verdict.reasoning[:80],
            )

    def _can_scale_up(self) -> bool:
        """Check if watcher is allowed to scale up."""
        w = self._watcher
        if w.scale_count >= w.max_scale_count:
            return False
        if w.current_exposure >= w.max_exposure_usd:
            return False
        return True

    def _get_current_price(self) -> float:
        """Get current price from price tracker or fall back to entry."""
        if self._price_tracker is not None:
            # PriceTracker stores (price, timestamp) per market
            raw = self._price_tracker._history.get(self._watcher.token_id)
            if raw:
                return raw[-1][0]  # latest price
        return self._watcher.avg_entry_price

    def _get_price_momentum(self) -> PriceMomentum:
        """Compute price momentum from price tracker history."""
        if self._price_tracker is None:
            return PriceMomentum(
                pct_1h=0.0, pct_4h=0.0, pct_24h=0.0, direction="neutral"
            )
        raw = self._price_tracker._history.get(self._watcher.token_id)
        if not raw:
            return PriceMomentum(
                pct_1h=0.0, pct_4h=0.0, pct_24h=0.0, direction="neutral"
            )
        # Convert (price, timestamp) -> (timestamp, price) for signal module
        prices = [(ts, price) for price, ts in raw]
        return compute_price_momentum(prices, time.time())

    async def _get_volume_signal(self) -> VolumeSignal:
        """Get volume data — placeholder until wired to data API."""
        return VolumeSignal(current_ratio=1.0, is_spike=False)

    async def _get_news_signal(self):
        """Fetch news headlines for this watcher's keywords."""
        if self._news_fetcher is None:
            return compute_news_signal([])

        try:
            keywords = json.loads(self._watcher.keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = []

        if not keywords:
            return compute_news_signal([])

        try:
            news_items = await self._news_fetcher.fetch_news(keywords, max_results=5)
            headlines = [(item.title, item.sentiment) for item in news_items]
            self._watcher.last_news_at = datetime.now(timezone.utc)
            return compute_news_signal(headlines)
        except Exception as e:
            logger.warning(
                "watcher_news_fetch_error",
                watcher_id=self.watcher_id,
                error=str(e),
            )
            return compute_news_signal([])

    def _check_termination(self, current_price: float = 0.0) -> str | None:
        """Check if watcher should auto-terminate. Returns reason or None."""
        w = self._watcher

        # Trailing stop: price dropped from highest observed
        if w.highest_price > 0 and current_price > 0:
            trail_threshold = w.highest_price * (1 - w.stop_loss_pct)
            if current_price < trail_threshold:
                return (
                    f"Trailing stop: {current_price:.4f} < "
                    f"peak {w.highest_price:.4f} * "
                    f"(1 - {w.stop_loss_pct})"
                )

        # Max age
        if w.created_at is not None:
            created = w.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = (
                datetime.now(timezone.utc) - created
            ).total_seconds() / 3600
            if age_hours > w.max_age_hours:
                return (
                    f"Max age exceeded: {age_hours:.1f}h > "
                    f"{w.max_age_hours:.1f}h"
                )

        # Max scales reached with price declining
        if w.scale_count >= w.max_scale_count and current_price > 0:
            if w.avg_entry_price > 0 and current_price < w.avg_entry_price:
                return (
                    f"Max scales ({w.scale_count}) reached and "
                    f"price declining ({current_price:.4f} < "
                    f"entry {w.avg_entry_price:.4f})"
                )

        # Market end_date approaching (< 48h left)
        end_date = getattr(w, "end_date", None)
        if end_date is not None:
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            hours_left = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left < 48:
                return f"Market ending soon: {hours_left:.1f}h remaining"

        return None

    async def _log_decision(
        self,
        verdict: WatcherVerdict,
        momentum: PriceMomentum,
        volume: VolumeSignal,
        news,
    ) -> None:
        """Persist decision to watcher_decisions table."""
        signals = {
            "momentum": {
                "pct_1h": momentum.pct_1h,
                "pct_4h": momentum.pct_4h,
                "pct_24h": momentum.pct_24h,
                "direction": momentum.direction,
            },
            "volume": {
                "ratio": volume.current_ratio,
                "is_spike": volume.is_spike,
            },
            "news": {
                "count": news.headline_count,
                "sentiment": news.avg_sentiment,
                "strong": news.has_strong_signal,
            },
        }

        decision = WatcherDecision(
            watcher_id=self.watcher_id,
            decision=verdict.action,
            signals_json=json.dumps(signals),
            reasoning=verdict.reasoning[:500],
            action_taken="pending" if verdict.action in ("scale_up", "exit") else "held",
            price_at_decision=self._get_current_price(),
        )

        async with async_session() as session:
            session.add(decision)
            await session.commit()

        logger.info(
            "watcher_decision",
            watcher_id=self.watcher_id,
            action=verdict.action,
            confidence=round(verdict.confidence, 2),
            confirming=verdict.confirming_signals,
        )

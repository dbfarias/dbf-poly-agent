"""TradeWatcher — a live agent monitoring a single trade."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from bot.agent.watcher_scaling import (
    CachedLevels,
    PriceLevel,
    ScaleLevelRequest,
    evaluate_scale_down,
    evaluate_scale_up,
    find_adjacent_level,
    find_our_level,
    is_cache_valid,
    parse_levels_from_event,
)
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
        self._pending_scale_level: ScaleLevelRequest | None = None
        self._last_fetched_price: float = 0.0
        self._position_lost: bool = False
        self._cached_levels: CachedLevels | None = None

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

    @property
    def pending_scale_level(self) -> ScaleLevelRequest | None:
        return self._pending_scale_level

    def clear_pending_scale_up(self) -> None:
        self._pending_scale_up = None

    def clear_pending_exit(self) -> None:
        self._pending_exit = None

    def clear_pending_scale_level(self) -> None:
        self._pending_scale_level = None

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
        # Verify position still exists — if lost, request re-entry
        if self._watcher.current_exposure > 0:
            await self._verify_position_exists()

        # Fetch price from API if local trackers have no data
        await self._fetch_price_from_api()

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

        # Evaluate event-level scaling after regular verdict
        await self._evaluate_scaling(current, verdict)

        self._watcher.last_check_at = datetime.now(timezone.utc)
        self._watcher.current_price = current

        # Persist state to DB so dashboard shows live data
        await self._persist_state()

    def _apply_verdict(self, verdict: WatcherVerdict, current_price: float) -> None:
        """Set pending action flags based on the verdict."""
        w = self._watcher

        # Re-entry after position lost: only if signals are not bearish
        if self._position_lost and verdict.action != "exit":
            self._position_lost = False
            self._pending_scale_up = PendingScaleUp(
                watcher_id=self.watcher_id,
                token_id=w.token_id,
                market_id=w.market_id,
                question=w.question,
                outcome=w.outcome,
                current_price=current_price,
                confidence=verdict.confidence,
                reasoning=f"Re-entry: position lost, signals say {verdict.action}.",
            )
            logger.info(
                "watcher_re_entry_on_signals",
                watcher_id=self.watcher_id,
                verdict=verdict.action,
                confidence=round(verdict.confidence, 2),
            )
            return

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

    async def _evaluate_scaling(
        self, current_price: float, verdict: WatcherVerdict
    ) -> None:
        """Evaluate if we should scale to a different price level."""
        if not self._watcher.event_slug:
            return

        # Don't create scaling request if we already have pending actions
        if self._pending_scale_level or self._pending_exit:
            return

        levels = await self._get_event_levels()
        if not levels:
            return

        our = find_our_level(levels, self._watcher.market_id)
        if our is None:
            return

        # Require at least 2 confirming signals for scaling decisions
        if verdict.confirming_signals < 2 and verdict.action != "exit":
            return

        self._try_scale_up(current_price, verdict, levels, our)
        if self._pending_scale_level is None:
            self._try_scale_down(current_price, verdict, levels, our)

    def _try_scale_up(
        self,
        current_price: float,
        verdict: WatcherVerdict,
        levels: list[PriceLevel],
        our: PriceLevel,
    ) -> None:
        """Try to create a scale-up request if conditions are met."""
        if verdict.action == "exit":
            return
        next_up = find_adjacent_level(levels, our, "up")
        if not evaluate_scale_up(current_price, our, next_up):
            return
        assert next_up is not None  # evaluate_scale_up checks this  # noqa: S101
        self._pending_scale_level = ScaleLevelRequest(
            watcher_id=self.watcher_id,
            direction="up",
            sell_market_id=our.market_id,
            sell_token_id=our.token_id,
            buy_market_id=next_up.market_id,
            buy_token_id=next_up.token_id,
            buy_price=next_up.yes_price,
            buy_question=next_up.question,
            buy_outcome="Yes",
            from_level=our.price_target,
            to_level=next_up.price_target,
            reasoning=(
                f"Scale up: ${our.price_target}-> ${next_up.price_target}, "
                f"current={current_price:.2f}, next={next_up.yes_price:.2f}"
            ),
        )
        logger.info(
            "watcher_scale_level_up",
            watcher_id=self.watcher_id,
            from_level=our.price_target,
            to_level=next_up.price_target,
        )

    def _try_scale_down(
        self,
        current_price: float,
        verdict: WatcherVerdict,
        levels: list[PriceLevel],
        our: PriceLevel,
    ) -> None:
        """Try to create a scale-down request if conditions are met."""
        next_down = find_adjacent_level(levels, our, "down")
        if not evaluate_scale_down(
            current_price, self._watcher.avg_entry_price, our, next_down
        ):
            return
        assert next_down is not None  # noqa: S101
        self._pending_scale_level = ScaleLevelRequest(
            watcher_id=self.watcher_id,
            direction="down",
            sell_market_id=our.market_id,
            sell_token_id=our.token_id,
            buy_market_id=next_down.market_id,
            buy_token_id=next_down.token_id,
            buy_price=next_down.yes_price,
            buy_question=next_down.question,
            buy_outcome="Yes",
            from_level=our.price_target,
            to_level=next_down.price_target,
            reasoning=(
                f"Scale down: ${our.price_target}-> ${next_down.price_target}, "
                f"current={current_price:.2f}, safer={next_down.yes_price:.2f}"
            ),
        )
        logger.info(
            "watcher_scale_level_down",
            watcher_id=self.watcher_id,
            from_level=our.price_target,
            to_level=next_down.price_target,
        )

    async def _get_event_levels(self) -> list[PriceLevel]:
        """Get cached event levels, refreshing if stale."""
        if is_cache_valid(self._cached_levels):
            assert self._cached_levels is not None  # noqa: S101
            return list(self._cached_levels.levels)

        levels = await self._fetch_event_levels()
        if levels:
            self._cached_levels = CachedLevels(
                levels=tuple(levels), fetched_at=time.time()
            )
        return levels

    async def _fetch_event_levels(self) -> list[PriceLevel]:
        """Fetch all price levels for this watcher's event from Gamma API."""
        slug = self._watcher.event_slug
        if not slug:
            return []
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://gamma-api.polymarket.com/events",
                    params={"slug": slug},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
                event = data[0] if isinstance(data, list) and data else None
                if not event:
                    return []
                return parse_levels_from_event(event)
        except Exception as e:
            logger.debug(
                "watcher_event_levels_fetch_failed",
                watcher_id=self.watcher_id,
                slug=slug,
                error=str(e),
            )
            return []

    def _can_scale_up(self) -> bool:
        """Check if watcher is allowed to scale up."""
        w = self._watcher
        if w.scale_count >= w.max_scale_count:
            return False
        if w.current_exposure >= w.max_exposure_usd:
            return False
        return True

    def _get_current_price(self) -> float:
        """Get current price from multiple sources, fallback chain."""
        # 1. Try PriceTracker (WebSocket-fed, real-time)
        if self._price_tracker is not None:
            raw = self._price_tracker._history.get(self._watcher.token_id)
            if raw:
                return raw[-1][0]
            # Also try market_id as key (some trackers use condition_id)
            raw = self._price_tracker._history.get(self._watcher.market_id)
            if raw:
                return raw[-1][0]

        # 2. Try OrderbookTracker (if available via engine)
        if hasattr(self, "_orderbook_tracker") and self._orderbook_tracker:
            mid = self._orderbook_tracker.get_mid_price(self._watcher.token_id)
            if mid and mid > 0:
                return mid

        # 3. Try fetching from Gamma API (async, so we cache result)
        # This is handled by _fetch_price_from_api called in check_cycle
        if self._last_fetched_price > 0:
            return self._last_fetched_price

        return self._watcher.avg_entry_price

    async def _fetch_price_from_api(self) -> float:
        """Fetch current price from Gamma API as fallback."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                # Gamma API uses condition_id param, not id
                resp = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"condition_id": self._watcher.market_id},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    market = data[0] if isinstance(data, list) and data else None
                    if market:
                        return self._parse_price_from_market(market)

                # Fallback: search by question text
                q = self._watcher.question
                if q:
                    resp = await client.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"_q": q[:50], "closed": "false", "_limit": 3},
                    )
                    if resp.status_code == 200:
                        for m in resp.json():
                            if self._watcher.market_id in (
                                m.get("conditionId", ""),
                                m.get("id", ""),
                            ):
                                return self._parse_price_from_market(m)
        except Exception:
            pass
        return 0.0

    def _parse_price_from_market(self, market: dict) -> float:
        """Extract price for our outcome from a Gamma API market dict."""
        prices = market.get("outcomePrices", "[]")
        outcomes = market.get("outcomes", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if outcomes and prices:
            try:
                idx = outcomes.index(self._watcher.outcome)
                price = float(prices[idx])
                if price > 0:
                    self._last_fetched_price = price
                    return price
            except (ValueError, IndexError):
                pass
        return 0.0

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

    async def _verify_position_exists(self) -> None:
        """Check if the position still exists in the DB.

        If the position was closed (phantom sync, manual sell, etc.)
        but the watcher thesis is still valid, request re-entry.
        """
        try:
            async with async_session() as session:
                from sqlalchemy import select

                from bot.data.models import Position

                result = await session.execute(
                    select(Position).where(
                        Position.market_id == self._watcher.market_id,
                        Position.is_open.is_(True),
                    )
                )
                position = result.scalars().first()

                if position is None and self._watcher.current_exposure > 0:
                    logger.warning(
                        "watcher_position_lost",
                        watcher_id=self.watcher_id,
                        market_id=self._watcher.market_id,
                    )
                    # Mark exposure as zero — re-entry only if signals confirm
                    self._watcher.current_exposure = 0.0
                    self._watcher.scale_count = max(0, self._watcher.scale_count - 1)
                    self._position_lost = True
        except Exception as e:
            logger.debug("watcher_position_check_failed", error=str(e))

    async def _persist_state(self) -> None:
        """Persist watcher state to DB so dashboard shows live data."""
        try:
            async with async_session() as session:
                await session.merge(self._watcher)
                await session.commit()
        except Exception as e:
            logger.debug("watcher_persist_failed", error=str(e))

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

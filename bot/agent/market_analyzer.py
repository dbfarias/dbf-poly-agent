"""Market scanner and analyzer for identifying trading opportunities."""

import re
from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import MarketScan
from bot.data.repositories import MarketScanRepository, PositionRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.types import GammaMarket, TradeSignal

from .strategies.base import BaseStrategy

logger = structlog.get_logger()


class MarketAnalyzer:
    """Scans markets and runs strategies to find opportunities."""

    # Quality filter thresholds
    MAX_SPREAD = 0.04  # 4 cents max spread
    MAX_CATEGORY_POSITIONS = 2  # Max pending+open per category

    def __init__(
        self,
        gamma_client: GammaClient,
        cache: MarketCache,
        strategies: list[BaseStrategy],
        clob_client: PolymarketClient | None = None,
    ):
        self.gamma = gamma_client
        self.cache = cache
        self.strategies = strategies
        self.clob = clob_client

    async def scan_markets(self, tier: CapitalTier) -> list[TradeSignal]:
        """Scan all markets and return ranked signals from all enabled strategies."""
        # Fetch active markets
        try:
            markets = await self.gamma.get_active_markets(limit=200)
            self.cache.set_markets_bulk(markets, ttl=120)
            logger.info("markets_fetched", count=len(markets))
        except Exception as e:
            logger.error("market_fetch_failed", error=str(e))
            markets = self.cache.get_all_markets()
            if not markets:
                return []

        # Apply quality filter before strategy evaluation
        markets = await self._filter_quality(markets)

        # Run enabled strategies
        all_signals: list[TradeSignal] = []
        for strategy in self.strategies:
            if not strategy.is_enabled_for_tier(tier):
                continue

            try:
                signals = await strategy.scan(markets)
                all_signals.extend(signals)
                logger.info(
                    "strategy_scan_complete",
                    strategy=strategy.name,
                    signals=len(signals),
                )
            except Exception as e:
                logger.error(
                    "strategy_scan_failed",
                    strategy=strategy.name,
                    error=str(e),
                )

        # Record scan results
        await self._record_scans(markets, all_signals)

        # Deduplicate correlated markets — keep only the best signal per group
        all_signals = self._deduplicate_correlated(all_signals)

        # Rank signals by edge * confidence
        all_signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
        return all_signals

    # Universal stop-loss thresholds
    STOP_LOSS_PCT = 0.40  # Exit if lost 40%+ of entry price
    NEAR_WORTHLESS_PRICE = 0.10  # Always exit below 10 cents
    DEFAULT_EXIT_PRICE = 0.70  # Fallback exit for unmatched strategies

    async def check_exits(
        self, positions: list, tier: CapitalTier
    ) -> list[str]:
        """Check if any open positions should be exited. Returns market IDs to exit."""
        exits = []
        for position in positions:
            # 1. Strategy-specific exit check
            strategy_matched = False
            for strategy in self.strategies:
                if strategy.name == position.strategy:
                    strategy_matched = True
                    try:
                        should_exit = await strategy.should_exit(
                            position.market_id, position.current_price
                        )
                        if should_exit:
                            exits.append(position.market_id)
                            logger.info(
                                "exit_signal",
                                strategy=strategy.name,
                                market_id=position.market_id,
                            )
                    except Exception as e:
                        logger.error(
                            "exit_check_failed",
                            strategy=strategy.name,
                            market_id=position.market_id,
                            error=str(e),
                        )
                    break

            # 2. Universal stop-loss for ALL positions (including unmatched strategies)
            if position.market_id not in exits:
                exit_reason = self._check_stop_loss(position, strategy_matched)
                if exit_reason:
                    exits.append(position.market_id)
                    logger.info(
                        "stop_loss_exit",
                        market_id=position.market_id,
                        strategy=position.strategy,
                        reason=exit_reason,
                        avg_price=round(position.avg_price, 4),
                        current_price=round(position.current_price, 4),
                    )
        return exits

    def _check_stop_loss(self, position, strategy_matched: bool) -> str | None:
        """Universal stop-loss check. Returns exit reason or None."""
        # Near-worthless: always exit
        if position.current_price < self.NEAR_WORTHLESS_PRICE:
            return f"near_worthless (price={position.current_price:.4f})"

        # Loss exceeds stop-loss threshold
        if position.avg_price > 0:
            loss_pct = (
                (position.avg_price - position.current_price) / position.avg_price
            )
            if loss_pct >= self.STOP_LOSS_PCT:
                return f"stop_loss ({loss_pct:.0%} loss)"

        # Unmatched strategy: apply default exit threshold
        if not strategy_matched and position.current_price < self.DEFAULT_EXIT_PRICE:
            return f"unmatched_strategy_exit (price={position.current_price:.4f})"

        return None

    async def _filter_quality(
        self, markets: list[GammaMarket]
    ) -> list[GammaMarket]:
        """Filter markets by quality before passing to strategies.

        Checks: binary-only, token IDs present, order book depth/spread,
        and category diversification.
        """
        # Get current open positions per category for diversification check
        category_counts: dict[str, int] = {}
        try:
            async with async_session() as session:
                pos_repo = PositionRepository(session)
                open_positions = await pos_repo.get_open()
                for pos in open_positions:
                    cat = pos.category or "Other"
                    category_counts[cat] = category_counts.get(cat, 0) + 1
        except Exception as e:
            logger.warning("quality_filter_position_fetch_failed", error=str(e))

        quality: list[GammaMarket] = []
        filtered_reasons: dict[str, int] = {}

        for market in markets:
            # Binary markets only (2 outcomes)
            if len(market.outcomes) != 2:
                filtered_reasons["not_binary"] = (
                    filtered_reasons.get("not_binary", 0) + 1
                )
                continue

            # Must have token IDs for both outcomes
            if not market.token_ids or len(market.token_ids) < 2:
                filtered_reasons["no_token_ids"] = (
                    filtered_reasons.get("no_token_ids", 0) + 1
                )
                continue

            # Category diversification check
            cat = market.category or "Other"
            if category_counts.get(cat, 0) >= self.MAX_CATEGORY_POSITIONS:
                filtered_reasons["category_limit"] = (
                    filtered_reasons.get("category_limit", 0) + 1
                )
                continue

            # Order book quality check (requires CLOB client)
            if self.clob:
                try:
                    book = await self.clob.get_order_book(market.token_ids[0])
                    if not book.bids or not book.asks:
                        filtered_reasons["no_liquidity"] = (
                            filtered_reasons.get("no_liquidity", 0) + 1
                        )
                        continue
                    if book.spread is not None and book.spread > self.MAX_SPREAD:
                        filtered_reasons["wide_spread"] = (
                            filtered_reasons.get("wide_spread", 0) + 1
                        )
                        continue
                except Exception:
                    # If order book fetch fails, still allow — don't block on API errors
                    pass

            quality.append(market)

        if filtered_reasons:
            logger.info(
                "quality_filter_applied",
                original=len(markets),
                passed=len(quality),
                reasons=filtered_reasons,
            )

        return quality

    async def _record_scans(
        self, markets: list[GammaMarket], signals: list[TradeSignal]
    ) -> None:
        """Record scan results to database."""
        signal_map = {s.market_id: s for s in signals}
        now = datetime.now(timezone.utc)
        scans = []
        for market in markets[:50]:  # Limit to top 50 markets
            signal = signal_map.get(market.id)

            hours_to_resolution = None
            end = market.end_date
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                hours_left = (end - now).total_seconds() / 3600
                if hours_left > 0:
                    hours_to_resolution = round(hours_left, 1)

            scan = MarketScan(
                scanned_at=datetime.utcnow(),
                market_id=market.id,
                question=market.question[:200],
                category=market.category or "",
                yes_price=market.yes_price or 0.0,
                no_price=market.no_price or 0.0,
                volume=market.volume,
                liquidity=market.liquidity,
                end_date=market.end_date,
                hours_to_resolution=hours_to_resolution,
                signal_strategy=signal.strategy if signal else "",
                signal_edge=signal.edge if signal else 0.0,
                signal_confidence=signal.confidence if signal else 0.0,
            )
            scans.append(scan)

        try:
            async with async_session() as session:
                repo = MarketScanRepository(session)
                await repo.create_batch(scans)
        except Exception as e:
            logger.error("scan_record_failed", error=str(e))

    @staticmethod
    def _question_group_key(question: str) -> str:
        """Extract a group key from a question to detect mutually exclusive markets.

        E.g. "Will Albert Littell be the Democratic nominee for Senate in Mississippi?"
        and  "Will Scott Colom be the Democratic nominee for Senate in Mississippi?"
        both map to "be the democratic nominee for senate in mississippi".
        """
        q = question.lower().strip().rstrip("?")
        # Remove "will <name>" prefix — name is 1-4 words before a common verb/preposition
        q = re.sub(r"^will\s+[\w\s]{1,60}?\s+(be\s+)", r"\1", q)
        # Remove leading articles
        q = re.sub(r"^(the|a|an)\s+", "", q)
        return q.strip()

    def _deduplicate_correlated(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        """Keep only the best signal per group of mutually exclusive markets."""
        if not signals:
            return signals

        groups: dict[str, TradeSignal] = {}
        for signal in signals:
            key = self._question_group_key(signal.question)
            existing = groups.get(key)
            if existing is None or (signal.edge * signal.confidence) > (
                existing.edge * existing.confidence
            ):
                if existing is not None:
                    logger.info(
                        "correlated_market_filtered",
                        kept=signal.question[:60],
                        dropped=existing.question[:60],
                        group_key=key[:40],
                    )
                groups[key] = signal

        filtered = list(groups.values())
        dropped = len(signals) - len(filtered)
        if dropped > 0:
            logger.info("correlated_dedup_complete", original=len(signals), kept=len(filtered))
        return filtered

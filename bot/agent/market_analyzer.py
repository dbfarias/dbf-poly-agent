"""Market scanner and analyzer for identifying trading opportunities."""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import MarketScan
from bot.data.repositories import MarketScanRepository
from bot.polymarket.gamma import GammaClient
from bot.polymarket.types import GammaMarket, TradeSignal

from .strategies.base import BaseStrategy

logger = structlog.get_logger()


class MarketAnalyzer:
    """Scans markets and runs strategies to find opportunities."""

    def __init__(
        self,
        gamma_client: GammaClient,
        cache: MarketCache,
        strategies: list[BaseStrategy],
    ):
        self.gamma = gamma_client
        self.cache = cache
        self.strategies = strategies

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

        # Rank signals by edge * confidence
        all_signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
        return all_signals

    async def check_exits(
        self, positions: list, tier: CapitalTier
    ) -> list[str]:
        """Check if any open positions should be exited. Returns market IDs to exit."""
        exits = []
        for position in positions:
            for strategy in self.strategies:
                if strategy.name == position.strategy:
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
        return exits

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

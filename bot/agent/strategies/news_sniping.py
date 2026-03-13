"""News sniping strategy: trade on breaking news before price moves.

Zero LLM cost — uses keyword overlap (Jaccard) + VADER sentiment from
NewsSniper to generate trade signals when headlines match open markets.
"""

from datetime import datetime, timezone

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal
from bot.research.news_sniper import NewsSniper, SnipeCandidate

from .base import BaseStrategy

logger = structlog.get_logger()

# Strategy constants
MAX_SIGNALS_PER_CYCLE = 3
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = 0.05
MAX_HOLD_HOURS = 24


class NewsSniperStrategy(BaseStrategy):
    """Trade breaking news: positive sentiment -> BUY Yes, negative -> BUY No."""

    name = "news_sniping"
    MIN_HOLD_SECONDS = 600  # 10 min minimum hold

    # Tunable params
    MIN_EDGE = 0.02
    MAX_EDGE = 0.10
    EDGE_SCALE = 0.15
    TAKE_PROFIT_PCT = TAKE_PROFIT_PCT
    STOP_LOSS_PCT = STOP_LOSS_PCT
    MAX_HOLD_HOURS = MAX_HOLD_HOURS
    MAX_SIGNALS_PER_CYCLE = MAX_SIGNALS_PER_CYCLE
    TAKE_PROFIT_MIN_HOLD_HOURS = 1.0

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.15},
        "MAX_EDGE": {"type": float, "min": 0.01, "max": 0.30},
        "EDGE_SCALE": {"type": float, "min": 0.01, "max": 0.50},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.005, "max": 0.10},
        "STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.15},
        "MAX_HOLD_HOURS": {"type": float, "min": 1, "max": 72},
        "MAX_SIGNALS_PER_CYCLE": {"type": int, "min": 1, "max": 10},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 3600},
        "TAKE_PROFIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 24.0},
    }

    def __init__(self, *args, news_sniper: NewsSniper | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._news_sniper = news_sniper

    def _candidate_to_signal(
        self, candidate: SnipeCandidate, market: GammaMarket,
    ) -> TradeSignal | None:
        """Convert a SnipeCandidate into a TradeSignal."""
        token_ids = market.token_ids
        if not token_ids:
            return None

        # Edge = abs(sentiment) * keyword_overlap * EDGE_SCALE, capped
        raw_edge = abs(candidate.sentiment) * candidate.keyword_overlap * self.EDGE_SCALE
        edge = min(raw_edge, self.MAX_EDGE)

        if edge < self.MIN_EDGE:
            return None

        # Direction: positive sentiment -> BUY Yes, negative -> BUY No
        if candidate.sentiment > 0:
            side = OrderSide.BUY
            outcome = "Yes"
            token_id = token_ids[0]
            price = candidate.yes_price
        else:
            if len(token_ids) < 2:
                return None
            side = OrderSide.BUY
            outcome = "No"
            token_id = token_ids[1]
            price = market.no_price or (1.0 - candidate.yes_price)

        estimated_prob = min(0.95, price + edge)

        # Confidence: base 0.4, boosted by overlap and sentiment strength
        confidence = 0.4 + candidate.keyword_overlap * 0.3 + abs(candidate.sentiment) * 0.2
        confidence = min(0.90, confidence)

        return TradeSignal(
            strategy=self.name,
            market_id=candidate.market_id,
            token_id=token_id,
            question=candidate.question,
            side=side,
            outcome=outcome,
            estimated_prob=estimated_prob,
            market_price=price,
            edge=edge,
            size_usd=0.0,  # Sized by risk manager
            confidence=confidence,
            reasoning=(
                f"News snipe: {outcome} at ${price:.3f}. "
                f"Headline: \"{candidate.headline[:80]}\". "
                f"Sentiment: {candidate.sentiment:+.2f}, "
                f"overlap: {candidate.keyword_overlap:.0%}, "
                f"source: {candidate.source}"
            ),
            metadata={
                "headline": candidate.headline,
                "source": candidate.source,
                "sentiment": candidate.sentiment,
                "keyword_overlap": candidate.keyword_overlap,
                "published": candidate.published.isoformat(),
            },
        )

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for news sniping opportunities from cached candidates."""
        if self._news_sniper is None:
            return []

        candidates = self._news_sniper.get_candidates()
        if not candidates:
            return []

        # Build market lookup
        market_map = {m.id: m for m in markets}

        signals: list[TradeSignal] = []
        for candidate in candidates:
            market = market_map.get(candidate.market_id)
            if market is None:
                continue

            signal = self._candidate_to_signal(candidate, market)
            if signal is not None:
                signals.append(signal)

        # Sort by edge descending, cap at max signals
        signals.sort(key=lambda s: s.edge, reverse=True)
        signals = signals[: self.MAX_SIGNALS_PER_CYCLE]

        if signals:
            self.logger.info(
                "news_sniping_scan_complete",
                candidates=len(candidates),
                signals_found=len(signals),
            )

        return signals

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs,
    ) -> str | bool:
        """Exit on take-profit (after 1h), stop-loss, or max hold time."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        now = datetime.now(timezone.utc)
        held_hours = 0.0
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_hours = (now - created_at).total_seconds() / 3600

        # Stop-loss: immediate
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.STOP_LOSS_PCT:
                self.logger.warning(
                    "news_snipe_exit_stop_loss",
                    market_id=market_id,
                    loss_pct=f"{loss_pct:.1%}",
                )
                return "stop_loss"

        # Take-profit: only after minimum hold
        if avg_price > 0 and held_hours >= self.TAKE_PROFIT_MIN_HOLD_HOURS:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.TAKE_PROFIT_PCT:
                self.logger.info(
                    "news_snipe_exit_take_profit",
                    market_id=market_id,
                    profit_pct=f"{profit_pct:.1%}",
                )
                return "take_profit"

        # Max hold time
        if held_hours >= self.MAX_HOLD_HOURS:
            self.logger.info(
                "news_snipe_exit_max_hold",
                market_id=market_id,
                held_hours=f"{held_hours:.1f}",
            )
            return "max_hold_time"

        return False

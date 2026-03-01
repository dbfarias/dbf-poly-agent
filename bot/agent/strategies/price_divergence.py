"""Price Divergence strategy: micro-trades via external data divergence.

Detects when external data (CoinGecko prices, news sentiment) disagrees with
Polymarket contract prices. Two signal types:

1. Crypto divergence: actual BTC/ETH price vs contract threshold
   e.g. BTC=$102k but "BTC above $100k?" priced at 0.60 → BUY YES

2. Sentiment divergence: news sentiment direction vs price trend
   e.g. sentiment=+0.65 but price falling → BUY YES

Targets 0.3-0.8% profit per trade with tight TP/SL exits.
"""

import re
from collections import deque
from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal
from bot.research.cache import ResearchCache

from .base import BaseStrategy

logger = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────────────────
MIN_DIVERGENCE_PCT = 0.003       # 0.3% minimum divergence
MIN_EDGE = 0.005                 # 0.5% minimum edge
MAX_EDGE = 0.25                  # 25% max edge — reject absurd signals
TAKE_PROFIT_PCT = 0.005          # 0.5% take profit
STOP_LOSS_PCT = 0.010            # 1.0% stop loss
MAX_HOLD_HOURS_CRYPTO = 4        # Fast crypto exit
MAX_HOLD_HOURS_OTHER = 24        # Slower non-crypto exit
MAX_SPREAD = 0.02                # Tighter than global 4¢
MIN_PRICE = 0.10                 # Avoid extreme low
MAX_PRICE = 0.90                 # Avoid extreme high
PRICE_HISTORY_MAXLEN = 20        # Snapshots kept per market
MAX_TRACKED_MARKETS = 500        # Hard ceiling on price history dict size
MAX_MARKET_ID_LEN = 128          # Match DB column width

# Crypto keyword sets for market classification
CRYPTO_KEYWORDS = frozenset({
    "bitcoin", "btc", "ethereum", "eth", "crypto",
    "cryptocurrency", "blockchain",
})

# Maps question keywords to CoinGecko coin IDs
# Order matters: longer keywords checked first to avoid substring matches
# (e.g. "ethereum" before "eth" so "MegaETH" doesn't match "eth")
COIN_KEYWORD_MAP = (
    ("bitcoin", "bitcoin"),
    ("ethereum", "ethereum"),
    ("btc", "bitcoin"),
    ("eth", "ethereum"),
)

# Regex to extract dollar thresholds: "$100,000", "$100k", "$50K", "$3,400", "$2B"
_THRESHOLD_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*([kKmMbBtT])?",
)


class PriceDivergenceStrategy(BaseStrategy):
    """Micro-trades via crypto price and sentiment divergence."""

    name = "price_divergence"
    min_tier = CapitalTier.TIER1

    def __init__(self, *args, research_cache: ResearchCache | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._research_cache = research_cache
        self._price_history: dict[str, deque[float]] = {}

    # ── Public interface ───────────────────────────────────────────────────

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets for price divergence opportunities."""
        self._update_price_history(markets)

        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        for market in markets:
            signal = self._evaluate_market(market, now)
            if signal is not None:
                signals.append(signal)

        # Sort by divergence strength (edge)
        signals.sort(key=lambda s: s.edge, reverse=True)

        self.logger.info(
            "price_divergence_scan_complete",
            signals_found=len(signals),
            tracked_markets=len(self._price_history),
        )
        return signals

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs
    ) -> bool:
        """Exit on take-profit, stop-loss, or time expiry."""
        avg_price = kwargs.get("avg_price")
        created_at = kwargs.get("created_at")

        # Take profit / stop loss
        if isinstance(avg_price, (int, float)) and avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= TAKE_PROFIT_PCT:
                self.logger.info(
                    "divergence_take_profit",
                    market_id=market_id,
                    profit_pct=round(profit_pct, 4),
                )
                return True
            if profit_pct <= -STOP_LOSS_PCT:
                self.logger.info(
                    "divergence_stop_loss",
                    market_id=market_id,
                    loss_pct=round(profit_pct, 4),
                )
                return True

        # Time expiry
        if isinstance(created_at, datetime):
            now = datetime.now(timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_hours = (now - created_at).total_seconds() / 3600
            # Use crypto hold time if market question references crypto
            is_crypto = self._is_crypto_market(kwargs.get("question", ""))
            max_hours = MAX_HOLD_HOURS_CRYPTO if is_crypto else MAX_HOLD_HOURS_OTHER
            if held_hours >= max_hours:
                self.logger.info(
                    "divergence_time_expiry",
                    market_id=market_id,
                    held_hours=round(held_hours, 2),
                    max_hours=max_hours,
                )
                return True

        return False

    # ── Market evaluation ──────────────────────────────────────────────────

    def _evaluate_market(
        self, market: GammaMarket, now: datetime
    ) -> TradeSignal | None:
        """Route market to crypto or sentiment divergence detection."""
        if not market.id or len(market.id) > MAX_MARKET_ID_LEN:
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        yes_price = market.yes_price
        if yes_price is None or yes_price < MIN_PRICE or yes_price > MAX_PRICE:
            return None

        # Spread check
        if (
            market.best_bid_price is not None
            and market.best_ask_price is not None
        ):
            spread = market.best_ask_price - market.best_bid_price
            if spread > MAX_SPREAD:
                return None
        else:
            return None

        # Try crypto divergence first (higher confidence)
        if self._is_crypto_market(market.question):
            signal = self._detect_crypto_divergence(market)
            if signal is not None:
                return signal

        # Fall back to sentiment divergence
        return self._detect_sentiment_divergence(market)

    # ── Crypto divergence ──────────────────────────────────────────────────

    @staticmethod
    def _is_crypto_market(question: str) -> bool:
        """Check if a market question is crypto-related.

        Uses word-boundary matching to avoid 'MegaETH' matching 'eth'.
        """
        q_lower = question.lower()
        for kw in CRYPTO_KEYWORDS:
            idx = q_lower.find(kw)
            if idx == -1:
                continue
            # Word boundary: char before must be non-alpha (or start of string)
            if idx > 0 and q_lower[idx - 1].isalnum():
                continue
            # Char after must be non-alpha (or end of string)
            end_idx = idx + len(kw)
            if end_idx < len(q_lower) and q_lower[end_idx].isalnum():
                continue
            return True
        return False

    def _detect_crypto_divergence(
        self, market: GammaMarket
    ) -> TradeSignal | None:
        """Compare actual crypto price vs contract threshold.

        Algorithm:
        1. Extract threshold: "Will BTC be above $100k?" → ("bitcoin", 100000)
        2. Get actual price from research cache → 102000
        3. distance_pct = (102000 - 100000) / 100000 = 2%
        4. estimated_prob via sigmoid-like mapping calibrated to crypto volatility
        5. edge = estimated_prob - contract_price
        6. If edge > MIN_EDGE → TradeSignal(BUY YES)
        """
        if self._research_cache is None:
            return None

        question = market.question
        coin_id, threshold = self._extract_crypto_target(question)
        if coin_id is None or threshold is None or threshold <= 0:
            return None

        # Look up actual price from any cached research result
        actual_price = self._get_crypto_price(coin_id)
        if actual_price is None or actual_price <= 0:
            return None

        # Calculate distance from threshold
        distance_pct = (actual_price - threshold) / threshold

        # Estimate probability: moderate curve calibrated to crypto volatility.
        # 5x amplification with ±0.20 cap produces actionable edges (5-20%).
        # The old 10x with ±0.45 made ALL crypto signals either edge=0
        # (tiny divergence) or edge>15% (rejected as absurd).
        estimated_prob = 0.5 + min(0.20, max(-0.20, distance_pct * 5))

        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Determine direction
        if estimated_prob > yes_price:
            # YES is underpriced
            edge = estimated_prob - yes_price
            side = OrderSide.BUY
            token_id = market.token_ids[0]
            outcome = "Yes"
            price = yes_price
        elif estimated_prob < (1 - yes_price):
            # NO is underpriced (contract overprices YES)
            if len(market.token_ids) < 2:
                return None
            no_price = market.no_price or (1.0 - yes_price)
            edge = (1 - estimated_prob) - no_price
            side = OrderSide.BUY
            token_id = market.token_ids[1]
            outcome = "No"
            price = no_price
        else:
            return None

        if edge < MIN_EDGE:
            return None

        # Reject absurd edges — likely a parsing/matching error
        if edge > MAX_EDGE:
            self.logger.warning(
                "crypto_divergence_edge_too_high",
                market_id=market.id,
                coin_id=coin_id,
                edge=round(edge, 4),
                actual_price=actual_price,
                threshold=threshold,
            )
            return None

        confidence = min(0.90, 0.65 + abs(distance_pct) * 2)

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=side,
            outcome=outcome,
            estimated_prob=min(0.95, estimated_prob if outcome == "Yes" else 1 - estimated_prob),
            market_price=price,
            edge=edge,
            size_usd=0.0,
            confidence=confidence,
            reasoning=(
                f"Crypto divergence: {coin_id} actual=${actual_price:,.0f} "
                f"vs threshold=${threshold:,.0f} "
                f"(distance={distance_pct:+.1%}). "
                f"{outcome} at ${price:.3f}, edge={edge:.3f}"
            ),
            metadata={
                "category": market.category,
                "hours_to_resolution": MAX_HOLD_HOURS_CRYPTO,
                "divergence_type": "crypto",
                "coin_id": coin_id,
                "actual_price": actual_price,
                "threshold": threshold,
                "distance_pct": distance_pct,
            },
        )

    def _extract_crypto_target(
        self, question: str
    ) -> tuple[str | None, float | None]:
        """Extract coin ID and price threshold from a market question.

        Examples:
        - "Will BTC be above $100,000?" → ("bitcoin", 100000.0)
        - "Will Bitcoin exceed $100k?"  → ("bitcoin", 100000.0)
        - "ETH above $3,400?"          → ("ethereum", 3400.0)

        Uses word-boundary matching to avoid false positives like
        "MegaETH" matching "eth".
        """
        q_lower = question.lower()

        # Find which coin — word boundary check prevents "MegaETH" → "eth"
        coin_id = None
        for keyword, cid in COIN_KEYWORD_MAP:
            idx = q_lower.find(keyword)
            if idx == -1:
                continue
            # Word boundary: char before must be non-alphanumeric (or start of string)
            if idx > 0 and q_lower[idx - 1].isalnum():
                continue
            # Char after must be non-alphanumeric (or end of string)
            end_idx = idx + len(keyword)
            if end_idx < len(q_lower) and q_lower[end_idx].isalnum():
                continue
            coin_id = cid
            break

        if coin_id is None:
            return None, None

        # Extract threshold
        threshold = _extract_price_threshold(question)
        return coin_id, threshold

    def _get_crypto_price(self, coin_id: str) -> float | None:
        """Look up actual crypto price from any cached research result."""
        if self._research_cache is None:
            return None

        for result in self._research_cache.get_all():
            for coin, price in result.crypto_prices:
                if coin == coin_id and price > 0:
                    return price
        return None

    # ── Sentiment divergence ───────────────────────────────────────────────

    def _detect_sentiment_divergence(
        self, market: GammaMarket
    ) -> TradeSignal | None:
        """Compare news sentiment direction vs market price trend.

        Algorithm:
        1. sentiment_score = +0.65 (positive news), price_trend = -0.25 (falling)
        2. Opposing directions → divergence detected
        3. divergence_pct = |sentiment| × |trend| × 0.1
        4. edge = divergence_pct × confidence
        5. If edge > MIN_EDGE → TradeSignal
        """
        if self._research_cache is None:
            return None

        research = self._research_cache.get(market.id)
        if research is None:
            return None

        sentiment = research.sentiment_score
        confidence = research.confidence

        if abs(sentiment) < 0.1 or confidence < 0.3:
            return None

        price_trend = self._get_price_trend(market.id)
        if abs(price_trend) < 0.05:
            return None

        # Divergence: sentiment and trend point in opposite directions
        if sentiment * price_trend >= 0:
            # Same direction — no divergence
            return None

        divergence_pct = abs(sentiment) * abs(price_trend) * 0.1
        if divergence_pct < MIN_DIVERGENCE_PCT:
            return None

        edge = divergence_pct * confidence
        if edge < MIN_EDGE:
            return None

        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Sentiment positive + price falling → undervalued → BUY YES
        # Sentiment negative + price rising → overvalued → BUY NO
        if sentiment > 0:
            side = OrderSide.BUY
            token_id = market.token_ids[0]
            outcome = "Yes"
            price = yes_price
            estimated_prob = min(0.95, yes_price + edge)
        else:
            if len(market.token_ids) < 2:
                return None
            side = OrderSide.BUY
            no_price = market.no_price or (1.0 - yes_price)
            token_id = market.token_ids[1]
            outcome = "No"
            price = no_price
            estimated_prob = min(0.95, no_price + edge)

        signal_confidence = min(0.85, 0.55 + confidence * 0.2 + divergence_pct * 5)

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=side,
            outcome=outcome,
            estimated_prob=estimated_prob,
            market_price=price,
            edge=edge,
            size_usd=0.0,
            confidence=signal_confidence,
            reasoning=(
                f"Sentiment divergence: sentiment={sentiment:+.2f}, "
                f"trend={price_trend:+.2f} (opposing). "
                f"Divergence={divergence_pct:.3f}, "
                f"{outcome} at ${price:.3f}"
            ),
            metadata={
                "category": market.category,
                "hours_to_resolution": MAX_HOLD_HOURS_OTHER,
                "divergence_type": "sentiment",
                "sentiment_score": sentiment,
                "price_trend": price_trend,
                "divergence_pct": divergence_pct,
            },
        )

    # ── Price history & trend ──────────────────────────────────────────────

    def _update_price_history(self, markets: list[GammaMarket]) -> None:
        """Update price snapshots; evict stale entries to prevent unbounded growth."""
        active_ids = {
            m.id
            for m in markets
            if m.id and m.best_bid_price is not None and m.best_bid_price > 0
        }

        # Evict markets no longer in current scan
        stale = [mid for mid in self._price_history if mid not in active_ids]
        for mid in stale:
            del self._price_history[mid]

        for market in markets:
            mid = market.id
            if not mid or len(mid) > MAX_MARKET_ID_LEN:
                continue
            if market.best_bid_price is None or market.best_bid_price <= 0:
                continue
            if mid not in self._price_history:
                if len(self._price_history) >= MAX_TRACKED_MARKETS:
                    continue
                self._price_history[mid] = deque(maxlen=PRICE_HISTORY_MAXLEN)
            self._price_history[mid].append(market.best_bid_price)

    def _get_price_trend(self, market_id: str) -> float:
        """Compute linear price trend from history, normalized to [-1, 1].

        Uses simple slope: (last - first) / first, clamped to [-1, 1].
        Returns 0.0 if insufficient data.
        """
        history = self._price_history.get(market_id)
        if not history or len(history) < 3:
            return 0.0

        prices = list(history)
        first = prices[0]
        last = prices[-1]
        if first <= 0:
            return 0.0

        trend = (last - first) / first
        return max(-1.0, min(1.0, trend))


def _extract_price_threshold(question: str) -> float | None:
    """Extract a dollar price threshold from a question string.

    Handles formats: "$100,000", "$100k", "$100K", "$3,400.50", "$50M"
    """
    match = _THRESHOLD_RE.search(question)
    if not match:
        return None

    raw_number = match.group(1).replace(",", "")
    try:
        value = float(raw_number)
    except ValueError:
        return None

    suffix = match.group(2)
    if suffix:
        suffix_lower = suffix.lower()
        if suffix_lower == "k":
            value *= 1_000
        elif suffix_lower == "m":
            value *= 1_000_000
        elif suffix_lower == "b":
            value *= 1_000_000_000
        elif suffix_lower == "t":
            value *= 1_000_000_000_000

    return value

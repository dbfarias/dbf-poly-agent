"""Crypto short-term strategy: exploit 5-min/15-min crypto prediction markets.

Uses real-time spot price momentum from Coinbase WebSocket combined with
Polymarket orderbook imbalance to predict short-term crypto price direction.
Inspired by high-frequency Polymarket bots (Gabagool et al).
"""

import re
from datetime import datetime, timezone

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Match crypto 5-min/15-min market questions
_CRYPTO_SHORT_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b.*\b(5[\s-]*min|15[\s-]*min|five[\s-]*minute|fifteen[\s-]*minute)\b"
    r"|\b(5[\s-]*min|15[\s-]*min|five[\s-]*minute|fifteen[\s-]*minute)\b.*\b(bitcoin|btc|ethereum|eth|solana|sol)\b",
    re.IGNORECASE,
)

# Polymarket real format: "Bitcoin Up or Down - March 10, 12:05PM-12:10PM ET"
# Detect crypto keyword + "up or down" pattern (always short-term)
_CRYPTO_UPDOWN_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b.*\b(up or down)\b"
    r"|\b(up or down)\b.*\b(bitcoin|btc|ethereum|eth|solana|sol)\b",
    re.IGNORECASE,
)

# Map question keywords to Coinbase symbols
_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTC-USD",
    "btc": "BTC-USD",
    "ethereum": "ETH-USD",
    "eth": "ETH-USD",
    "solana": "SOL-USD",
    "sol": "SOL-USD",
}

# Regex for extracting crypto keyword from question
_CRYPTO_KEYWORD_RE = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b",
    re.IGNORECASE,
)


class CryptoShortTermStrategy(BaseStrategy):
    """Trade 5-min/15-min crypto prediction markets using spot momentum + orderbook."""

    name = "crypto_short_term"
    MIN_HOLD_SECONDS = 30

    MIN_EDGE = 0.02
    IMBALANCE_WEIGHT = 0.40
    SPOT_MOMENTUM_WEIGHT = 0.40
    VOLUME_ANOMALY_WEIGHT = 0.20
    TAKE_PROFIT_PCT = 0.30  # 30% gain → sell (was 2%, way too low for binary)
    STOP_LOSS_PCT = 0.03
    SWING_EXIT_PRICE = 0.65  # Sell when price reaches this (lock in gains)
    MAX_CONCURRENT = 3
    MAX_MARKET_MINUTES = 20
    MIN_BOOK_VOLUME = 100.0

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.3},
        "IMBALANCE_WEIGHT": {"type": float, "min": 0.0, "max": 1.0},
        "SPOT_MOMENTUM_WEIGHT": {"type": float, "min": 0.0, "max": 1.0},
        "VOLUME_ANOMALY_WEIGHT": {"type": float, "min": 0.0, "max": 1.0},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.005, "max": 0.50},
        "STOP_LOSS_PCT": {"type": float, "min": 0.005, "max": 0.10},
        "SWING_EXIT_PRICE": {"type": float, "min": 0.60, "max": 0.95},
        "MAX_CONCURRENT": {"type": int, "min": 1, "max": 10},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 600},
        "MAX_MARKET_MINUTES": {"type": int, "min": 1, "max": 60},
        "MIN_BOOK_VOLUME": {"type": float, "min": 0.0, "max": 5000.0},
    }

    def __init__(self, *args, spot_ws=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._spot_ws = spot_ws

    @staticmethod
    def _extract_symbol(question: str) -> str | None:
        """Extract Coinbase symbol from a market question.

        Returns e.g. "BTC-USD" or None if no crypto keyword found.
        """
        match = _CRYPTO_KEYWORD_RE.search(question)
        if match is None:
            return None
        keyword = match.group(1).lower()
        return _SYMBOL_MAP.get(keyword)

    def _is_crypto_short_term(self, market: GammaMarket) -> bool:
        """Check if a market is a short-term crypto prediction market."""
        question = market.question
        slug = market.slug or ""

        # Must contain a crypto keyword
        has_crypto = _CRYPTO_KEYWORD_RE.search(question) is not None

        # Pattern match: crypto + 5min/15min in question
        pattern_match = _CRYPTO_SHORT_PATTERN.search(question) is not None

        # Pattern match: "Bitcoin Up or Down" (Polymarket's actual format for 5-min markets)
        updown_match = _CRYPTO_UPDOWN_PATTERN.search(question) is not None

        # Slug match: e.g. "btc-5min-up"
        slug_match = has_crypto and ("5min" in slug or "15min" in slug)

        if not pattern_match and not updown_match and not slug_match:
            return False

        # Must resolve within MAX_MARKET_MINUTES
        end = market.end_date
        if end is None:
            return False
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        minutes_left = (end - datetime.now(timezone.utc)).total_seconds() / 60
        return 0 < minutes_left <= self.MAX_MARKET_MINUTES

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for crypto short-term trading opportunities."""
        if self._spot_ws is None:
            return []

        signals: list[TradeSignal] = []

        for market in markets:
            if not self._is_crypto_short_term(market):
                continue

            signal = await self._evaluate_market(market)
            if signal is not None:
                signals.append(signal)

        # Sort by absolute combined score (strongest signals first)
        signals.sort(key=lambda s: s.edge, reverse=True)

        # Limit concurrent signals
        signals = signals[: self.MAX_CONCURRENT]

        self.logger.info(
            "crypto_short_term_scan_complete",
            candidates=sum(1 for m in markets if self._is_crypto_short_term(m)),
            signals_found=len(signals),
        )
        return signals

    async def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a single crypto short-term market."""
        symbol = self._extract_symbol(market.question)
        if symbol is None:
            return None

        # Get spot price data from WebSocket
        spot_price = self._spot_ws.get_price(symbol)
        momentum = self._spot_ws.get_momentum(symbol)
        if spot_price is None or momentum is None:
            return None

        # Get orderbook
        token_ids = market.token_ids
        if not token_ids:
            return None

        try:
            book = await self.get_order_book(token_ids[0])
        except Exception:
            return None

        if not book.bids or not book.asks:
            return None

        bid_volume = sum(b.size for b in book.bids[:5])
        ask_volume = sum(a.size for a in book.asks[:5])
        total_volume = bid_volume + ask_volume

        if total_volume < self.MIN_BOOK_VOLUME:
            return None

        # Compute orderbook imbalance: -1 to +1
        imbalance = (bid_volume - ask_volume) / total_volume

        # Normalize momentum: cap at +/-5%, scale to +/-1
        capped_momentum = max(-0.05, min(0.05, momentum))
        normalized_momentum = capped_momentum / 0.05

        # Compute weighted components
        spot_component = normalized_momentum * self.SPOT_MOMENTUM_WEIGHT
        book_component = imbalance * self.IMBALANCE_WEIGHT

        combined = spot_component + book_component

        if abs(combined) < self.MIN_EDGE:
            return None

        # Determine side: positive combined -> BUY YES, negative -> BUY NO
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if combined > 0:
            side = OrderSide.BUY
            outcome = "Yes"
            token_id = token_ids[0]
            price = yes_price
        else:
            if len(token_ids) < 2:
                return None
            side = OrderSide.BUY
            outcome = "No"
            token_id = token_ids[1]
            price = market.no_price or (1.0 - yes_price)

        edge = abs(combined)
        estimated_prob = min(0.95, price + edge)

        # Confidence: base 0.5, boosted by signal agreement
        confidence = 0.5
        # Bonus if momentum and imbalance agree in direction
        if (normalized_momentum > 0 and imbalance > 0) or (
            normalized_momentum < 0 and imbalance < 0
        ):
            confidence += 0.20
        confidence = min(0.95, confidence)

        end = market.end_date
        minutes_left = 0.0
        if end is not None:
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            minutes_left = (end - datetime.now(timezone.utc)).total_seconds() / 60

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
            confidence=confidence,
            reasoning=(
                f"Crypto short-term: {outcome} at ${price:.3f}. "
                f"Spot momentum: {momentum:+.4f} ({symbol}), "
                f"book imbalance: {imbalance:+.1%}. "
                f"Combined: {combined:+.3f}, {minutes_left:.0f}min to resolve"
            ),
            metadata={
                "symbol": symbol,
                "spot_price": spot_price,
                "momentum": momentum,
                "imbalance": imbalance,
                "combined_score": combined,
                "minutes_to_resolution": minutes_left,
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
            },
        )

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs
    ) -> str | bool:
        """Exit on stop-loss, swing exit, take-profit, or time expiry."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        # Stop-loss
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.STOP_LOSS_PCT:
                self.logger.warning(
                    "crypto_short_exit_stop_loss",
                    market_id=market_id,
                    loss_pct=f"{loss_pct:.1%}",
                )
                return "stop_loss"

        # Swing exit: sell when price is high enough to lock in gains
        # instead of waiting for resolution where we might lose everything.
        # E.g. bought at $0.50, price now $0.80+ → sell for ~60% gain
        # rather than risk $0 if the market resolves against us.
        if current_price >= self.SWING_EXIT_PRICE and avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct > 0:
                self.logger.info(
                    "crypto_short_exit_swing",
                    market_id=market_id,
                    current_price=current_price,
                    avg_price=avg_price,
                    profit_pct=f"{profit_pct:.1%}",
                )
                return "swing_exit"

        # Take-profit (percentage-based, for smaller gains)
        if avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.TAKE_PROFIT_PCT:
                self.logger.info(
                    "crypto_short_exit_take_profit",
                    market_id=market_id,
                    profit_pct=f"{profit_pct:.1%}",
                )
                return "take_profit"

        # Time expiry: held longer than MAX_MARKET_MINUTES
        if created_at is not None:
            now = datetime.now(timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_minutes = (now - created_at).total_seconds() / 60
            if held_minutes >= self.MAX_MARKET_MINUTES:
                self.logger.info(
                    "crypto_short_exit_time_expiry",
                    market_id=market_id,
                    held_minutes=f"{held_minutes:.0f}",
                )
                return "time_expiry"

        return False

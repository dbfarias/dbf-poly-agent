"""Sports Favorite strategy — buy No on the weaker team.

Buys "No" (team does NOT win) on the weaker team in football matches.
Wins on both draw and loss of the weak team. Entry at $0.70-$0.90
(weak team has 10-30% win probability).

Proven: 10/11 wins (91%), +$15 P&L, 22% ROI on manual trades.
"""

import re
from datetime import datetime, timezone

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Pattern: "Will <team> win on YYYY-MM-DD?"
_WIN_ON_PATTERN = re.compile(
    r"Will\s+(.+?)\s+win\s+on\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE,
)


class SportsFavoriteStrategy(BaseStrategy):
    """Buy No on the weaker team in football (soccer) matches."""

    name = "sports_favorite"

    # Price range for the No token (weak team has 10-30% win chance)
    MIN_NO_PRICE = 0.70
    MAX_NO_PRICE = 0.90

    # Time window relative to resolution
    MIN_HOURS_TO_RESOLUTION = 1.0
    MAX_HOURS_TO_RESOLUTION = 12.0

    # Market quality filters
    MIN_VOLUME = 5000.0

    # Position management
    MIN_HOLD_SECONDS = 300  # 5 min — let it settle
    TAKE_PROFIT_PCT = 0.15  # 15% profit target
    STOP_LOSS_PCT = 0.25    # 25% stop — generous, these usually resolve at 1.00

    _MUTABLE_PARAMS = {
        "MIN_NO_PRICE": {"type": float, "min": 0.50, "max": 0.95},
        "MAX_NO_PRICE": {"type": float, "min": 0.60, "max": 0.99},
        "MIN_HOURS_TO_RESOLUTION": {"type": float, "min": 0.0, "max": 24.0},
        "MAX_HOURS_TO_RESOLUTION": {"type": float, "min": 1.0, "max": 72.0},
        "MIN_VOLUME": {"type": float, "min": 0.0, "max": 100_000.0},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 7200},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.50},
        "STOP_LOSS_PCT": {"type": float, "min": 0.05, "max": 0.50},
    }

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for weak-team No tokens in football match markets."""
        signals: list[TradeSignal] = []

        for market in markets:
            signal = self._evaluate_market(market)
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals

    def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a single market for a sports_favorite signal."""
        if not market.active or not market.accepting_orders:
            return None

        match = _WIN_ON_PATTERN.search(market.question)
        if match is None:
            return None

        team_name = match.group(1).strip()

        if not self._passes_time_filter(market):
            return None

        if market.volume < self.MIN_VOLUME:
            return None

        return self._build_signal(market, team_name)

    def _passes_time_filter(self, market: GammaMarket) -> bool:
        """Check if market resolves within the allowed time window."""
        end = market.end_date
        if end is None:
            return False

        now = datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        hours_left = (end - now).total_seconds() / 3600.0
        return self.MIN_HOURS_TO_RESOLUTION <= hours_left <= self.MAX_HOURS_TO_RESOLUTION

    def _build_signal(
        self, market: GammaMarket, team_name: str,
    ) -> TradeSignal | None:
        """Build a TradeSignal for buying No on the weak team."""
        no_price = market.no_price
        if no_price is None:
            return None

        if not (self.MIN_NO_PRICE <= no_price <= self.MAX_NO_PRICE):
            return None

        token_ids = market.token_ids
        if len(token_ids) < 2:
            return None

        # No token is index 1 (outcomes = ["Yes", "No"])
        no_token_id = token_ids[1]

        # Expected to resolve near 1.00 (weak team usually loses or draws)
        estimated_prob = min(no_price + 0.10, 0.99)
        edge = (estimated_prob - no_price) / no_price
        yes_price = market.yes_price or (1.0 - no_price)

        reasoning = (
            f"Sports favorite: buying No on {team_name} at ${no_price:.2f} "
            f"(team has {yes_price * 100:.0f}% win chance). "
            f"Win on draw or loss."
        )

        # Scale confidence with distance from sweet spot (0.75-0.85).
        # Peak confidence at center of sweet spot, lower at range edges.
        sweet_spot_center = 0.80
        sweet_spot_half_width = 0.05  # 0.75-0.85 is the sweet spot
        range_half_width = (self.MAX_NO_PRICE - self.MIN_NO_PRICE) / 2.0
        distance = abs(no_price - sweet_spot_center)
        if distance <= sweet_spot_half_width:
            confidence = 0.85
        else:
            # Linear decay from 0.85 to 0.65 as price moves to range edges
            edge_distance = distance - sweet_spot_half_width
            max_edge_distance = range_half_width - sweet_spot_half_width
            decay = edge_distance / max_edge_distance if max_edge_distance > 0 else 0.0
            confidence = 0.85 - 0.20 * min(decay, 1.0)

        self.logger.info(
            "sports_favorite_signal",
            market_id=market.id,
            team=team_name,
            no_price=no_price,
            edge=round(edge, 4),
            confidence=round(confidence, 2),
        )

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=no_token_id,
            question=market.question,
            side=OrderSide.BUY,
            outcome="No",
            estimated_prob=estimated_prob,
            market_price=no_price,
            edge=edge,
            size_usd=0.0,  # Risk manager will size
            confidence=confidence,
            reasoning=reasoning,
        )

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs,
    ) -> bool:
        """Exit on take-profit or stop-loss. Most resolve naturally at 1.00."""
        avg_price = kwargs.get("avg_price", current_price)

        # Take profit
        if current_price >= avg_price * (1.0 + self.TAKE_PROFIT_PCT):
            self.logger.info(
                "sports_favorite_tp",
                market_id=market_id,
                avg_price=avg_price,
                current_price=current_price,
            )
            return True

        # Stop loss
        if current_price <= avg_price * (1.0 - self.STOP_LOSS_PCT):
            self.logger.info(
                "sports_favorite_sl",
                market_id=market_id,
                avg_price=avg_price,
                current_price=current_price,
            )
            return True

        return False

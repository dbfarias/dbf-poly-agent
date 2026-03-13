"""Copy trading strategy: mirror top Polymarket traders' positions.

Zero LLM cost — monitors whale wallets via Data API, copies BUY trades
proportionally scaled to our bankroll. Skips SELL/exit trades and sports.
"""

from datetime import datetime, timezone

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal
from bot.research.whale_tracker import WhaleTracker, WhaleTrade

from .base import BaseStrategy

logger = structlog.get_logger()

# Constants
MAX_COPY_SIGNALS_PER_CYCLE = 2
MAX_CONCURRENT_COPIES = 3
TAKE_PROFIT_PCT = 0.05
STOP_LOSS_PCT = 0.08
MAX_HOLD_HOURS = 72
class CopyTradingStrategy(BaseStrategy):
    """Copy top trader positions: proportional sizing, filtered by quality."""

    name = "copy_trading"
    MIN_HOLD_SECONDS = 1800  # 30 min minimum hold

    # Tunable params
    MIN_EDGE = 0.02
    MIN_COPY_USD = 1.0
    MAX_COPY_USD = 5.0
    WHALE_BANKROLL_ESTIMATE = 10000.0
    BASE_EDGE = 0.03
    WIN_RATE_BONUS_SCALE = 0.10
    MIN_WIN_RATE_THRESHOLD = 0.55
    TAKE_PROFIT_PCT = TAKE_PROFIT_PCT
    STOP_LOSS_PCT = STOP_LOSS_PCT
    MAX_HOLD_HOURS = MAX_HOLD_HOURS
    MAX_COPY_SIGNALS_PER_CYCLE = MAX_COPY_SIGNALS_PER_CYCLE
    MAX_CONCURRENT_COPIES = MAX_CONCURRENT_COPIES
    TAKE_PROFIT_MIN_HOLD_HOURS = 2.0

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.15},
        "MIN_COPY_USD": {"type": float, "min": 0.5, "max": 20.0},
        "MAX_COPY_USD": {"type": float, "min": 1.0, "max": 50.0},
        "WHALE_BANKROLL_ESTIMATE": {"type": float, "min": 1000.0, "max": 100000.0},
        "BASE_EDGE": {"type": float, "min": 0.0, "max": 0.15},
        "WIN_RATE_BONUS_SCALE": {"type": float, "min": 0.0, "max": 0.50},
        "MIN_WIN_RATE_THRESHOLD": {"type": float, "min": 0.3, "max": 0.9},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.15},
        "STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.20},
        "MAX_HOLD_HOURS": {"type": float, "min": 1, "max": 168},
        "MAX_COPY_SIGNALS_PER_CYCLE": {"type": int, "min": 1, "max": 10},
        "MAX_CONCURRENT_COPIES": {"type": int, "min": 1, "max": 10},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 7200},
        "TAKE_PROFIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 48.0},
    }

    def __init__(
        self, *args,
        whale_tracker: WhaleTracker | None = None,
        bankroll_fn=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._whale_tracker = whale_tracker
        self._bankroll_fn = bankroll_fn  # Callable returning current bankroll

    def _compute_copy_size(
        self, whale_size: float, whale_price: float,
    ) -> float:
        """Compute proportional position size, capped to [MIN, MAX]."""
        whale_notional = whale_size * whale_price
        if whale_notional <= 0:
            return 0.0

        # Get our bankroll
        bankroll = 30.0  # Default fallback
        if self._bankroll_fn is not None:
            try:
                bankroll = self._bankroll_fn()
            except Exception:
                pass

        # Proportional: whale_notional * (my_bankroll / whale_bankroll_est)
        ratio = bankroll / self.WHALE_BANKROLL_ESTIMATE
        raw_size = whale_notional * ratio

        return max(self.MIN_COPY_USD, min(self.MAX_COPY_USD, raw_size))

    def _compute_edge(self, win_rate: float) -> float:
        """Compute edge: base + win_rate bonus."""
        bonus = max(0.0, (win_rate - self.MIN_WIN_RATE_THRESHOLD) * self.WIN_RATE_BONUS_SCALE)
        return self.BASE_EDGE + bonus

    def _whale_trade_to_signal(
        self, trade: WhaleTrade, market: GammaMarket,
    ) -> TradeSignal | None:
        """Convert a WhaleTrade into a TradeSignal."""
        # Skip SELL trades (can't copy exits we didn't enter)
        if trade.side != "BUY":
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        # Determine token based on outcome
        outcome = trade.outcome
        if outcome.lower() in ("yes", ""):
            token_id = token_ids[0]
            price = market.yes_price or trade.price
            outcome = "Yes"
        elif outcome.lower() == "no":
            if len(token_ids) < 2:
                return None
            token_id = token_ids[1]
            price = market.no_price or (1.0 - (market.yes_price or 0.5))
            outcome = "No"
        else:
            return None

        if price <= 0 or price >= 1.0:
            return None

        edge = self._compute_edge(trade.win_rate)
        if edge < self.MIN_EDGE:
            return None

        copy_size = self._compute_copy_size(trade.size, trade.price)
        estimated_prob = min(0.95, price + edge)

        # Confidence based on whale's win rate
        confidence = 0.3 + trade.win_rate * 0.5
        confidence = min(0.85, confidence)

        return TradeSignal(
            strategy=self.name,
            market_id=trade.market_id,
            token_id=token_id,
            question=trade.question or market.question,
            side=OrderSide.BUY,
            outcome=outcome,
            estimated_prob=estimated_prob,
            market_price=price,
            edge=edge,
            size_usd=copy_size,
            confidence=confidence,
            reasoning=(
                f"Copy trade: {outcome} at ${price:.3f}. "
                f"Whale: {trade.username or trade.proxy_address[:12]} "
                f"(WR: {trade.win_rate:.0%}, size: {trade.size:.0f} shares). "
                f"Proportional copy: ${copy_size:.2f}"
            ),
            metadata={
                "source_wallet": trade.proxy_address,
                "whale_username": trade.username,
                "whale_win_rate": trade.win_rate,
                "whale_size": trade.size,
                "whale_price": trade.price,
                "whale_trade_id": trade.trade_id,
            },
        )

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for copy trading opportunities from whale trades."""
        if self._whale_tracker is None:
            return []

        whale_trades = self._whale_tracker.get_whale_trades()
        if not whale_trades:
            return []

        # Build market lookup
        market_map = {m.id: m for m in markets}

        signals: list[TradeSignal] = []
        seen_markets: set[str] = set()

        for trade in whale_trades:
            # Skip if already have a signal for this market
            if trade.market_id in seen_markets:
                continue

            market = market_map.get(trade.market_id)
            if market is None:
                continue

            signal = self._whale_trade_to_signal(trade, market)
            if signal is not None:
                signals.append(signal)
                seen_markets.add(trade.market_id)

        # Sort by edge descending, cap at max signals
        signals.sort(key=lambda s: s.edge, reverse=True)
        signals = signals[: self.MAX_COPY_SIGNALS_PER_CYCLE]

        if signals:
            self.logger.info(
                "copy_trading_scan_complete",
                whale_trades=len(whale_trades),
                signals_found=len(signals),
            )

        return signals

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs,
    ) -> str | bool:
        """Exit on stop-loss, take-profit (after 2h), or max hold time."""
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
                    "copy_trade_exit_stop_loss",
                    market_id=market_id,
                    loss_pct=f"{loss_pct:.1%}",
                )
                return "stop_loss"

        # Take-profit: only after minimum hold
        if avg_price > 0 and held_hours >= self.TAKE_PROFIT_MIN_HOLD_HOURS:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.TAKE_PROFIT_PCT:
                self.logger.info(
                    "copy_trade_exit_take_profit",
                    market_id=market_id,
                    profit_pct=f"{profit_pct:.1%}",
                )
                return "take_profit"

        # Max hold time
        if held_hours >= self.MAX_HOLD_HOURS:
            self.logger.info(
                "copy_trade_exit_max_hold",
                market_id=market_id,
                held_hours=f"{held_hours:.1f}",
            )
            return "max_hold_time"

        return False

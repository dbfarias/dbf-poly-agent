"""Signal detection for Trade Watcher agents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceMomentum:
    """Price change over multiple time windows."""

    pct_1h: float
    pct_4h: float
    pct_24h: float
    direction: str  # "bullish", "bearish", "neutral"


@dataclass(frozen=True)
class VolumeSignal:
    """Volume analysis relative to average."""

    current_ratio: float  # current / average (>1 = above avg)
    is_spike: bool  # >2x average


@dataclass(frozen=True)
class NewsSignal:
    """News headline analysis."""

    headline_count: int
    avg_sentiment: float  # -1 to +1
    has_strong_signal: bool  # count >= 3 and |sentiment| > 0.3


@dataclass(frozen=True)
class WatcherVerdict:
    """Aggregated decision from all signals."""

    action: str  # "scale_up", "hold", "scale_down", "exit"
    confidence: float  # 0-1
    confirming_signals: int  # how many signals agree
    reasoning: str


def compute_price_momentum(
    prices: list[tuple[float, float]], now: float
) -> PriceMomentum:
    """Compute price momentum over 1h, 4h, and 24h windows.

    Args:
        prices: list of (timestamp, price) sorted by time ascending.
        now: current timestamp in seconds.

    Returns:
        PriceMomentum with percentage changes and direction.
    """
    if not prices:
        return PriceMomentum(pct_1h=0.0, pct_4h=0.0, pct_24h=0.0, direction="neutral")

    current_price = prices[-1][1]
    if current_price <= 0:
        return PriceMomentum(pct_1h=0.0, pct_4h=0.0, pct_24h=0.0, direction="neutral")

    pct_1h = _pct_change_since(prices, now - 3600, current_price)
    pct_4h = _pct_change_since(prices, now - 14400, current_price)
    pct_24h = _pct_change_since(prices, now - 86400, current_price)

    direction = _classify_direction(pct_1h, pct_4h)
    return PriceMomentum(
        pct_1h=pct_1h, pct_4h=pct_4h, pct_24h=pct_24h, direction=direction
    )


def _pct_change_since(
    prices: list[tuple[float, float]], since: float, current: float
) -> float:
    """Find the price closest to `since` and compute pct change."""
    old_price = current
    for ts, price in prices:
        if ts >= since:
            old_price = price
            break
    if old_price <= 0:
        return 0.0
    return (current - old_price) / old_price


def _classify_direction(pct_1h: float, pct_4h: float) -> str:
    """Classify momentum direction from short-term changes."""
    bullish = (pct_1h > 0.01) + (pct_4h > 0.02)
    bearish = (pct_1h < -0.01) + (pct_4h < -0.02)
    if bullish >= 1 and bearish == 0:
        return "bullish"
    if bearish >= 1 and bullish == 0:
        return "bearish"
    return "neutral"


def compute_volume_signal(
    current_24h_vol: float, avg_24h_vol: float
) -> VolumeSignal:
    """Compute volume signal relative to average.

    Args:
        current_24h_vol: current 24h volume.
        avg_24h_vol: rolling average 24h volume.

    Returns:
        VolumeSignal with ratio and spike flag.
    """
    if avg_24h_vol <= 0:
        return VolumeSignal(current_ratio=0.0, is_spike=False)
    ratio = current_24h_vol / avg_24h_vol
    return VolumeSignal(current_ratio=ratio, is_spike=ratio > 2.0)


def compute_news_signal(
    headlines: list[tuple[str, float]],
) -> NewsSignal:
    """Compute news signal from headlines with sentiment scores.

    Args:
        headlines: list of (title, sentiment) where sentiment is [-1, +1].

    Returns:
        NewsSignal with count, average sentiment, and strong signal flag.
    """
    if not headlines:
        return NewsSignal(headline_count=0, avg_sentiment=0.0, has_strong_signal=False)

    count = len(headlines)
    avg_sent = sum(s for _, s in headlines) / count
    has_strong = count >= 3 and abs(avg_sent) > 0.3
    return NewsSignal(
        headline_count=count, avg_sentiment=avg_sent, has_strong_signal=has_strong
    )


def aggregate_signals(
    momentum: PriceMomentum,
    volume: VolumeSignal,
    news: NewsSignal,
    current_price: float,
    avg_entry: float,
    stop_loss_pct: float,
) -> WatcherVerdict:
    """Aggregate signals into a single verdict.

    Rules:
    - exit: stop loss hit OR (bearish + negative news)
    - scale_up: bullish + (volume spike OR strong news), min 2 confirming
    - hold: everything else
    """
    # Stop loss check
    if avg_entry > 0 and current_price < avg_entry * (1 - stop_loss_pct):
        return WatcherVerdict(
            action="exit",
            confidence=1.0,
            confirming_signals=0,
            reasoning=(
                f"Stop loss hit: price {current_price:.4f} < "
                f"entry {avg_entry:.4f} * (1 - {stop_loss_pct})"
            ),
        )

    # Count confirming signals for scale_up
    bullish_confirms = 0
    reasons: list[str] = []

    if momentum.direction == "bullish":
        bullish_confirms += 1
        reasons.append(f"bullish momentum (1h={momentum.pct_1h:+.2%})")

    if volume.is_spike:
        bullish_confirms += 1
        reasons.append(f"volume spike ({volume.current_ratio:.1f}x)")

    if news.has_strong_signal and news.avg_sentiment > 0.3:
        bullish_confirms += 1
        reasons.append(f"strong positive news ({news.headline_count} headlines)")

    if bullish_confirms >= 2:
        return WatcherVerdict(
            action="scale_up",
            confidence=min(1.0, bullish_confirms * 0.35),
            confirming_signals=bullish_confirms,
            reasoning="; ".join(reasons),
        )

    # Check bearish exit
    bearish_confirms = 0
    bearish_reasons: list[str] = []

    if momentum.direction == "bearish":
        bearish_confirms += 1
        bearish_reasons.append(f"bearish momentum (1h={momentum.pct_1h:+.2%})")

    if news.has_strong_signal and news.avg_sentiment < -0.3:
        bearish_confirms += 1
        bearish_reasons.append(f"strong negative news ({news.headline_count} headlines)")

    if bearish_confirms >= 2:
        return WatcherVerdict(
            action="exit",
            confidence=min(1.0, bearish_confirms * 0.4),
            confirming_signals=bearish_confirms,
            reasoning="; ".join(bearish_reasons),
        )

    # Default: hold
    return WatcherVerdict(
        action="hold",
        confidence=0.5,
        confirming_signals=0,
        reasoning="Mixed or insufficient signals",
    )

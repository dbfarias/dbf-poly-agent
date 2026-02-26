"""Tests for watcher signal detection and aggregation."""

import time

import pytest

from bot.agent.watcher_signals import (
    PriceMomentum,
    VolumeSignal,
    aggregate_signals,
    compute_news_signal,
    compute_price_momentum,
    compute_volume_signal,
)

# ---------------------------------------------------------------------------
# compute_price_momentum
# ---------------------------------------------------------------------------


class TestPriceMomentum:
    def test_empty_prices(self):
        result = compute_price_momentum([], time.time())
        assert result.direction == "neutral"
        assert result.pct_1h == 0.0

    def test_single_price(self):
        now = time.time()
        result = compute_price_momentum([(now, 0.50)], now)
        assert result.direction == "neutral"

    def test_bullish_momentum(self):
        now = time.time()
        prices = [
            (now - 7200, 0.40),  # 2h ago
            (now - 3600, 0.42),  # 1h ago
            (now, 0.50),         # now
        ]
        result = compute_price_momentum(prices, now)
        assert result.pct_1h > 0
        assert result.direction == "bullish"

    def test_bearish_momentum(self):
        now = time.time()
        prices = [
            (now - 7200, 0.60),
            (now - 3600, 0.55),
            (now, 0.50),
        ]
        result = compute_price_momentum(prices, now)
        assert result.pct_1h < 0
        assert result.direction == "bearish"

    def test_neutral_flat(self):
        now = time.time()
        prices = [
            (now - 7200, 0.50),
            (now - 3600, 0.50),
            (now, 0.502),
        ]
        result = compute_price_momentum(prices, now)
        assert result.direction == "neutral"

    def test_zero_price(self):
        now = time.time()
        result = compute_price_momentum([(now, 0.0)], now)
        assert result.direction == "neutral"


# ---------------------------------------------------------------------------
# compute_volume_signal
# ---------------------------------------------------------------------------


class TestVolumeSignal:
    def test_normal_volume(self):
        result = compute_volume_signal(10000.0, 10000.0)
        assert result.current_ratio == pytest.approx(1.0)
        assert result.is_spike is False

    def test_volume_spike(self):
        result = compute_volume_signal(25000.0, 10000.0)
        assert result.current_ratio == pytest.approx(2.5)
        assert result.is_spike is True

    def test_low_volume(self):
        result = compute_volume_signal(3000.0, 10000.0)
        assert result.current_ratio == pytest.approx(0.3)
        assert result.is_spike is False

    def test_zero_average(self):
        result = compute_volume_signal(5000.0, 0.0)
        assert result.current_ratio == 0.0
        assert result.is_spike is False

    def test_exactly_2x_not_spike(self):
        result = compute_volume_signal(20000.0, 10000.0)
        assert result.is_spike is False  # >2x required, not >=2x

    def test_just_above_2x_is_spike(self):
        result = compute_volume_signal(20001.0, 10000.0)
        assert result.is_spike is True


# ---------------------------------------------------------------------------
# compute_news_signal
# ---------------------------------------------------------------------------


class TestNewsSignal:
    def test_no_headlines(self):
        result = compute_news_signal([])
        assert result.headline_count == 0
        assert result.has_strong_signal is False

    def test_few_headlines_not_strong(self):
        headlines = [("Good news", 0.5), ("Great news", 0.8)]
        result = compute_news_signal(headlines)
        assert result.headline_count == 2
        assert result.has_strong_signal is False  # need >= 3

    def test_strong_positive_signal(self):
        headlines = [
            ("Good news A", 0.5),
            ("Good news B", 0.4),
            ("Good news C", 0.6),
        ]
        result = compute_news_signal(headlines)
        assert result.headline_count == 3
        assert result.avg_sentiment > 0.3
        assert result.has_strong_signal is True

    def test_strong_negative_signal(self):
        headlines = [
            ("Bad news A", -0.5),
            ("Bad news B", -0.4),
            ("Bad news C", -0.6),
        ]
        result = compute_news_signal(headlines)
        assert result.has_strong_signal is True
        assert result.avg_sentiment < -0.3

    def test_mixed_not_strong(self):
        headlines = [
            ("Good", 0.5),
            ("Bad", -0.4),
            ("Neutral", 0.0),
        ]
        result = compute_news_signal(headlines)
        assert result.has_strong_signal is False  # avg ~ 0.03


# ---------------------------------------------------------------------------
# aggregate_signals
# ---------------------------------------------------------------------------


class TestAggregateSignals:
    def _default_momentum(self, direction: str = "neutral") -> PriceMomentum:
        return PriceMomentum(pct_1h=0.0, pct_4h=0.0, pct_24h=0.0, direction=direction)

    def _default_volume(self, is_spike: bool = False) -> VolumeSignal:
        ratio = 2.5 if is_spike else 1.0
        return VolumeSignal(current_ratio=ratio, is_spike=is_spike)

    def test_stop_loss_exit(self):
        result = aggregate_signals(
            momentum=self._default_momentum(),
            volume=self._default_volume(),
            news=compute_news_signal([]),
            current_price=0.30,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "exit"
        assert result.confidence == 1.0
        assert "Stop loss" in result.reasoning

    def test_scale_up_bullish_with_volume(self):
        bullish = PriceMomentum(pct_1h=0.02, pct_4h=0.05, pct_24h=0.08, direction="bullish")
        result = aggregate_signals(
            momentum=bullish,
            volume=self._default_volume(is_spike=True),
            news=compute_news_signal([]),
            current_price=0.55,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "scale_up"
        assert result.confirming_signals >= 2

    def test_scale_up_bullish_with_strong_news(self):
        bullish = PriceMomentum(pct_1h=0.02, pct_4h=0.05, pct_24h=0.08, direction="bullish")
        news = compute_news_signal([
            ("Great news A", 0.5),
            ("Great news B", 0.4),
            ("Great news C", 0.6),
        ])
        result = aggregate_signals(
            momentum=bullish,
            volume=self._default_volume(),
            news=news,
            current_price=0.55,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "scale_up"

    def test_bearish_exit(self):
        bearish = PriceMomentum(pct_1h=-0.03, pct_4h=-0.06, pct_24h=-0.10, direction="bearish")
        bad_news = compute_news_signal([
            ("Bad A", -0.5),
            ("Bad B", -0.6),
            ("Bad C", -0.4),
        ])
        result = aggregate_signals(
            momentum=bearish,
            volume=self._default_volume(),
            news=bad_news,
            current_price=0.45,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "exit"

    def test_hold_on_mixed(self):
        result = aggregate_signals(
            momentum=self._default_momentum("neutral"),
            volume=self._default_volume(),
            news=compute_news_signal([]),
            current_price=0.50,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "hold"

    def test_hold_when_one_signal(self):
        """Only one confirming signal is not enough for scale_up."""
        bullish = PriceMomentum(pct_1h=0.02, pct_4h=0.05, pct_24h=0.08, direction="bullish")
        result = aggregate_signals(
            momentum=bullish,
            volume=self._default_volume(is_spike=False),
            news=compute_news_signal([]),
            current_price=0.55,
            avg_entry=0.50,
            stop_loss_pct=0.25,
        )
        assert result.action == "hold"

    def test_stop_loss_zero_entry(self):
        """Zero avg_entry should not trigger stop loss."""
        result = aggregate_signals(
            momentum=self._default_momentum(),
            volume=self._default_volume(),
            news=compute_news_signal([]),
            current_price=0.10,
            avg_entry=0.0,
            stop_loss_pct=0.25,
        )
        assert result.action == "hold"

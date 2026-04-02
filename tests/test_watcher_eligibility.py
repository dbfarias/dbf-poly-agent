"""Tests for watcher eligibility determination."""

from datetime import datetime, timedelta, timezone

from bot.agent.watcher_eligibility import is_watcher_eligible
from bot.research.market_classifier import MarketType

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Market type eligibility
# ---------------------------------------------------------------------------


class TestMarketTypeEligibility:
    """Only LONG_TERM, ECONOMIC, and UNKNOWN qualify."""

    def _make_eligible(self, market_type: MarketType) -> bool:
        return is_watcher_eligible(
            market_type=market_type,
            end_date=_NOW + timedelta(hours=72),
            price=0.50,
            volume=10000.0,
        )

    def test_short_term_rejected(self):
        assert self._make_eligible(MarketType.SHORT_TERM) is False

    def test_event_rejected(self):
        assert self._make_eligible(MarketType.EVENT) is False

    def test_weather_rejected(self):
        assert self._make_eligible(MarketType.WEATHER) is False

    def test_long_term_eligible(self):
        assert self._make_eligible(MarketType.LONG_TERM) is True

    def test_economic_eligible(self):
        assert self._make_eligible(MarketType.ECONOMIC) is True

    def test_unknown_eligible(self):
        assert self._make_eligible(MarketType.UNKNOWN) is True


# ---------------------------------------------------------------------------
# Price boundaries
# ---------------------------------------------------------------------------


class TestPriceBoundaries:
    def _check_price(self, price: float) -> bool:
        return is_watcher_eligible(
            market_type=MarketType.LONG_TERM,
            end_date=_NOW + timedelta(hours=72),
            price=price,
            volume=10000.0,
        )

    def test_price_below_min(self):
        assert self._check_price(0.09) is False

    def test_price_at_min(self):
        assert self._check_price(0.10) is True

    def test_price_at_max(self):
        assert self._check_price(0.85) is True

    def test_price_above_max(self):
        assert self._check_price(0.86) is False

    def test_price_zero(self):
        assert self._check_price(0.0) is False

    def test_price_one(self):
        assert self._check_price(1.0) is False


# ---------------------------------------------------------------------------
# Volume boundaries
# ---------------------------------------------------------------------------


class TestVolumeBoundaries:
    def _check_volume(self, volume: float) -> bool:
        return is_watcher_eligible(
            market_type=MarketType.LONG_TERM,
            end_date=_NOW + timedelta(hours=72),
            price=0.50,
            volume=volume,
        )

    def test_volume_below_min(self):
        assert self._check_volume(4999.0) is False

    def test_volume_at_min(self):
        assert self._check_volume(5000.0) is True

    def test_volume_large(self):
        assert self._check_volume(1_000_000.0) is True


# ---------------------------------------------------------------------------
# Time horizon
# ---------------------------------------------------------------------------


class TestTimeHorizon:
    def _check_hours(self, hours_left: float) -> bool:
        return is_watcher_eligible(
            market_type=MarketType.LONG_TERM,
            end_date=_NOW + timedelta(hours=hours_left),
            price=0.50,
            volume=10000.0,
        )

    def test_below_min_hours(self):
        assert self._check_hours(47.0) is False

    def test_at_min_hours(self):
        assert self._check_hours(48.1) is True

    def test_plenty_of_time(self):
        assert self._check_hours(720.0) is True

    def test_no_end_date_passes(self):
        """Markets without an end_date should pass the time check."""
        result = is_watcher_eligible(
            market_type=MarketType.LONG_TERM,
            end_date=None,
            price=0.50,
            volume=10000.0,
        )
        assert result is True

    def test_naive_end_date_treated_as_utc(self):
        """A naive datetime should be treated as UTC."""
        naive_future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=72)
        result = is_watcher_eligible(
            market_type=MarketType.LONG_TERM,
            end_date=naive_future,
            price=0.50,
            volume=10000.0,
        )
        assert result is True

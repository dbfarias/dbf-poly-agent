"""Tests for fee tracking: calculation, capture, PnL adjustment."""

import pytest

from bot.utils.risk_metrics import polymarket_fee


class TestPolymarketFee:
    """Unit tests for the polymarket_fee() pure function."""

    def test_zero_fee_rate(self):
        """Most markets (politics) have fee_rate=0 → no fee."""
        assert polymarket_fee(0.50, 10.0, 0.0) == 0.0

    def test_zero_shares(self):
        assert polymarket_fee(0.50, 0.0, 0.25) == 0.0

    def test_zero_price(self):
        assert polymarket_fee(0.0, 10.0, 0.25) == 0.0

    def test_price_at_one(self):
        """Price=1.0 → (1*(1-1))^exp = 0 → fee=0."""
        assert polymarket_fee(1.0, 10.0, 0.25) == 0.0

    def test_negative_inputs(self):
        assert polymarket_fee(-0.5, 10.0, 0.25) == 0.0
        assert polymarket_fee(0.5, -10.0, 0.25) == 0.0
        assert polymarket_fee(0.5, 10.0, -0.01) == 0.0

    def test_midpoint_maximum_fee(self):
        """At p=0.5, fee is maximum: shares * 0.5 * rate * (0.5*0.5)^2."""
        fee = polymarket_fee(0.50, 100.0, 0.25, exponent=2.0)
        # 100 * 0.50 * 0.25 * (0.5 * 0.5)^2 = 50 * 0.25 * 0.0625 = 0.78125
        assert fee == pytest.approx(0.78125, rel=1e-6)

    def test_crypto_fee_rate(self):
        """Crypto markets: fee_rate=0.25, typical price=0.60."""
        fee = polymarket_fee(0.60, 50.0, 0.25)
        # 50 * 0.60 * 0.25 * (0.60 * 0.40)^2 = 30 * 0.25 * 0.0576 = 0.432
        expected = 50 * 0.60 * 0.25 * (0.60 * 0.40) ** 2
        assert fee == pytest.approx(expected, rel=1e-6)

    def test_sports_fee_rate(self):
        """Sports markets: fee_rate=0.0175."""
        fee = polymarket_fee(0.50, 100.0, 0.0175)
        expected = 100 * 0.50 * 0.0175 * (0.50 * 0.50) ** 2
        assert fee == pytest.approx(expected, rel=1e-6)

    def test_custom_exponent(self):
        """Non-default exponent."""
        fee = polymarket_fee(0.50, 100.0, 0.25, exponent=1.0)
        # 100 * 0.50 * 0.25 * (0.5 * 0.5)^1 = 12.5 * 0.25 = 3.125
        expected = 100 * 0.50 * 0.25 * (0.50 * 0.50) ** 1.0
        assert fee == pytest.approx(expected, rel=1e-6)

    def test_extreme_price_near_zero(self):
        """Price near 0 → very small fee."""
        fee = polymarket_fee(0.01, 100.0, 0.25)
        assert fee > 0
        assert fee < 0.001  # Negligible

    def test_extreme_price_near_one(self):
        """Price near 1 → very small fee."""
        fee = polymarket_fee(0.99, 100.0, 0.25)
        assert fee > 0
        assert fee < 0.01  # Very small


class TestFeeTradeModel:
    """Test that Trade model has fee fields with correct defaults."""

    def test_trade_fee_defaults(self):
        from bot.data.models import Trade

        trade = Trade(
            market_id="m1",
            token_id="t1",
            side="BUY",
            price=0.5,
            size=10.0,
            strategy="test",
            fee_rate_bps=0,
            fee_amount_usd=0.0,
        )
        assert trade.fee_rate_bps == 0
        assert trade.fee_amount_usd == 0.0

    def test_trade_fee_set(self):
        from bot.data.models import Trade

        trade = Trade(
            market_id="m1",
            token_id="t1",
            side="BUY",
            price=0.5,
            size=10.0,
            strategy="test",
            fee_rate_bps=250,
            fee_amount_usd=0.78,
        )
        assert trade.fee_rate_bps == 250
        assert trade.fee_amount_usd == 0.78


class TestTradeResponseFeeFields:
    """Test that API schema includes fee fields."""

    def test_trade_response_defaults(self):
        from datetime import datetime, timezone

        from api.schemas import TradeResponse

        resp = TradeResponse(
            id=1,
            created_at=datetime.now(timezone.utc),
            market_id="m1",
            question="Test?",
            outcome="Yes",
            side="BUY",
            price=0.5,
            size=10.0,
            cost_usd=5.0,
            strategy="test",
            edge=0.05,
            estimated_prob=0.55,
            confidence=0.7,
            reasoning="test",
            status="filled",
            pnl=0.0,
            is_paper=True,
        )
        assert resp.fee_rate_bps == 0
        assert resp.fee_amount_usd == 0.0

    def test_trade_response_with_fees(self):
        from datetime import datetime, timezone

        from api.schemas import TradeResponse

        resp = TradeResponse(
            id=1,
            created_at=datetime.now(timezone.utc),
            market_id="m1",
            question="Test?",
            outcome="Yes",
            side="BUY",
            price=0.5,
            size=10.0,
            cost_usd=5.0,
            strategy="test",
            edge=0.05,
            estimated_prob=0.55,
            confidence=0.7,
            reasoning="test",
            status="filled",
            pnl=0.0,
            fee_rate_bps=250,
            fee_amount_usd=0.78,
            is_paper=True,
        )
        assert resp.fee_rate_bps == 250
        assert resp.fee_amount_usd == 0.78

    def test_trade_stats_includes_fees(self):
        from api.schemas import TradeStats

        stats = TradeStats(
            total_trades=10,
            winning_trades=5,
            total_pnl=1.23,
            win_rate=0.5,
            total_fees_usd=0.45,
        )
        assert stats.total_fees_usd == 0.45

    def test_trade_stats_default_fees(self):
        from api.schemas import TradeStats

        stats = TradeStats(
            total_trades=10,
            winning_trades=5,
            total_pnl=1.23,
            win_rate=0.5,
        )
        assert stats.total_fees_usd == 0.0

"""Tests for new risk checks: VaR gate and Z-Score gate."""


from bot.agent.risk_manager import RiskManager
from bot.data.returns_tracker import ReturnsTracker
from bot.polymarket.types import OrderSide, TradeSignal


def _make_signal(**overrides) -> TradeSignal:
    defaults = {
        "strategy": "value_betting",
        "market_id": "test-market",
        "token_id": "test-token",
        "question": "Test question?",
        "side": OrderSide.BUY,
        "outcome": "Yes",
        "estimated_prob": 0.75,
        "market_price": 0.60,
        "edge": 0.15,
        "size_usd": 5.0,
        "confidence": 0.8,
        "reasoning": "test",
        "metadata": {},
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


class TestVarCheck:
    def test_no_tracker(self):
        rm = RiskManager(returns_tracker=None)
        result = rm._check_daily_var(100.0)
        assert result.passed

    def test_insufficient_data(self):
        tracker = ReturnsTracker()
        for r in [0.01, -0.02, 0.03]:  # Only 3 days
            tracker.record_return(r)
        rm = RiskManager(returns_tracker=tracker)
        result = rm._check_daily_var(100.0)
        assert result.passed  # Not enough data, allow

    def test_good_var_passes(self):
        tracker = ReturnsTracker()
        # Stable positive returns → VaR should be fine
        for r in [0.01, 0.02, 0.01, 0.015, 0.01, 0.02, 0.01]:
            tracker.record_return(r)
        rm = RiskManager(returns_tracker=tracker)
        result = rm._check_daily_var(100.0)
        assert result.passed

    def test_bad_var_blocks_large_bankroll(self):
        tracker = ReturnsTracker()
        # Consistently losing → VaR should be very negative
        for r in [-0.08, -0.10, -0.07, -0.12, -0.09, -0.11, -0.08]:
            tracker.record_return(r)
        rm = RiskManager(returns_tracker=tracker)
        # Large bankroll uses strict -5% limit
        result = rm._check_daily_var(100.0)
        assert not result.passed
        assert "VaR" in result.reason

    def test_var_scales_with_small_bankroll(self):
        tracker = ReturnsTracker()
        # Moderate losses: VaR around -15%
        for r in [-0.05, -0.06, -0.04, -0.07, -0.05, -0.06, -0.05]:
            tracker.record_return(r)
        rm = RiskManager(returns_tracker=tracker)
        # Small bankroll ($12) uses -20% limit → passes
        result = rm._check_daily_var(12.0)
        assert result.passed
        # Large bankroll ($100) uses -5% limit → blocks
        result = rm._check_daily_var(100.0)
        assert not result.passed

    def test_var_limit_tiers(self):
        # VaR ~ -12.2% with these returns
        tracker = ReturnsTracker()
        for r in [-0.08, -0.10, -0.07, -0.12, -0.09, -0.11, -0.08]:
            tracker.record_return(r)
        rm = RiskManager(returns_tracker=tracker)
        # $12 → -20% limit → passes (VaR -12.2% > -20%)
        assert rm._check_daily_var(12.0).passed
        # $30 → -15% limit → passes (VaR -12.2% > -15%)
        assert rm._check_daily_var(30.0).passed
        # $60 → -10% limit → blocks (VaR -12.2% < -10%)
        assert not rm._check_daily_var(60.0).passed
        # $100 → -5% limit → blocks (VaR -12.2% < -5%)
        assert not rm._check_daily_var(100.0).passed


class TestZscoreCheck:
    def test_high_zscore_passes(self):
        rm = RiskManager()
        signal = _make_signal(
            estimated_prob=0.75,
            market_price=0.60,
            metadata={"price_std": 0.05},
        )
        # Z = (0.75 - 0.60) / 0.05 = 3.0 > 1.5
        result = rm._check_zscore(signal)
        assert result.passed
        assert signal.metadata["zscore"] == 3.0

    def test_low_zscore_blocks(self):
        rm = RiskManager()
        signal = _make_signal(
            estimated_prob=0.62,
            market_price=0.60,
            metadata={"price_std": 0.05},
        )
        # Z = (0.62 - 0.60) / 0.05 = 0.4 < 1.5
        result = rm._check_zscore(signal)
        assert not result.passed
        assert "Z-score" in result.reason

    def test_default_std_used(self):
        rm = RiskManager()
        signal = _make_signal(
            estimated_prob=0.75,
            market_price=0.60,
            metadata={},  # No price_std → defaults to 0.05
        )
        result = rm._check_zscore(signal)
        assert result.passed

    def test_negative_zscore_blocks(self):
        rm = RiskManager()
        signal = _make_signal(
            estimated_prob=0.58,
            market_price=0.60,
            metadata={"price_std": 0.05},
        )
        # Z = (0.58 - 0.60) / 0.05 = -0.4, |Z| = 0.4 < 1.5
        result = rm._check_zscore(signal)
        assert not result.passed


class TestTighterTierDefaults:
    def test_tier1_tighter(self):
        from bot.config import CapitalTier, TierConfig

        config = TierConfig.get(CapitalTier.TIER1)
        assert config["max_drawdown_pct"] == 0.12
        assert config["daily_loss_limit_pct"] == 0.06
        assert config["max_deployed_pct"] == 0.60
        assert config["kelly_fraction"] == 0.35

    def test_tier2_tighter(self):
        from bot.config import CapitalTier, TierConfig

        config = TierConfig.get(CapitalTier.TIER2)
        assert config["max_drawdown_pct"] == 0.10
        assert config["max_deployed_pct"] == 0.50
        assert config["kelly_fraction"] == 0.25

    def test_tier3_tighter(self):
        from bot.config import CapitalTier, TierConfig

        config = TierConfig.get(CapitalTier.TIER3)
        assert config["max_drawdown_pct"] == 0.08
        assert config["max_deployed_pct"] == 0.45

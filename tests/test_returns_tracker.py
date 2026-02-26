"""Tests for bot/data/returns_tracker.py."""


from bot.data.returns_tracker import ReturnsTracker


class TestReturnsTracker:
    def test_empty_returns(self):
        tracker = ReturnsTracker()
        assert tracker.returns == ()
        assert tracker.daily_var_95 == 0.0
        assert tracker.rolling_sharpe == 0.0

    def test_record_return(self):
        tracker = ReturnsTracker(window=5)
        tracker.record_return(0.01)
        tracker.record_return(-0.02)
        tracker.record_return(0.03)
        assert len(tracker.returns) == 3
        assert tracker.returns == (0.01, -0.02, 0.03)

    def test_window_limit(self):
        tracker = ReturnsTracker(window=3)
        for r in [0.01, 0.02, 0.03, 0.04, 0.05]:
            tracker.record_return(r)
        assert len(tracker.returns) == 3
        assert tracker.returns == (0.03, 0.04, 0.05)

    def test_immutable_returns(self):
        tracker = ReturnsTracker()
        tracker.record_return(0.01)
        returns = tracker.returns
        assert isinstance(returns, tuple)

    def test_var_with_enough_data(self):
        tracker = ReturnsTracker()
        # 10 days of returns
        for r in [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, -0.02, 0.02, -0.01]:
            tracker.record_return(r)
        var = tracker.daily_var_95
        assert var != 0.0  # Should compute something
        assert var < 0.05  # Reasonable range

    def test_sharpe_with_enough_data(self):
        tracker = ReturnsTracker()
        # Mostly positive returns
        for r in [0.02, 0.01, 0.03, 0.01, 0.02, 0.01, 0.02]:
            tracker.record_return(r)
        sharpe = tracker.rolling_sharpe
        assert sharpe > 0  # Positive returns → positive sharpe

    def test_profit_factor_default(self):
        tracker = ReturnsTracker()
        assert tracker.profit_factor_value == 0.0

    def test_profit_factor_with_data(self):
        tracker = ReturnsTracker()
        tracker._gross_profit = 50.0
        tracker._gross_loss = 25.0
        assert tracker.profit_factor_value == 2.0

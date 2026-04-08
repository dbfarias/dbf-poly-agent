"""Tests for Markov Chain model."""

import random

import pytest

from bot.research.markov_model import (
    MarkovEstimate,
    build_transition_matrix,
    monte_carlo_estimate,
)


class TestBuildTransitionMatrix:
    def test_rows_sum_to_one(self):
        """Each row of the transition matrix should sum to 1."""
        prices = [0.3, 0.4, 0.5, 0.6, 0.5, 0.4, 0.5, 0.6, 0.7]
        matrix = build_transition_matrix(prices)
        for row in matrix:
            assert abs(sum(row) - 1.0) < 1e-10

    def test_rows_sum_to_one_with_zero_rows(self):
        """Rows with no observed transitions should get uniform distribution."""
        prices = [0.05, 0.05, 0.05]  # Only state 0 has transitions
        matrix = build_transition_matrix(prices)
        for row in matrix:
            assert abs(sum(row) - 1.0) < 1e-10

    def test_trending_prices_upward(self):
        """An upward trend should have higher transition probs to higher states."""
        prices = [0.1 * i for i in range(1, 10)]  # 0.1, 0.2, ..., 0.9
        matrix = build_transition_matrix(prices)
        # State 0 (price ~0.1) should transition to state 1 (price ~0.2)
        assert matrix[0][1] > 0

    def test_stable_prices(self):
        """Stable prices should have high self-transition probability."""
        prices = [0.50] * 20
        matrix = build_transition_matrix(prices)
        state = int(0.50 * 10)  # state 5
        assert matrix[state][state] == 1.0

    def test_matrix_shape(self):
        prices = [0.3, 0.4, 0.5]
        matrix = build_transition_matrix(prices, n_states=10)
        assert len(matrix) == 10
        assert all(len(row) == 10 for row in matrix)

    def test_custom_n_states(self):
        prices = [0.2, 0.4, 0.6, 0.8]
        matrix = build_transition_matrix(prices, n_states=5)
        assert len(matrix) == 5
        assert all(len(row) == 5 for row in matrix)
        for row in matrix:
            assert abs(sum(row) - 1.0) < 1e-10

    def test_uniform_for_unvisited_states(self):
        """Unvisited states should have uniform (1/n) transition probabilities."""
        prices = [0.05, 0.05, 0.05]
        matrix = build_transition_matrix(prices, n_states=10)
        # State 5 was never visited, should be uniform
        expected = 1.0 / 10
        for val in matrix[5]:
            assert abs(val - expected) < 1e-10


class TestMonteCarloEstimate:
    def test_near_certainty(self):
        """A price stuck at 0.95 should estimate high probability."""
        random.seed(42)
        prices = [0.95] * 50
        matrix = build_transition_matrix(prices)
        result = monte_carlo_estimate(matrix, 0.95, days_to_resolution=10)
        assert isinstance(result, MarkovEstimate)
        assert result.model_prob > 0.9
        assert result.market_price == 0.95

    def test_near_zero(self):
        """A price stuck at 0.05 should estimate low probability."""
        random.seed(42)
        prices = [0.05] * 50
        matrix = build_transition_matrix(prices)
        result = monte_carlo_estimate(matrix, 0.05, days_to_resolution=10)
        assert result.model_prob < 0.1

    def test_uncertain_midrange(self):
        """A volatile price around 0.5 should give roughly 50% probability."""
        random.seed(42)
        prices = [max(0.01, min(0.99, 0.5 + random.gauss(0, 0.1))) for _ in range(100)]
        matrix = build_transition_matrix(prices)
        result = monte_carlo_estimate(
            matrix, 0.50, days_to_resolution=30, n_simulations=5000
        )
        assert 0.2 < result.model_prob < 0.8

    def test_result_fields(self):
        """Verify all fields are populated correctly."""
        random.seed(42)
        prices = [0.50] * 20
        matrix = build_transition_matrix(prices)
        result = monte_carlo_estimate(
            matrix, 0.50, days_to_resolution=5, n_simulations=100
        )
        assert result.simulations == 100
        assert abs(result.edge - (result.model_prob - result.market_price)) < 1e-10
        assert 0.0 <= result.confidence <= 1.0

    def test_frozen_dataclass(self):
        """MarkovEstimate should be immutable."""
        est = MarkovEstimate(
            model_prob=0.5, market_price=0.4, edge=0.1,
            simulations=100, confidence=0.8,
        )
        with pytest.raises(AttributeError):
            est.model_prob = 0.6  # type: ignore[misc]

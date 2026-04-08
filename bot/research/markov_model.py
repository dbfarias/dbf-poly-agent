"""Markov Chain model for prediction market price dynamics.

Builds a transition matrix from historical price data and runs
Monte Carlo simulations to estimate true probability.

Pure Python implementation -- no NumPy dependency required.
"""

import random
import statistics
from dataclasses import dataclass

N_STATES = 10  # Discretize price into 10 states (0-10%, 10-20%, ..., 90-100%)


@dataclass(frozen=True)
class MarkovEstimate:
    """Result of a Markov Chain Monte Carlo estimation."""

    model_prob: float  # Estimated true probability (fraction of sims ending >50%)
    market_price: float  # Current market price
    edge: float  # model_prob - market_price
    simulations: int  # Number of Monte Carlo runs
    confidence: float  # Based on simulation variance


def _discretize(price: float, n_states: int) -> int:
    """Convert a price (0-1) to a discrete state index."""
    return max(0, min(n_states - 1, int(price * n_states)))


def build_transition_matrix(
    prices: list[float], n_states: int = N_STATES
) -> list[list[float]]:
    """Build Markov transition matrix from price history.

    Discretizes prices into n_states bins and counts transitions
    between consecutive states. Returns a row-stochastic matrix
    where T[i][j] = P(next_state=j | current_state=i).

    Rows with no observed transitions get a uniform distribution.
    """
    states = [_discretize(p, n_states) for p in prices]

    # Count transitions
    counts: list[list[int]] = [[0] * n_states for _ in range(n_states)]
    for i in range(len(states) - 1):
        counts[states[i]][states[i + 1]] += 1

    # Normalize rows; zero-count rows get uniform distribution
    matrix: list[list[float]] = []
    uniform = 1.0 / n_states
    for row in counts:
        row_sum = sum(row)
        if row_sum == 0:
            matrix.append([uniform] * n_states)
        else:
            matrix.append([c / row_sum for c in row])

    return matrix


def _weighted_choice(weights: list[float]) -> int:
    """Select an index with probability proportional to weights."""
    r = random.random()  # noqa: S311
    cumulative = 0.0
    for i, w in enumerate(weights):
        cumulative += w
        if r <= cumulative:
            return i
    return len(weights) - 1


def monte_carlo_estimate(
    transition_matrix: list[list[float]],
    current_price: float,
    days_to_resolution: int = 30,
    n_simulations: int = 10_000,
    n_states: int = N_STATES,
) -> MarkovEstimate:
    """Run Monte Carlo simulation through transition matrix.

    Simulates n_simulations random walks through the Markov chain,
    each lasting days_to_resolution steps. Returns the fraction of
    simulations that end in a "Yes" state (>= 50%).
    """
    start_state = _discretize(current_price, n_states)
    threshold = n_states // 2
    finals: list[int] = []

    for _ in range(n_simulations):
        state = start_state
        for _ in range(days_to_resolution):
            state = _weighted_choice(transition_matrix[state])
        finals.append(state)

    yes_count = sum(1 for f in finals if f >= threshold)
    p_yes = yes_count / n_simulations

    # Confidence: lower variance in final states => higher confidence
    std_dev = statistics.stdev(finals) if len(finals) > 1 else 0.0
    confidence = max(0.0, 1.0 - std_dev / n_states)

    return MarkovEstimate(
        model_prob=p_yes,
        market_price=current_price,
        edge=p_yes - current_price,
        simulations=n_simulations,
        confidence=confidence,
    )

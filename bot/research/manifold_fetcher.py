"""Manifold Markets cross-platform price comparison — free, unlimited, no key.

Compares Manifold probability vs Polymarket price for edge detection.
If Manifold says 72% and Polymarket says 58%, that's 14 points of edge.
"""

import time

import httpx
import structlog

logger = structlog.get_logger()

_API_URL = "https://api.manifold.markets/v0"


class ManifoldFetcher:
    """Fetch Manifold Markets probabilities for cross-platform arbitrage."""

    CACHE_TTL = 600  # 10 min
    TIMEOUT = 15.0

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}  # keyword → probability
        self._cache_expires: float = 0.0
        self._all_markets: list[dict] = []

    async def refresh_markets(self) -> None:
        """Fetch trending/active markets from Manifold."""
        if time.monotonic() < self._cache_expires:
            return

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                # Fetch active markets sorted by volume
                response = await client.get(
                    f"{_API_URL}/search-markets",
                    params={
                        "sort": "liquidity",
                        "limit": 100,
                        "filter": "open",
                    },
                )
                response.raise_for_status()
                self._all_markets = response.json()
                self._cache_expires = time.monotonic() + self.CACHE_TTL
                self._cache.clear()

                logger.info(
                    "manifold_markets_fetched",
                    count=len(self._all_markets),
                )

        except Exception as e:
            logger.warning("manifold_fetch_failed", error=str(e))

    def find_matching_probability(self, question: str) -> float | None:
        """Find a Manifold market matching the Polymarket question.

        Returns Manifold probability (0-1) or None if no match.
        Uses keyword overlap to fuzzy-match questions.
        """
        if not self._all_markets:
            return None

        # Extract key words from question (skip common words)
        stop_words = {
            "will", "the", "be", "by", "on", "in", "of", "a", "an",
            "to", "or", "and", "is", "for", "at", "from", "with",
            "march", "april", "may", "june", "2026", "2027",
        }
        q_words = set(
            w.lower().strip("?.,!") for w in question.split()
            if len(w) > 2 and w.lower() not in stop_words
        )

        if len(q_words) < 2:
            return None

        best_match: dict | None = None
        best_overlap = 0

        for market in self._all_markets:
            m_question = market.get("question", "")
            m_words = set(
                w.lower().strip("?.,!") for w in m_question.split()
                if len(w) > 2 and w.lower() not in stop_words
            )

            overlap = len(q_words & m_words)
            # Require at least 3 matching keywords and >40% Jaccard similarity
            jaccard = overlap / len(q_words | m_words) if (q_words | m_words) else 0
            if overlap >= 3 and jaccard > 0.4 and overlap > best_overlap:
                best_overlap = overlap
                best_match = market

        if best_match is None:
            return None

        prob = best_match.get("probability")
        if prob is not None and 0 < prob < 1:
            logger.info(
                "manifold_match_found",
                polymarket_q=question[:60],
                manifold_q=best_match.get("question", "")[:60],
                manifold_prob=round(prob, 3),
                overlap=best_overlap,
            )
            return prob

        return None

    def get_cross_platform_edge(
        self, question: str, polymarket_price: float,
    ) -> tuple[float, float]:
        """Compare Manifold probability vs Polymarket price.

        Returns (manifold_prob, edge) where edge = manifold_prob - polymarket_price.
        Positive edge means Polymarket is underpriced vs Manifold.
        """
        prob = self.find_matching_probability(question)
        if prob is None:
            return 0.0, 0.0
        edge = prob - polymarket_price
        return prob, edge

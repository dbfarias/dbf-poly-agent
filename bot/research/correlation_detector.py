"""Cross-market correlation detector — prevents overexposure to the same event."""

import re

import structlog

logger = structlog.get_logger()

# Jaccard threshold for considering two markets correlated
_JACCARD_THRESHOLD = 0.5

# Stop words to exclude from tokenization (same as keyword_extractor)
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "will", "would", "could", "should", "may", "might", "can", "do", "does",
    "did", "has", "have", "had", "if", "or", "and", "but", "not", "no",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
    "about", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "than", "too",
    "very", "just", "also", "more", "most", "other", "some", "such",
    "any", "each", "every", "all", "both", "few", "how", "what", "which",
    "who", "whom", "this", "that", "these", "those", "when", "where",
    "why", "so", "because", "as", "until", "while", "it", "its",
    "he", "she", "they", "them", "his", "her", "their", "there",
    "yes", "no", "market", "resolve",
})

_MIN_TOKEN_LEN = 3


def _tokenize(question: str) -> frozenset[str]:
    """Tokenize a question into a set of meaningful words."""
    # Remove punctuation, lowercase
    cleaned = re.sub(r"[^\w\s]", " ", question.lower())
    tokens = {
        word
        for word in cleaned.split()
        if len(word) >= _MIN_TOKEN_LEN and word not in _STOP_WORDS
    }
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


class _UnionFind:
    """Disjoint set / Union-Find for transitive grouping."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        # Path compression
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        # Union by rank
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1


class CorrelationDetector:
    """Detects correlated markets using word-overlap similarity (Jaccard).

    Groups markets whose questions are semantically similar to prevent
    overexposure to the same underlying event.
    """

    def __init__(self) -> None:
        self._question_tokens: dict[str, frozenset[str]] = {}
        self._correlation_groups: dict[str, str] = {}  # market_id → group_id

    def update(self, markets: list) -> None:
        """Rebuild correlation groups from current market list.

        Each market must have: id, question.
        Uses Jaccard coefficient on tokenized questions.
        Pairs with Jaccard > threshold are grouped transitively via Union-Find.
        """
        # Tokenize all questions
        tokens_map: dict[str, frozenset[str]] = {}
        for market in markets:
            tokens_map[market.id] = _tokenize(market.question)
        self._question_tokens = tokens_map

        # Build groups via Union-Find
        uf = _UnionFind()
        market_ids = list(tokens_map.keys())

        # O(n^2) comparison — fine for ~50-100 markets per scan
        for i in range(len(market_ids)):
            for j in range(i + 1, len(market_ids)):
                mid_a, mid_b = market_ids[i], market_ids[j]
                similarity = _jaccard(tokens_map[mid_a], tokens_map[mid_b])
                if similarity >= _JACCARD_THRESHOLD:
                    uf.union(mid_a, mid_b)

        # Build group mapping
        new_groups: dict[str, str] = {}
        for mid in market_ids:
            new_groups[mid] = uf.find(mid)
        self._correlation_groups = new_groups

        # Count non-trivial groups (>1 member)
        group_members: dict[str, list[str]] = {}
        for mid, gid in new_groups.items():
            group_members.setdefault(gid, []).append(mid)
        multi_groups = {
            gid: members
            for gid, members in group_members.items()
            if len(members) > 1
        }

        if multi_groups:
            logger.info(
                "correlation_groups_found",
                groups=len(multi_groups),
                total_correlated=sum(len(m) for m in multi_groups.values()),
            )

    def get_group(self, market_id: str) -> str | None:
        """Get the correlation group ID for a market."""
        return self._correlation_groups.get(market_id)

    def are_correlated(self, market_id_a: str, market_id_b: str) -> bool:
        """Check if two markets are in the same correlation group."""
        group_a = self._correlation_groups.get(market_id_a)
        group_b = self._correlation_groups.get(market_id_b)
        if group_a is None or group_b is None:
            return False
        return group_a == group_b

    def get_group_members(self, group_id: str) -> list[str]:
        """Get all market IDs in a correlation group."""
        return [
            mid
            for mid, gid in self._correlation_groups.items()
            if gid == group_id
        ]

    def jaccard_similarity(self, question_a: str, question_b: str) -> float:
        """Compute Jaccard similarity between two questions (for external use)."""
        return _jaccard(_tokenize(question_a), _tokenize(question_b))

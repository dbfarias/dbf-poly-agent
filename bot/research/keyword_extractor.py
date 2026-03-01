"""Extract search keywords from Polymarket questions."""

import re

# Common stop words to filter out
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
})

# Question prefixes to strip
_PREFIX_PATTERNS = [
    r"^will\s+",
    r"^does\s+",
    r"^is\s+",
    r"^can\s+",
    r"^shall\s+",
    r"^are\s+",
]

# Common suffixes to strip
_SUFFIX_PATTERNS = [
    r"\s+by\s+\w+\s+\d{1,2},?\s+\d{4}\??$",  # "by January 1, 2026?"
    r"\s+before\s+\w+\s+\d{1,2},?\s+\d{4}\??$",
    r"\s+in\s+\d{4}\??$",  # "in 2026?"
    r"\?+$",
]

# Known entities that should always be kept
_KNOWN_ENTITIES = frozenset({
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "matic", "polygon",
    "cardano", "ada", "dogecoin", "doge", "xrp", "ripple",
    "trump", "biden", "harris", "desantis", "obama", "putin", "zelensky",
    "fed", "federal reserve", "sec", "congress", "senate", "supreme court",
    "nfl", "nba", "mlb", "nhl", "super bowl", "world cup", "olympics",
    "tesla", "apple", "google", "microsoft", "amazon", "nvidia", "meta",
    "s&p", "nasdaq", "dow jones", "inflation", "gdp", "interest rate",
})


def extract_keywords(question: str) -> list[str]:
    """Extract 2-5 search keywords from a Polymarket question.

    Strategy:
    1. Strip question prefixes (Will, Does, Is, etc.)
    2. Strip date suffixes
    3. Extract known entities
    4. Extract capitalized words (proper nouns)
    5. Remove stop words
    6. Return 2-5 best keywords
    """
    if not question or len(question.strip()) < 5:
        return []

    text = question.strip()

    # Strip prefixes
    for pattern in _PREFIX_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Strip suffixes
    for pattern in _SUFFIX_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    keywords: list[str] = []
    text_lower = text.lower()

    # Check for known entities first
    for entity in _KNOWN_ENTITIES:
        if entity in text_lower:
            keywords.append(entity)

    # Extract capitalized words (proper nouns) — skip first word
    # (may be capitalized due to sentence start)
    words = text.split()
    for word in words[1:]:
        cleaned = re.sub(r"[^\w]", "", word)
        if cleaned and cleaned[0].isupper() and len(cleaned) > 1:
            lower = cleaned.lower()
            if lower not in _STOP_WORDS and lower not in keywords:
                keywords.append(lower)

    # If we still have fewer than 2 keywords, add remaining non-stop words
    if len(keywords) < 2:
        for word in words:
            cleaned = re.sub(r"[^\w]", "", word).lower()
            if (
                cleaned and len(cleaned) > 2
                and cleaned not in _STOP_WORDS
                and cleaned not in keywords
            ):
                keywords.append(cleaned)

    # Cap at 5 keywords
    return keywords[:5]

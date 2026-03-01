"""Research data types — immutable dataclasses for news and sentiment."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    """A single news headline with VADER sentiment."""

    title: str
    source: str
    published: datetime
    url: str
    sentiment: float  # VADER compound [-1, 1]


@dataclass(frozen=True)
class ResearchResult:
    """Aggregated research for a single market."""

    market_id: str
    keywords: tuple[str, ...]
    news_items: tuple[NewsItem, ...]
    sentiment_score: float  # weighted avg [-1, 1]
    confidence: float  # 0-1 based on article count
    research_multiplier: float  # 0.7 - 1.3
    updated_at: datetime
    crypto_sentiment: float = 0.0  # BTC/ETH market sentiment

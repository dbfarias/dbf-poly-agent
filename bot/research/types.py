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
    crypto_prices: tuple[tuple[str, float], ...] = ()  # (("bitcoin", 102000.0), ...)
    description_context: str = ""  # Extracted from market description
    resolution_condition: str = ""  # HOW market resolves (from description)
    resolution_source: str = ""  # Data source for resolution
    is_volume_anomaly: bool = False  # Volume/price spike detected
    whale_activity: bool = False  # Large orders detected on CLOB
    market_category: str = ""  # LLM/regex classified category
    historical_base_rate: float = 0.0  # Win rate for similar past trades (0 = no data)
    twitter_sentiment: float = 0.0  # Separate Twitter/X sentiment [-1, 1]
    tweet_count: int = 0  # Number of tweets found

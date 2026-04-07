"""Google News + Financial Times RSS fetcher — free, unlimited, no API key."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog

from bot.research.sentiment import get_headline_sentiment
from bot.research.types import NewsItem

logger = structlog.get_logger()

_FT_FEEDS: dict[str, dict[str, str | set[str]]] = {
    "commodities": {
        "url": "https://www.ft.com/commodities?format=rss",
        "keywords": {
            "oil", "crude", "wti", "brent", "opec", "gas",
            "energy", "commodity", "petroleum", "barrel",
        },
    },
    "world": {
        "url": "https://www.ft.com/world?format=rss",
        "keywords": {
            "iran", "sanctions", "tariff", "war", "ceasefire",
            "russia", "china", "trump", "biden", "election", "geopolit",
        },
    },
    "markets": {
        "url": "https://www.ft.com/markets?format=rss",
        "keywords": {
            "fed", "rate", "inflation", "cpi", "gdp", "unemployment",
            "bitcoin", "crypto", "stock", "bond", "yield",
        },
    },
}

# Cache TTL for FT feeds (seconds)
_FT_CACHE_TTL = 900  # 15 minutes


class _FTFeedCache:
    """Per-feed cache entry for Financial Times RSS."""

    __slots__ = ("items", "fetched_at")

    def __init__(self, items: list[NewsItem], fetched_at: datetime) -> None:
        self.items = items
        self.fetched_at = fetched_at

    def is_expired(self) -> bool:
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds()
        return age > _FT_CACHE_TTL


class NewsFetcher:
    """Fetches news headlines from Google News RSS + Financial Times RSS."""

    BASE_URL = "https://news.google.com/rss/search"
    TIMEOUT = 10.0

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._ft_cache: dict[str, _FTFeedCache] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    async def fetch_news(
        self, keywords: list[str], max_results: int = 10
    ) -> list[NewsItem]:
        """Fetch news for given keywords via Google News + FT RSS.

        Returns list of NewsItem sorted by recency (newest first).
        """
        if not keywords:
            return []

        google_items = await self._fetch_google_news(keywords, max_results)
        ft_items = await self._fetch_ft_news(keywords)

        return self._merge_and_deduplicate(google_items, ft_items, max_results)

    async def _fetch_google_news(
        self, keywords: list[str], max_results: int
    ) -> list[NewsItem]:
        """Fetch news from Google News RSS."""
        query = " ".join(keywords)
        url = f"{self.BASE_URL}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            return self._parse_rss(response.text, max_results)
        except httpx.HTTPError as e:
            logger.warning("news_fetch_failed", error=str(e), keywords=keywords)
            return []
        except Exception as e:
            logger.warning("news_parse_failed", error=str(e), keywords=keywords)
            return []

    async def _fetch_ft_news(self, keywords: list[str]) -> list[NewsItem]:
        """Fetch relevant FT RSS feeds based on keyword matching."""
        lower_keywords = {kw.lower() for kw in keywords}
        matched_feeds = _match_ft_feeds(lower_keywords)

        if not matched_feeds:
            return []

        all_items: list[NewsItem] = []
        for feed_name in matched_feeds:
            items = await self._fetch_single_ft_feed(feed_name)
            filtered = _filter_ft_items(items, lower_keywords)
            all_items.extend(filtered)

        return all_items

    async def _fetch_single_ft_feed(self, feed_name: str) -> list[NewsItem]:
        """Fetch a single FT feed, using cache if available."""
        cached = self._ft_cache.get(feed_name)
        if cached is not None and not cached.is_expired():
            logger.debug("ft_feed_cache_hit", feed=feed_name)
            return cached.items

        feed_config = _FT_FEEDS[feed_name]
        url = str(feed_config["url"])

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            items = self._parse_rss(response.text, max_results=50, source="FT")
            self._ft_cache[feed_name] = _FTFeedCache(
                items=items, fetched_at=datetime.now(timezone.utc),
            )
            logger.info("ft_feed_fetched", feed=feed_name, count=len(items))
            return items
        except httpx.HTTPError as e:
            logger.warning("ft_feed_failed", feed=feed_name, error=str(e))
            return cached.items if cached else []
        except Exception as e:
            logger.warning("ft_feed_parse_failed", feed=feed_name, error=str(e))
            return cached.items if cached else []

    def _parse_rss(
        self, xml_text: str, max_results: int, source: str | None = None,
    ) -> list[NewsItem]:
        """Parse RSS XML into NewsItem list."""
        feed = feedparser.parse(xml_text)
        items: list[NewsItem] = []

        for entry in feed.entries[:max_results]:
            title = entry.get("title", "")
            entry_source = source or (
                entry.get("source", {}).get("title", "Unknown")
            )
            raw_url = entry.get("link", "")
            url = raw_url if raw_url.startswith("https://") else ""
            published = self._parse_date(entry.get("published", ""))
            sentiment = get_headline_sentiment(title)

            items.append(
                NewsItem(
                    title=title,
                    source=entry_source,
                    published=published,
                    url=url,
                    sentiment=sentiment,
                ),
            )

        return sorted(items, key=lambda x: x.published, reverse=True)

    def _parse_date(self, date_str: str) -> datetime:
        """Parse RSS date string to datetime."""
        if not date_str:
            return datetime.now(timezone.utc)
        try:
            return parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _merge_and_deduplicate(
        primary: list[NewsItem],
        secondary: list[NewsItem],
        max_results: int,
    ) -> list[NewsItem]:
        """Merge two lists, dedup by Jaccard > 0.6, sort by recency."""
        merged = list(primary)
        for item in secondary:
            item_words = set(item.title.lower().split())
            is_dup = any(
                _jaccard(item_words, set(e.title.lower().split())) > 0.6
                for e in merged
                if item_words
            )
            if not is_dup:
                merged.append(item)

        merged.sort(key=lambda x: x.published, reverse=True)
        return merged[:max_results]

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _match_ft_feeds(lower_keywords: set[str]) -> list[str]:
    """Return FT feed names whose keywords overlap with search terms."""
    matched: list[str] = []
    for name, config in _FT_FEEDS.items():
        feed_kws = config["keywords"]
        assert isinstance(feed_kws, set)  # noqa: S101
        if lower_keywords & feed_kws:
            matched.append(name)
    return matched


def _filter_ft_items(
    items: list[NewsItem], keywords: set[str],
) -> list[NewsItem]:
    """Keep only FT items whose title contains at least one keyword."""
    return [
        item for item in items
        if any(kw in item.title.lower() for kw in keywords)
    ]


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

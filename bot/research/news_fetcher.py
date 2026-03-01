"""Google News RSS fetcher — free, unlimited, no API key."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog

from bot.research.sentiment import get_headline_sentiment
from bot.research.types import NewsItem

logger = structlog.get_logger()


class NewsFetcher:
    """Fetches news headlines from Google News RSS."""

    BASE_URL = "https://news.google.com/rss/search"
    TIMEOUT = 10.0

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=False,
            )
        return self._client

    async def fetch_news(
        self, keywords: list[str], max_results: int = 10
    ) -> list[NewsItem]:
        """Fetch news for given keywords via Google News RSS.

        Returns list of NewsItem sorted by recency (newest first).
        """
        if not keywords:
            return []

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

    def _parse_rss(self, xml_text: str, max_results: int) -> list[NewsItem]:
        """Parse RSS XML into NewsItem list."""
        feed = feedparser.parse(xml_text)
        items: list[NewsItem] = []

        for entry in feed.entries[:max_results]:
            title = entry.get("title", "")
            source = entry.get("source", {}).get("title", "Unknown")
            raw_url = entry.get("link", "")
            url = raw_url if raw_url.startswith("https://") else ""

            # Parse published date
            published = self._parse_date(entry.get("published", ""))

            # Run VADER sentiment on headline
            sentiment = get_headline_sentiment(title)

            items.append(
                NewsItem(
                    title=title,
                    source=source,
                    published=published,
                    url=url,
                    sentiment=sentiment,
                )
            )

        # Sort by recency (newest first)
        return sorted(items, key=lambda x: x.published, reverse=True)

    def _parse_date(self, date_str: str) -> datetime:
        """Parse RSS date string to datetime."""
        if not date_str:
            return datetime.now(timezone.utc)
        try:
            return parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

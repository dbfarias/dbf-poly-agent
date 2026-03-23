"""General news fetcher via Tavily search API — broader than Twitter-only."""

import asyncio
import time
from datetime import date, datetime, timezone

import httpx
import structlog

from bot.config import settings
from bot.research.sentiment import get_headline_sentiment
from bot.research.types import NewsItem

logger = structlog.get_logger()

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 10.0
_REQUEST_DELAY = 1.5  # Slightly slower than Twitter fetcher
_MAX_FAILURES = 3
_CIRCUIT_BREAK_SECONDS = 300  # 5 min cooldown
_DAILY_BUDGET = 30  # Max 30 searches/day (free tier = 1000/month ≈ 33/day)


class TavilyNewsFetcher:
    """Fetches general news via Tavily search API (no domain restriction).

    Rate-limited with circuit breaker and daily budget to stay within free tier.
    Complements the existing Google News + Reddit + Twitter fetchers.
    """

    def __init__(self, daily_budget: int = _DAILY_BUDGET) -> None:
        self._client: httpx.AsyncClient | None = None
        self._failure_count = 0
        self._circuit_open_until: float = 0.0
        self._last_request_time: float = 0.0
        self._today_calls = 0
        self._today_date: date = date.today()
        self._daily_budget = daily_budget

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    async def search_news(
        self,
        keywords: list[str],
        max_results: int = 5,
    ) -> list[NewsItem]:
        """Search for general news matching keywords via Tavily.

        Returns list of NewsItem (compatible with other fetchers).
        Returns [] silently if no API key, budget exceeded, or circuit open.
        """
        if not settings.tavily_api_key:
            return []

        if not keywords:
            return []

        # Circuit breaker check
        if self._is_circuit_open():
            return []

        # Daily budget check
        if not self._check_daily_budget():
            return []

        query = " ".join(keywords[:5])
        try:
            items = await self._search_tavily(query, max_results)
        except Exception as e:
            logger.warning(
                "tavily_news_search_failed",
                error=str(e),
            )
            self._record_failure()
            return []

        # Deduplicate by title (exact match)
        seen_titles: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            title_key = item.title.lower().strip()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique.append(item)

        return unique[:max_results]

    async def _search_tavily(
        self, query: str, max_results: int
    ) -> list[NewsItem]:
        """Call Tavily search API for general news (no domain restriction)."""
        # Rate limiting
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _REQUEST_DELAY:
            await asyncio.sleep(_REQUEST_DELAY - elapsed)

        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",  # Basic is cheaper, sufficient for news
            "topic": "news",
        }

        client = await self._get_client()
        self._last_request_time = time.monotonic()
        response = await client.post(_TAVILY_URL, json=payload)
        response.raise_for_status()

        data = response.json()
        self._failure_count = 0  # Reset on success

        items: list[NewsItem] = []
        for result in data.get("results", [])[:max_results]:
            title = result.get("title", "")
            if not title:
                continue

            published_str = result.get("published_date", "")
            published = _parse_date(published_str)
            url = result.get("url", "")

            items.append(
                NewsItem(
                    title=title,
                    source="Tavily",
                    published=published,
                    url=url,
                    sentiment=get_headline_sentiment(title),
                )
            )

        return items

    def _check_daily_budget(self) -> bool:
        """Check if daily call budget is available. Resets at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today != self._today_date:
            self._today_date = today
            self._today_calls = 0

        if self._today_calls >= self._daily_budget:
            logger.debug(
                "tavily_news_daily_budget_exceeded",
                calls=self._today_calls,
                budget=self._daily_budget,
            )
            return False

        self._today_calls += 1
        return True

    def _record_failure(self) -> None:
        """Record a failure and open circuit breaker if threshold exceeded."""
        self._failure_count += 1
        if self._failure_count >= _MAX_FAILURES:
            self._circuit_open_until = time.monotonic() + _CIRCUIT_BREAK_SECONDS
            logger.warning(
                "tavily_news_circuit_breaker_open",
                failures=self._failure_count,
                cooldown_seconds=_CIRCUIT_BREAK_SECONDS,
            )

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is currently open."""
        if self._failure_count < _MAX_FAILURES:
            return False
        if time.monotonic() >= self._circuit_open_until:
            # Reset circuit breaker
            self._failure_count = 0
            self._circuit_open_until = 0.0
            return False
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _parse_date(date_str: str) -> datetime:
    """Best-effort ISO date parse; falls back to now(UTC)."""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)

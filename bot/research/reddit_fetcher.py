"""Reddit post fetcher — supplements Google News with Reddit discussions."""

import asyncio
import time
from datetime import datetime, timezone

import httpx
import structlog

from bot.research.sentiment import get_headline_sentiment
from bot.research.types import NewsItem

logger = structlog.get_logger()

# Subreddit mapping by market category
_SUBREDDIT_MAP: dict[str, list[str]] = {
    "crypto": ["cryptocurrency", "bitcoin", "ethereum"],
    "politics": ["politics", "geopolitics"],
    "economics": ["economics", "finance"],
    "other": ["news", "worldnews"],
}

_USER_AGENT = "polybot-research/1.0"
_TIMEOUT = 10.0
_REQUEST_DELAY = 1.0  # Reddit allows ~10 req/min unauthenticated
_MAX_FAILURES = 3  # Circuit breaker threshold
_CIRCUIT_BREAK_SECONDS = 300  # 5 min cooldown after failures


class RedditFetcher:
    """Fetches Reddit posts to supplement Google News headlines.

    Uses Reddit's public JSON API (no auth needed).
    Rate-limited to 1 req/s with circuit breaker on repeated failures.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._failure_count = 0
        self._circuit_open_until: float = 0.0
        self._last_request_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    async def fetch_posts(
        self,
        keywords: list[str],
        category: str = "other",
        max_results: int = 5,
    ) -> list[NewsItem]:
        """Fetch Reddit posts matching keywords for the given category.

        Returns list of NewsItem (compatible with Google News items).
        """
        # Circuit breaker check
        if self._is_circuit_open():
            return []

        subreddits = _SUBREDDIT_MAP.get(category, _SUBREDDIT_MAP["other"])
        query = " ".join(keywords[:3])  # Reddit search works best with fewer terms

        all_posts: list[NewsItem] = []
        for sub in subreddits[:2]:  # Limit to 2 subreddits per call
            try:
                posts = await self._search_subreddit(sub, query, max_results)
                all_posts.extend(posts)
            except Exception as e:
                logger.debug(
                    "reddit_search_failed",
                    subreddit=sub,
                    error=str(e),
                )
                self._record_failure()
                if self._is_circuit_open():
                    break

        # Deduplicate by title (exact match)
        seen_titles: set[str] = set()
        unique: list[NewsItem] = []
        for post in all_posts:
            title_key = post.title.lower().strip()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique.append(post)

        return unique[:max_results]

    async def _search_subreddit(
        self, subreddit: str, query: str, limit: int
    ) -> list[NewsItem]:
        """Search a single subreddit via JSON API."""
        # Rate limiting
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _REQUEST_DELAY:
            await asyncio.sleep(_REQUEST_DELAY - elapsed)

        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {
            "q": query,
            "sort": "new",
            "limit": str(limit),
            "restrict_sr": "on",
            "t": "week",  # Last week only
        }

        client = await self._get_client()
        self._last_request_time = time.monotonic()
        response = await client.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        self._failure_count = 0  # Reset on success

        posts: list[NewsItem] = []
        children = data.get("data", {}).get("children", [])

        for child in children[:limit]:
            post_data = child.get("data", {})
            title = post_data.get("title", "")
            if not title:
                continue

            created_utc = post_data.get("created_utc", 0)
            published = datetime.fromtimestamp(created_utc, tz=timezone.utc)
            permalink = post_data.get("permalink", "")
            post_url = f"https://www.reddit.com{permalink}" if permalink else ""

            posts.append(
                NewsItem(
                    title=title,
                    source=f"Reddit r/{subreddit}",
                    published=published,
                    url=post_url,
                    sentiment=get_headline_sentiment(title),
                )
            )

        return posts

    def _record_failure(self) -> None:
        """Record a failure and open circuit breaker if threshold exceeded."""
        self._failure_count += 1
        if self._failure_count >= _MAX_FAILURES:
            self._circuit_open_until = time.monotonic() + _CIRCUIT_BREAK_SECONDS
            logger.warning(
                "reddit_circuit_breaker_open",
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

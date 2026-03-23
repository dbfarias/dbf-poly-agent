"""Sports odds fetcher — compare sportsbook odds vs Polymarket prices.

Uses The Odds API (free tier: 500 requests/month) to get consensus odds
from multiple sportsbooks (DraftKings, FanDuel, BetMGM, etc.).

When sportsbooks say a team has 95% chance but Polymarket prices it at $0.85,
that's a 10% edge — fundamentally sound arbitrage.
"""

import os
import re
import time

import httpx
import structlog

logger = structlog.get_logger()

# The Odds API — free tier: 500 req/month
_ODDS_API_URL = "https://api.the-odds-api.com/v4/sports"
_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Sport keys for The Odds API
_SPORT_KEYS = [
    "basketball_nba",
    "basketball_ncaab",
    "icehockey_nhl",
    "baseball_mlb",
    "americanfootball_nfl",
    "soccer_epl",
    "soccer_usa_mls",
    "mma_mixed_martial_arts",
]

# Keywords to detect sports in Polymarket questions
_SPORT_PATTERNS = [
    # NBA teams
    re.compile(r"\b(lakers|celtics|warriors|nuggets|76ers|sixers|bucks|heat|"
               r"suns|nets|knicks|clippers|mavericks|mavs|grizzlies|"
               r"cavaliers|cavs|thunder|timberwolves|rockets|pelicans|"
               r"pacers|hawks|bulls|magic|raptors|pistons|hornets|wizards|"
               r"spurs|kings|blazers|trail blazers|jazz)\b", re.I),
    # NFL teams
    re.compile(r"\b(chiefs|eagles|49ers|ravens|lions|bills|cowboys|"
               r"dolphins|jets|packers|bengals|chargers|rams|vikings|"
               r"steelers|browns|titans|texans|jaguars|colts|broncos|"
               r"saints|falcons|seahawks|cardinals|commanders|bears|"
               r"patriots|raiders|giants|panthers|buccaneers|bucs)\b", re.I),
    # Generic sports
    re.compile(r"\b(nba|nfl|mlb|nhl|ufc|mma|premier league|epl|mls|"
               r"ncaa|march madness|super bowl|world series|stanley cup|"
               r"championship|playoff|finals|win.*game|beat|defeat|"
               r"vs\.?|versus)\b", re.I),
]


# eSports patterns — Counter-Strike, Valorant, LoL, Dota, etc.
_ESPORTS_PATTERNS = [
    re.compile(
        r"\b(counter-?strike|cs2?|valorant|league of legends|lol|dota|"
        r"overwatch|rocket league|fortnite|pubg|apex legends|"
        r"call of duty|cod|rainbow six|r6|starcraft|halo)\b", re.I,
    ),
    re.compile(
        r"\b(blast|esl|iem|vct|lcs|lec|lck|lpl|worlds|"
        r"major|bo[135]|map \d|round of|bracket|"
        r"furia|navi|g2|fnatic|cloud9|c9|team liquid|"
        r"t1|gen\.?g|sentinels|100 thieves|vitality|"
        r"falcons|heroic|mouz|faze|nip|astralis)\b", re.I,
    ),
]

# Soccer/football specific
_SOCCER_PATTERNS = [
    re.compile(
        r"\b(fc|sc|cf|afc|sfc|united|city|rovers|wanderers|"
        r"athletic|atletico|real madrid|barcelona|bayern|"
        r"liverpool|manchester|arsenal|chelsea|tottenham|"
        r"juventus|inter|milan|psg|dortmund|"
        r"liga|serie a|bundesliga|ligue 1|eredivisie)\b", re.I,
    ),
]


def is_sports_market(question: str) -> bool:
    """Check if a Polymarket question is about traditional sports."""
    return any(p.search(question) for p in _SPORT_PATTERNS)


def is_event_market(question: str) -> bool:
    """Check if a market is an event that should wait for resolution.

    Covers: sports, eSports, soccer, and any competitive event.
    These markets resolve when the event completes — never exit early.
    """
    return (
        any(p.search(question) for p in _SPORT_PATTERNS)
        or any(p.search(question) for p in _ESPORTS_PATTERNS)
        or any(p.search(question) for p in _SOCCER_PATTERNS)
    )


def _extract_teams(question: str) -> list[str]:
    """Extract team names from a sports question."""
    teams = []
    for pattern in _SPORT_PATTERNS[:2]:  # NBA + NFL patterns
        matches = pattern.findall(question)
        teams.extend(m.lower() for m in matches)
    return teams


class SportsFetcher:
    """Fetches odds from The Odds API and computes implied probabilities."""

    CACHE_TTL = 600  # 10 min (conserve API calls)
    TIMEOUT = 15.0

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._odds_cache: dict[str, list[dict]] = {}
        self._cache_expires: dict[str, float] = {}

        from bot.utils.circuit_breaker import CircuitBreaker
        self._breaker = CircuitBreaker(
            "odds_api", failure_threshold=3, recovery_seconds=600,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    async def get_odds_for_sport(self, sport_key: str) -> list[dict]:
        """Fetch odds for a sport from The Odds API.

        Returns list of game dicts with bookmaker odds.
        """
        if not _API_KEY:
            return []

        # Check cache
        cached = self._odds_cache.get(sport_key)
        expires = self._cache_expires.get(sport_key, 0.0)
        if cached is not None and time.monotonic() < expires:
            return cached

        if not self._breaker.allow_request():
            return cached or []

        try:
            client = await self._get_client()
            response = await client.get(
                f"{_ODDS_API_URL}/{sport_key}/odds",
                params={
                    "apiKey": _API_KEY,
                    "regions": "us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                },
            )

            if response.status_code == 429:
                logger.warning("odds_api_rate_limited")
                self._breaker.record_failure()
                return cached or []

            if response.status_code == 401:
                logger.warning("odds_api_unauthorized")
                return []

            response.raise_for_status()
            data = response.json()

            self._odds_cache[sport_key] = data
            self._cache_expires[sport_key] = time.monotonic() + self.CACHE_TTL
            self._breaker.record_success()

            logger.info(
                "odds_fetched",
                sport=sport_key,
                games=len(data),
            )
            return data

        except Exception as e:
            self._breaker.record_failure()
            logger.warning("odds_fetch_failed", sport=sport_key, error=str(e))
            return cached or []

    async def get_all_odds(self) -> dict[str, list[dict]]:
        """Fetch odds for all tracked sports. Returns sport_key → games."""
        result = {}
        for sport_key in _SPORT_KEYS:
            odds = await self.get_odds_for_sport(sport_key)
            if odds:
                result[sport_key] = odds
        return result

    def compute_implied_probability(
        self, game: dict, team_name: str,
    ) -> tuple[float, int]:
        """Compute consensus implied probability for a team from multiple bookmakers.

        Returns (probability, num_bookmakers).
        Averages across all bookmakers for robustness.
        """
        probs: list[float] = []

        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "").lower()
                    # Fuzzy match team name
                    if team_name.lower() in name.lower() or name.lower() in team_name.lower():
                        decimal_odds = outcome.get("price", 0)
                        if decimal_odds > 1.0:
                            prob = 1.0 / decimal_odds
                            probs.append(prob)

        if not probs:
            return 0.0, 0

        avg_prob = sum(probs) / len(probs)
        return round(avg_prob, 4), len(probs)

    def match_polymarket_to_game(
        self, question: str, all_odds: dict[str, list[dict]],
    ) -> tuple[float, int, str] | None:
        """Match a Polymarket question to a game and return odds probability.

        Returns (implied_prob, num_bookmakers, matched_team) or None.
        """
        if not is_sports_market(question):
            return None

        teams = _extract_teams(question)
        if not teams:
            return None

        # Search through all sports/games for a match
        for sport_key, games in all_odds.items():
            for game in games:
                home = game.get("home_team", "").lower()
                away = game.get("away_team", "").lower()

                for team in teams:
                    # Check if team name matches home or away
                    if team in home or team in away:
                        # Determine which team the question is about
                        # Default: assume question asks about the team mentioned
                        prob, n_books = self.compute_implied_probability(game, team)
                        if prob > 0 and n_books >= 2:
                            logger.info(
                                "sports_odds_matched",
                                question=question[:60],
                                team=team,
                                prob=prob,
                                bookmakers=n_books,
                                sport=sport_key,
                            )
                            return prob, n_books, team

        return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

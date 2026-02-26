"""Tests for sports odds fetcher — pattern matching and probability computation."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.research.sports_fetcher import (
    SportsFetcher,
    _extract_teams,
    is_event_market,
    is_sports_market,
)  # noqa: I001

# ---------------------------------------------------------------------------
# is_sports_market
# ---------------------------------------------------------------------------


class TestIsSportsMarket:
    def test_nba_team(self):
        assert is_sports_market("Will the Lakers win tonight?") is True

    def test_nfl_team(self):
        assert is_sports_market("Will the Chiefs win the Super Bowl?") is True

    def test_generic_nba(self):
        assert is_sports_market("NBA Finals MVP odds?") is True

    def test_nfl_keyword(self):
        assert is_sports_market("Who will win the NFL championship?") is True

    def test_ufc_keyword(self):
        assert is_sports_market("Will UFC 300 break records?") is True

    def test_match_keyword(self):
        assert is_sports_market("Will Team A beat Team B?") is True

    def test_non_sports_market(self):
        assert is_sports_market("Will Bitcoin reach $100k?") is False

    def test_political_market(self):
        assert is_sports_market("Will inflation exceed 4% in March?") is False

    def test_empty_string(self):
        assert is_sports_market("") is False

    def test_case_insensitive(self):
        assert is_sports_market("LAKERS vs CELTICS tonight") is True

    def test_playoff_keyword(self):
        assert is_sports_market("Who advances in the playoff round?") is True

    def test_march_madness(self):
        assert is_sports_market("March Madness bracket predictions") is True

    def test_versus_abbreviation(self):
        assert is_sports_market("Team A vs. Team B") is True


# ---------------------------------------------------------------------------
# is_event_market (sports + eSports + soccer)
# ---------------------------------------------------------------------------


class TestIsEventMarket:
    def test_sports_detected(self):
        assert is_event_market("Will the Warriors win game 7?") is True

    def test_esports_counter_strike(self):
        assert is_event_market("Will FURIA win the CS2 Major?") is True

    def test_esports_valorant(self):
        assert is_event_market("Valorant VCT Champions winner?") is True

    def test_esports_league_of_legends(self):
        assert is_event_market("League of Legends Worlds 2026 finals") is True

    def test_esports_team_names(self):
        assert is_event_market("Will Team Liquid win the bo3?") is True

    def test_soccer_team(self):
        assert is_event_market("Will Real Madrid win La Liga?") is True

    def test_soccer_league(self):
        assert is_event_market("Premier League top scorer?") is True

    def test_soccer_bundesliga(self):
        assert is_event_market("Bundesliga champion this season?") is True

    def test_soccer_club_prefix(self):
        assert is_event_market("Will Liverpool FC win?") is True

    def test_non_event(self):
        assert is_event_market("Will CPI exceed 3%?") is False

    def test_navi_esports(self):
        assert is_event_market("NAVI vs G2 in the IEM finals") is True


# ---------------------------------------------------------------------------
# _extract_teams
# ---------------------------------------------------------------------------


class TestExtractTeams:
    def test_single_nba_team(self):
        teams = _extract_teams("Will the Lakers win tonight?")
        assert "lakers" in teams

    def test_two_nba_teams(self):
        teams = _extract_teams("Lakers vs Celtics tonight")
        assert "lakers" in teams
        assert "celtics" in teams

    def test_nfl_team(self):
        teams = _extract_teams("Can the Chiefs beat the Eagles?")
        assert "chiefs" in teams
        assert "eagles" in teams

    def test_no_teams(self):
        teams = _extract_teams("Will Bitcoin reach $100k?")
        assert teams == []

    def test_mixed_case(self):
        teams = _extract_teams("WARRIORS vs NUGGETS")
        assert "warriors" in teams
        assert "nuggets" in teams

    def test_single_nfl(self):
        teams = _extract_teams("Will the Cowboys make the playoffs?")
        assert "cowboys" in teams


# ---------------------------------------------------------------------------
# compute_implied_probability
# ---------------------------------------------------------------------------


class TestComputeImpliedProbability:
    @pytest.fixture
    def fetcher(self):
        return SportsFetcher()

    def test_single_bookmaker(self, fetcher):
        game = {
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Los Angeles Lakers", "price": 2.0},
                                {"name": "Boston Celtics", "price": 1.8},
                            ],
                        }
                    ],
                }
            ]
        }
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == pytest.approx(0.5, abs=0.01)
        assert n == 1

    def test_multiple_bookmakers(self, fetcher):
        game = {
            "bookmakers": [
                {
                    "key": "dk",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Lakers", "price": 2.0},
                    ]}],
                },
                {
                    "key": "fd",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Lakers", "price": 4.0},
                    ]}],
                },
            ]
        }
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        # avg of 0.5 and 0.25 = 0.375
        assert prob == pytest.approx(0.375, abs=0.01)
        assert n == 2

    def test_no_match(self, fetcher):
        game = {
            "bookmakers": [
                {
                    "key": "dk",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Celtics", "price": 1.5},
                    ]}],
                }
            ]
        }
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == 0.0
        assert n == 0

    def test_non_h2h_market_ignored(self, fetcher):
        game = {
            "bookmakers": [
                {
                    "key": "dk",
                    "markets": [{"key": "spreads", "outcomes": [
                        {"name": "Lakers", "price": 1.9},
                    ]}],
                }
            ]
        }
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == 0.0
        assert n == 0

    def test_empty_bookmakers(self, fetcher):
        game = {"bookmakers": []}
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == 0.0
        assert n == 0

    def test_no_bookmakers_key(self, fetcher):
        game = {}
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == 0.0
        assert n == 0

    def test_odds_below_1_ignored(self, fetcher):
        game = {
            "bookmakers": [
                {
                    "key": "dk",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Lakers", "price": 0.5},
                    ]}],
                }
            ]
        }
        prob, n = fetcher.compute_implied_probability(game, "lakers")
        assert prob == 0.0
        assert n == 0


# ---------------------------------------------------------------------------
# match_polymarket_to_game
# ---------------------------------------------------------------------------


class TestMatchPolymarketToGame:
    @pytest.fixture
    def fetcher(self):
        return SportsFetcher()

    def _make_odds(self, home="Los Angeles Lakers", away="Boston Celtics",
                   home_price=2.0, away_price=1.8):
        return {
            "basketball_nba": [
                {
                    "home_team": home,
                    "away_team": away,
                    "bookmakers": [
                        {
                            "key": "dk",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": home, "price": home_price},
                                {"name": away, "price": away_price},
                            ]}],
                        },
                        {
                            "key": "fd",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": home, "price": home_price},
                                {"name": away, "price": away_price},
                            ]}],
                        },
                    ],
                }
            ]
        }

    def test_match_found(self, fetcher):
        odds = self._make_odds()
        result = fetcher.match_polymarket_to_game(
            "Will the Lakers win tonight?", odds,
        )
        assert result is not None
        prob, n_books, team = result
        assert prob > 0
        assert n_books >= 2
        assert team == "lakers"

    def test_no_match_non_sports(self, fetcher):
        odds = self._make_odds()
        result = fetcher.match_polymarket_to_game(
            "Will Bitcoin reach $100k?", odds,
        )
        assert result is None

    def test_no_teams_extracted(self, fetcher):
        # Sports keywords but no actual team names
        odds = self._make_odds()
        result = fetcher.match_polymarket_to_game(
            "Will the NCAA tournament be exciting?", odds,
        )
        # NCAA matches sport patterns but no team names extractable
        assert result is None

    def test_team_not_in_odds(self, fetcher):
        odds = self._make_odds(home="Phoenix Suns", away="Denver Nuggets")
        result = fetcher.match_polymarket_to_game(
            "Will the Lakers win tonight?", odds,
        )
        assert result is None

    def test_nfl_match(self, fetcher):
        odds = {
            "americanfootball_nfl": [
                {
                    "home_team": "Kansas City Chiefs",
                    "away_team": "Philadelphia Eagles",
                    "bookmakers": [
                        {
                            "key": "dk",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": "Kansas City Chiefs", "price": 1.5},
                                {"name": "Philadelphia Eagles", "price": 2.5},
                            ]}],
                        },
                        {
                            "key": "fd",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": "Kansas City Chiefs", "price": 1.5},
                                {"name": "Philadelphia Eagles", "price": 2.5},
                            ]}],
                        },
                    ],
                }
            ]
        }
        result = fetcher.match_polymarket_to_game(
            "Will the Chiefs beat the Eagles?", odds,
        )
        assert result is not None
        prob, n_books, team = result
        assert team == "chiefs"
        assert n_books >= 2

    def test_empty_odds(self, fetcher):
        result = fetcher.match_polymarket_to_game(
            "Will the Lakers win tonight?", {},
        )
        assert result is None

    def test_too_few_bookmakers(self, fetcher):
        odds = {
            "basketball_nba": [
                {
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "bookmakers": [
                        {
                            "key": "dk",
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": "Los Angeles Lakers", "price": 2.0},
                            ]}],
                        },
                    ],
                }
            ]
        }
        result = fetcher.match_polymarket_to_game(
            "Will the Lakers win tonight?", odds,
        )
        # Only 1 bookmaker, needs >= 2
        assert result is None


# ---------------------------------------------------------------------------
# get_odds_for_sport (async, mocked HTTP)
# ---------------------------------------------------------------------------


class TestGetOddsForSport:
    @pytest.fixture
    def fetcher(self):
        return SportsFetcher()

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self, fetcher):
        with patch("bot.research.sports_fetcher._API_KEY", ""):
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == []

    @pytest.mark.asyncio
    async def test_cache_hit(self, fetcher):
        fetcher._odds_cache["basketball_nba"] = [{"game": 1}]
        fetcher._cache_expires["basketball_nba"] = time.monotonic() + 600
        with patch("bot.research.sports_fetcher._API_KEY", "test-key"):
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == [{"game": 1}]

    @pytest.mark.asyncio
    async def test_successful_fetch(self, fetcher):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json = lambda: [{"id": "game1"}]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("bot.research.sports_fetcher._API_KEY", "test-key"):
            fetcher._client = mock_client
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == [{"id": "game1"}]
            assert "basketball_nba" in fetcher._odds_cache

    @pytest.mark.asyncio
    async def test_rate_limited_returns_cached(self, fetcher):
        fetcher._odds_cache["basketball_nba"] = [{"cached": True}]

        mock_response = AsyncMock()
        mock_response.status_code = 429
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("bot.research.sports_fetcher._API_KEY", "test-key"):
            fetcher._client = mock_client
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == [{"cached": True}]

    @pytest.mark.asyncio
    async def test_unauthorized_returns_empty(self, fetcher):
        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("bot.research.sports_fetcher._API_KEY", "test-key"):
            fetcher._client = mock_client
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_cached(self, fetcher):
        fetcher._odds_cache["basketball_nba"] = [{"cached": True}]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mock_client.is_closed = False

        with patch("bot.research.sports_fetcher._API_KEY", "test-key"):
            fetcher._client = mock_client
            result = await fetcher.get_odds_for_sport("basketball_nba")
            assert result == [{"cached": True}]

    @pytest.mark.asyncio
    async def test_close(self, fetcher):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        fetcher._client = mock_client
        await fetcher.close()
        mock_client.aclose.assert_awaited_once()

"""Tests for market type classification and policy enforcement."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.research.market_classifier import (
    MarketPolicy,
    MarketType,
    classify_market,
    get_policy,
)


# ---------------------------------------------------------------------------
# classify_market — SHORT_TERM
# ---------------------------------------------------------------------------


class TestClassifyShortTerm:
    def test_crypto_up_or_down(self):
        assert classify_market("Will Bitcoin go up or down in the next 5 min?") == MarketType.SHORT_TERM

    def test_eth_5_min(self):
        assert classify_market("ETH 5-min price movement?") == MarketType.SHORT_TERM

    def test_sol_opens_up(self):
        assert classify_market("Will SOL opens up or down today?") == MarketType.SHORT_TERM

    def test_btc_daily(self):
        assert classify_market("BTC daily close above $80k?") == MarketType.SHORT_TERM

    def test_crypto_hourly(self):
        assert classify_market("Ethereum hourly price direction?") == MarketType.SHORT_TERM

    def test_end_date_within_24h(self):
        soon = datetime.now(timezone.utc) + timedelta(hours=6)
        assert classify_market("Will something happen?", end_date=soon) == MarketType.SHORT_TERM

    def test_end_date_12h(self):
        soon = datetime.now(timezone.utc) + timedelta(hours=12)
        assert classify_market("Random question about markets", end_date=soon) == MarketType.SHORT_TERM

    def test_crypto_end_of_day(self):
        assert classify_market("Will Bitcoin close above $90k by end of day?") == MarketType.SHORT_TERM


# ---------------------------------------------------------------------------
# classify_market — EVENT
# ---------------------------------------------------------------------------


class TestClassifyEvent:
    def test_nba_game(self):
        assert classify_market("Will the Lakers win tonight?") == MarketType.EVENT

    def test_nfl_superbowl(self):
        assert classify_market("Will the Chiefs win the Super Bowl?") == MarketType.EVENT

    def test_esports_cs2(self):
        assert classify_market("Will FURIA win the CS2 Major?") == MarketType.EVENT

    def test_esports_valorant(self):
        assert classify_market("Valorant VCT Champions winner?") == MarketType.EVENT

    def test_esports_lol(self):
        assert classify_market("League of Legends Worlds 2026 finals") == MarketType.EVENT

    def test_soccer_real_madrid(self):
        assert classify_market("Will Real Madrid win La Liga?") == MarketType.EVENT

    def test_soccer_premier_league(self):
        assert classify_market("Premier League top scorer?") == MarketType.EVENT

    def test_ufc(self):
        assert classify_market("Will UFC 300 break records?") == MarketType.EVENT

    def test_navi_vs_g2(self):
        assert classify_market("NAVI vs G2 in the IEM finals") == MarketType.EVENT

    def test_team_liquid_bo3(self):
        assert classify_market("Will Team Liquid win the bo3?") == MarketType.EVENT

    def test_march_madness(self):
        assert classify_market("March Madness bracket predictions") == MarketType.EVENT

    def test_win_on_date(self):
        assert classify_market("Will the Celtics win on 2026-03-25?") == MarketType.EVENT

    def test_vs_pattern(self):
        assert classify_market("Antalya 2: Player A vs Player B") == MarketType.EVENT


# ---------------------------------------------------------------------------
# classify_market — LONG_TERM
# ---------------------------------------------------------------------------


class TestClassifyLongTerm:
    def test_president(self):
        assert classify_market("Will Biden be the next president?") == MarketType.LONG_TERM

    def test_ceasefire(self):
        assert classify_market("Will there be a ceasefire in Iran?") == MarketType.LONG_TERM

    def test_election(self):
        assert classify_market("Will the 2028 election be contested?") == MarketType.LONG_TERM

    def test_war(self):
        assert classify_market("Will Russia invade another country?") == MarketType.LONG_TERM

    def test_treaty(self):
        assert classify_market("Will the treaty be ratified by 2027?") == MarketType.LONG_TERM

    def test_sanctions(self):
        assert classify_market("Will sanctions on Iran be lifted?") == MarketType.LONG_TERM

    def test_end_date_beyond_7_days(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        assert classify_market("Will something happen eventually?", end_date=far) == MarketType.LONG_TERM

    def test_prime_minister(self):
        assert classify_market("Will the UK prime minister resign?") == MarketType.LONG_TERM

    def test_impeach(self):
        assert classify_market("Will the president be impeached?") == MarketType.LONG_TERM

    def test_nomination(self):
        assert classify_market("Democratic nominee for Senate in Mississippi?") == MarketType.LONG_TERM


# ---------------------------------------------------------------------------
# classify_market — ECONOMIC
# ---------------------------------------------------------------------------


class TestClassifyEconomic:
    def test_fed_rate(self):
        assert classify_market("Will the Fed cut interest rates?") == MarketType.ECONOMIC

    def test_cpi(self):
        assert classify_market("Will CPI exceed 3% in March?") == MarketType.ECONOMIC

    def test_unemployment(self):
        assert classify_market("Will unemployment rise above 5%?") == MarketType.ECONOMIC

    def test_gdp(self):
        assert classify_market("Will GDP growth exceed 2% this quarter?") == MarketType.ECONOMIC

    def test_treasury(self):
        assert classify_market("Will treasury yields hit 5%?") == MarketType.ECONOMIC

    def test_inflation(self):
        assert classify_market("Will inflation exceed expectations?") == MarketType.ECONOMIC

    def test_fomc(self):
        assert classify_market("FOMC meeting outcome prediction") == MarketType.ECONOMIC


# ---------------------------------------------------------------------------
# classify_market — WEATHER
# ---------------------------------------------------------------------------


class TestClassifyWeather:
    def test_temperature(self):
        assert classify_market("Will the temperature exceed 100°F in Phoenix?") == MarketType.WEATHER

    def test_precipitation(self):
        assert classify_market("Will there be precipitation above 2 inches?") == MarketType.WEATHER

    def test_snowfall(self):
        assert classify_market("How many inches of snow in Chicago?") == MarketType.WEATHER

    def test_cold_snap(self):
        assert classify_market("Will a cold snap hit the Midwest?") == MarketType.WEATHER

    def test_weather_forecast(self):
        assert classify_market("Weather forecast for NYC this week?") == MarketType.WEATHER


# ---------------------------------------------------------------------------
# classify_market — UNKNOWN
# ---------------------------------------------------------------------------


class TestClassifyUnknown:
    def test_empty_string(self):
        assert classify_market("") == MarketType.UNKNOWN

    def test_ambiguous(self):
        assert classify_market("Will the thing happen soon?") == MarketType.UNKNOWN

    def test_crypto_without_short_pattern(self):
        # "Will BTC hit $100k?" is crypto but no short-term pattern
        assert classify_market("Will Bitcoin hit $100k?") == MarketType.UNKNOWN

    def test_generic_question(self):
        assert classify_market("Will the company announce a merger?") == MarketType.UNKNOWN


# ---------------------------------------------------------------------------
# get_policy — returns correct policy for each type
# ---------------------------------------------------------------------------


class TestGetPolicy:
    def test_short_term_allows_all(self):
        p = get_policy(MarketType.SHORT_TERM)
        assert "market_making" in p.allowed_strategies
        assert "crypto_short_term" in p.allowed_strategies
        assert p.allow_early_exit is True
        assert p.allow_bayesian_exit is True
        assert p.allow_stop_loss is True
        assert p.stop_loss_pct == 0.15
        assert p.allow_rebalance is True

    def test_event_no_early_exit(self):
        p = get_policy(MarketType.EVENT)
        assert p.allow_early_exit is False
        assert p.allow_bayesian_exit is False
        assert p.allow_stop_loss is False
        assert p.allow_rebalance is False
        assert p.max_hold_hours == 0.0
        assert "market_making" not in p.allowed_strategies
        assert "time_decay" in p.allowed_strategies
        assert "copy_trading" in p.allowed_strategies

    def test_long_term_restrictive(self):
        p = get_policy(MarketType.LONG_TERM)
        assert p.allow_bayesian_exit is False
        assert p.allow_stop_loss is True
        assert p.stop_loss_pct == 0.35
        assert p.allow_rebalance is False
        assert "market_making" not in p.allowed_strategies
        assert "news_sniping" in p.allowed_strategies

    def test_economic_wait_for_resolution(self):
        p = get_policy(MarketType.ECONOMIC)
        assert p.allow_early_exit is False
        assert p.allow_stop_loss is False
        assert p.max_hold_hours == 0.0
        assert "time_decay" in p.allowed_strategies
        assert len(p.allowed_strategies) == 1

    def test_weather_policy(self):
        p = get_policy(MarketType.WEATHER)
        assert p.allow_early_exit is True
        assert p.allow_stop_loss is True
        assert p.stop_loss_pct == 0.25
        assert "weather_trading" in p.allowed_strategies
        assert "market_making" not in p.allowed_strategies

    def test_unknown_same_as_long_term(self):
        p_unk = get_policy(MarketType.UNKNOWN)
        p_lt = get_policy(MarketType.LONG_TERM)
        assert p_unk.allow_bayesian_exit == p_lt.allow_bayesian_exit
        assert p_unk.stop_loss_pct == p_lt.stop_loss_pct
        assert p_unk.allow_rebalance == p_lt.allow_rebalance

    def test_policy_is_frozen(self):
        p = get_policy(MarketType.SHORT_TERM)
        with pytest.raises(AttributeError):
            p.allow_early_exit = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Policy enforcement — strategy filtering
# ---------------------------------------------------------------------------


class TestPolicyEnforcement:
    def test_market_making_blocked_for_events(self):
        p = get_policy(classify_market("Will the Lakers win tonight?"))
        assert "market_making" not in p.allowed_strategies

    def test_market_making_allowed_for_short_term(self):
        p = get_policy(classify_market("BTC up or down in 5 min?"))
        assert "market_making" in p.allowed_strategies

    def test_crypto_short_blocked_for_politics(self):
        p = get_policy(classify_market("Will the president resign?"))
        assert "crypto_short_term" not in p.allowed_strategies

    def test_weather_only_td_and_weather(self):
        p = get_policy(classify_market("Will temperature exceed 100°F?"))
        assert p.allowed_strategies == frozenset({"time_decay", "weather_trading"})

    def test_stop_loss_blocked_for_events(self):
        p = get_policy(classify_market("Will FURIA win the CS2 Major?"))
        assert p.allow_stop_loss is False

    def test_stop_loss_wider_for_long_term(self):
        p = get_policy(classify_market("Will there be a ceasefire?"))
        assert p.stop_loss_pct == 0.35

    def test_bayesian_only_for_short_term(self):
        for mt in MarketType:
            p = get_policy(mt)
            if mt == MarketType.SHORT_TERM:
                assert p.allow_bayesian_exit is True
            else:
                assert p.allow_bayesian_exit is False

    def test_rebalance_only_for_short_term(self):
        for mt in MarketType:
            p = get_policy(mt)
            if mt == MarketType.SHORT_TERM:
                assert p.allow_rebalance is True
            else:
                assert p.allow_rebalance is False

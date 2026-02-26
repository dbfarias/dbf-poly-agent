"""Tests for Trade Assistant API parsing functions."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from api.routers.assistant import (
    _find_best_market,
    _format_event_summary,
    _format_market_summary,
    _get_token_and_price,
    _parse_json_field,
    detect_mode,
    extract_slug,
    extract_url,
    has_trade_intent,
    parse_amount,
    parse_intent,
)


class TestExtractUrl:
    def test_extracts_url(self):
        msg = "Buy $5 https://polymarket.com/sports/fif-ita-nir-2026-03-26"
        assert extract_url(msg) == "https://polymarket.com/sports/fif-ita-nir-2026-03-26"

    def test_no_url(self):
        assert extract_url("just a message with no link") is None

    def test_url_with_query_params(self):
        msg = "check https://polymarket.com/event/slug?tab=markets now"
        assert extract_url(msg) == "https://polymarket.com/event/slug?tab=markets"


class TestExtractSlug:
    def test_sports_url(self):
        url = "https://polymarket.com/sports/fifa-friendlies/fif-ita-nir-2026-03-26"
        assert extract_slug(url) == "fif-ita-nir-2026-03-26"

    def test_event_url(self):
        url = "https://polymarket.com/event/will-bitcoin-hit-100k"
        assert extract_slug(url) == "will-bitcoin-hit-100k"

    def test_trailing_slash(self):
        url = "https://polymarket.com/sports/fif-ita-nir/"
        assert extract_slug(url) == "fif-ita-nir"

    def test_with_query_params(self):
        url = "https://polymarket.com/event/slug?tab=markets"
        assert extract_slug(url) == "slug"


class TestParseAmount:
    def test_dollar_sign(self):
        assert parse_amount("buy $10 on Italy") == 10.0

    def test_dollar_decimal(self):
        assert parse_amount("buy $2.50 on Italy") == 2.50

    def test_word_usd(self):
        assert parse_amount("buy 7 usd on Italy") == 7.0

    def test_default(self):
        assert parse_amount("buy Italy win") == 5.0


class TestParseIntent:
    def test_buy_yes(self):
        side, outcome = parse_intent("Buy Yes on Italy win")
        assert side == "BUY"
        assert outcome == "Yes"

    def test_buy_no(self):
        side, outcome = parse_intent("Uruguay not win")
        assert side == "BUY"
        assert outcome == "No"

    def test_sell(self):
        side, outcome = parse_intent("Sell my Italy position")
        assert side == "SELL"

    def test_draw(self):
        side, outcome = parse_intent("I think it will draw")
        assert side == "BUY"
        assert outcome == "No"

    def test_against(self):
        side, outcome = parse_intent("bet against Norway")
        assert side == "BUY"
        assert outcome == "No"

    def test_default_no_keywords(self):
        side, outcome = parse_intent("Italy https://polymarket.com/...")
        assert side == "BUY"
        assert outcome == ""

    def test_win_keyword(self):
        side, outcome = parse_intent("Italy wins this game")
        assert side == "BUY"
        assert outcome == "Yes"


class TestHasTradeIntent:
    def test_buy_keyword(self):
        assert has_trade_intent("buy No on Italy") is True

    def test_sell_keyword(self):
        assert has_trade_intent("sell my position") is True

    def test_comprar_pt(self):
        assert has_trade_intent("comprar $5 neste mercado") is True

    def test_no_trade_intent(self):
        assert has_trade_intent("what about bitcoin?") is False

    def test_url_only_no_intent(self):
        assert has_trade_intent("https://polymarket.com/event/slug") is False

    def test_close_keyword(self):
        assert has_trade_intent("close this position") is True


class TestDetectMode:
    def test_execute_with_buy_and_url(self):
        msg = "buy No $5 https://polymarket.com/event/some-slug"
        assert detect_mode(msg) == "execute"

    def test_execute_with_sell_and_url(self):
        msg = "sell https://polymarket.com/event/some-slug"
        assert detect_mode(msg) == "execute"

    def test_analyze_url_no_trade(self):
        msg = "https://polymarket.com/event/some-slug"
        assert detect_mode(msg) == "analyze"

    def test_analyze_url_with_text(self):
        msg = "what do you think about https://polymarket.com/event/some-slug"
        assert detect_mode(msg) == "analyze"

    def test_search_no_url(self):
        msg = "bitcoin april"
        assert detect_mode(msg) == "search"

    def test_search_portuguese(self):
        msg = "futebol hoje"
        assert detect_mode(msg) == "search"

    def test_execute_comprar_and_url(self):
        msg = "comprar $5 https://polymarket.com/event/slug"
        assert detect_mode(msg) == "execute"


class TestFindBestMarket:
    def test_single_market(self):
        markets = [{"question": "Will X?", "outcomes": '["Yes", "No"]'}]
        mkt, outcome = _find_best_market(markets, "yes", "Yes")
        assert mkt == markets[0]
        assert outcome == "Yes"

    def test_multi_market_matches_team(self):
        markets = [
            {"question": "Will Italy win?", "outcomes": '["Yes", "No"]',
             "groupItemTitle": "Italy"},
            {"question": "Will Germany win?", "outcomes": '["Yes", "No"]',
             "groupItemTitle": "Germany"},
        ]
        mkt, outcome = _find_best_market(markets, "italy not win", "No")
        assert "Italy" in mkt["question"]
        assert outcome == "No"

    def test_defaults_to_first(self):
        markets = [
            {"question": "Market A", "outcomes": '["Yes", "No"]'},
            {"question": "Market B", "outcomes": '["Yes", "No"]'},
        ]
        mkt, _ = _find_best_market(markets, "xyz", "")
        assert mkt == markets[0]


class TestGetTokenAndPrice:
    def test_yes_token(self):
        market = {
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.75", "0.25"]',
            "clobTokenIds": '["token_yes", "token_no"]',
        }
        token, price = _get_token_and_price(market, "Yes")
        assert token == "token_yes"
        assert price == 0.75

    def test_no_token(self):
        market = {
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.75", "0.25"]',
            "clobTokenIds": '["token_yes", "token_no"]',
        }
        token, price = _get_token_and_price(market, "No")
        assert token == "token_no"
        assert price == 0.25

    def test_invalid_outcome(self):
        market = {
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.75", "0.25"]',
            "clobTokenIds": '["token_yes", "token_no"]',
        }
        token, price = _get_token_and_price(market, "Maybe")
        assert token is None
        assert price is None


class TestParseJsonField:
    def test_string(self):
        assert _parse_json_field('["a", "b"]') == ["a", "b"]

    def test_list(self):
        assert _parse_json_field(["a", "b"]) == ["a", "b"]

    def test_invalid(self):
        assert _parse_json_field("not json") == []

    def test_none(self):
        assert _parse_json_field(None) == []


class TestFormatMarketSummary:
    def test_basic_format(self):
        market = {
            "question": "Will BTC hit 100k?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.65", "0.35"]',
            "volume": "150000",
            "endDate": "2026-12-31",
        }
        result = _format_market_summary(market)
        assert "Will BTC hit 100k?" in result
        assert "Yes: 65c" in result
        assert "No: 35c" in result
        assert "$150,000" in result
        assert "2026-12-31" in result

    def test_no_volume(self):
        market = {
            "question": "Test?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.50", "0.50"]',
        }
        result = _format_market_summary(market)
        assert "Test?" in result
        assert "Volume" not in result

    def test_list_fields(self):
        market = {
            "question": "Q?",
            "outcomes": ["A", "B"],
            "outcomePrices": ["0.40", "0.60"],
        }
        result = _format_market_summary(market)
        assert "A: 40c" in result
        assert "B: 60c" in result


class TestFormatEventSummary:
    def test_basic_event(self):
        event = {
            "title": "Bitcoin 100k",
            "volume": "500000",
            "endDate": "2026-12-31",
            "markets": [
                {
                    "question": "Will BTC hit 100k by Dec?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.45", "0.55"]',
                },
            ],
        }
        result = _format_event_summary(event)
        assert "Bitcoin 100k" in result
        assert "$500,000" in result
        assert "Will BTC hit 100k by Dec?" in result
        assert "Yes:45c" in result

    def test_empty_markets(self):
        event = {"title": "Empty", "markets": []}
        result = _format_event_summary(event)
        assert "Empty" in result

    def test_limits_markets_to_five(self):
        markets = [
            {
                "question": f"Market {i}",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.50", "0.50"]',
            }
            for i in range(10)
        ]
        event = {"title": "Many", "markets": markets}
        result = _format_event_summary(event)
        assert "Market 4" in result
        assert "Market 5" not in result

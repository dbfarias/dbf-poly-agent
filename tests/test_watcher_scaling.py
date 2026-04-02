"""Tests for event-level price scaling logic."""

import time

from bot.agent.watcher_scaling import (
    CachedLevels,
    PriceLevel,
    ScaleLevelRequest,
    _extract_yes_price,
    _extract_yes_token_id,
    evaluate_scale_down,
    evaluate_scale_up,
    find_adjacent_level,
    find_our_level,
    is_cache_valid,
    parse_levels_from_event,
)


def _make_level(
    price_target: float,
    market_id: str = "",
    token_id: str = "",
    yes_price: float = 0.5,
    question: str = "",
) -> PriceLevel:
    return PriceLevel(
        price_target=price_target,
        market_id=market_id or f"mkt_{int(price_target)}",
        token_id=token_id or f"tok_{int(price_target)}",
        yes_price=yes_price,
        question=question or f"Will X hit ${int(price_target)}?",
    )


# A common set of price levels for testing
_LEVELS = [
    _make_level(110, yes_price=0.95),
    _make_level(120, yes_price=0.67),
    _make_level(130, yes_price=0.34),
    _make_level(140, yes_price=0.19),
    _make_level(150, yes_price=0.08),
]


# ---------------------------------------------------------------------------
# ScaleLevelRequest is frozen
# ---------------------------------------------------------------------------

class TestScaleLevelRequest:
    def test_frozen(self):
        req = ScaleLevelRequest(
            watcher_id=1, direction="up",
            sell_market_id="a", sell_token_id="b",
            buy_market_id="c", buy_token_id="d",
            buy_price=0.4, buy_question="Q", buy_outcome="Yes",
            from_level=120, to_level=130, reasoning="test",
        )
        assert req.direction == "up"
        try:
            req.direction = "down"  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# parse_levels_from_event
# ---------------------------------------------------------------------------

class TestParseLevelsFromEvent:
    def test_parses_basic_event(self):
        event = {
            "markets": [
                {
                    "question": "Will WTI hit (HIGH) $120 in April?",
                    "conditionId": "cond_120",
                    "outcomePrices": '["0.67","0.33"]',
                    "outcomes": '["Yes","No"]',
                    "clobTokenIds": '["tok_120_yes","tok_120_no"]',
                },
                {
                    "question": "Will WTI hit (HIGH) $130 in April?",
                    "conditionId": "cond_130",
                    "outcomePrices": '["0.34","0.66"]',
                    "outcomes": '["Yes","No"]',
                    "clobTokenIds": '["tok_130_yes","tok_130_no"]',
                },
            ]
        }
        levels = parse_levels_from_event(event)
        assert len(levels) == 2
        assert levels[0].price_target == 120
        assert levels[1].price_target == 130
        assert levels[0].market_id == "cond_120"
        assert levels[0].token_id == "tok_120_yes"
        assert levels[0].yes_price == 0.67

    def test_empty_event(self):
        assert parse_levels_from_event({}) == []
        assert parse_levels_from_event({"markets": []}) == []

    def test_skips_non_price_markets(self):
        event = {
            "markets": [
                {
                    "question": "Will Trump win the election?",
                    "conditionId": "cond_1",
                    "outcomePrices": '["0.50","0.50"]',
                    "outcomes": '["Yes","No"]',
                    "clobTokenIds": '["tok_1"]',
                },
            ]
        }
        assert parse_levels_from_event(event) == []

    def test_sorted_by_price_target(self):
        event = {
            "markets": [
                {
                    "question": "Above $150",
                    "conditionId": "c3",
                    "outcomePrices": '["0.10"]',
                    "outcomes": '["Yes"]',
                    "clobTokenIds": '["t3"]',
                },
                {
                    "question": "Above $100",
                    "conditionId": "c1",
                    "outcomePrices": '["0.90"]',
                    "outcomes": '["Yes"]',
                    "clobTokenIds": '["t1"]',
                },
                {
                    "question": "Above $120",
                    "conditionId": "c2",
                    "outcomePrices": '["0.50"]',
                    "outcomes": '["Yes"]',
                    "clobTokenIds": '["t2"]',
                },
            ]
        }
        levels = parse_levels_from_event(event)
        assert [lv.price_target for lv in levels] == [100, 120, 150]


# ---------------------------------------------------------------------------
# _extract_yes_price / _extract_yes_token_id
# ---------------------------------------------------------------------------

class TestExtractHelpers:
    def test_yes_price_json_string(self):
        m = {"outcomePrices": '["0.67","0.33"]', "outcomes": '["Yes","No"]'}
        assert _extract_yes_price(m) == 0.67

    def test_yes_price_list(self):
        m = {"outcomePrices": [0.67, 0.33], "outcomes": ["Yes", "No"]}
        assert _extract_yes_price(m) == 0.67

    def test_yes_price_no_data(self):
        assert _extract_yes_price({}) is None

    def test_yes_token_json_string(self):
        m = {"clobTokenIds": '["tok_yes","tok_no"]'}
        assert _extract_yes_token_id(m) == "tok_yes"

    def test_yes_token_empty(self):
        assert _extract_yes_token_id({}) == ""


# ---------------------------------------------------------------------------
# find_our_level / find_adjacent_level
# ---------------------------------------------------------------------------

class TestFindLevel:
    def test_find_our_level_found(self):
        result = find_our_level(_LEVELS, "mkt_120")
        assert result is not None
        assert result.price_target == 120

    def test_find_our_level_not_found(self):
        assert find_our_level(_LEVELS, "mkt_999") is None

    def test_find_next_up(self):
        our = _LEVELS[1]  # 120
        next_up = find_adjacent_level(_LEVELS, our, "up")
        assert next_up is not None
        assert next_up.price_target == 130

    def test_find_next_down(self):
        our = _LEVELS[1]  # 120
        next_down = find_adjacent_level(_LEVELS, our, "down")
        # 110 has yes_price=0.95 which is > 0.98 boundary check fails
        # Actually 0.95 < 0.98, so it passes
        assert next_down is not None
        assert next_down.price_target == 110

    def test_no_up_from_top(self):
        our = _LEVELS[-1]  # 150
        assert find_adjacent_level(_LEVELS, our, "up") is None

    def test_no_down_from_bottom(self):
        our = _LEVELS[0]  # 110
        assert find_adjacent_level(_LEVELS, our, "down") is None

    def test_skips_resolved_level_up(self):
        """Levels with yes_price >= 0.98 are considered resolved."""
        levels = [
            _make_level(110, yes_price=0.50),
            _make_level(120, yes_price=0.99),  # resolved
            _make_level(130, yes_price=0.30),
        ]
        our = levels[0]
        next_up = find_adjacent_level(levels, our, "up")
        # next up is 120 but it's resolved (0.99 >= 0.98), so returns None
        assert next_up is None

    def test_skips_resolved_level_down(self):
        """Levels with yes_price <= 0.02 are considered resolved."""
        levels = [
            _make_level(110, yes_price=0.01),  # resolved
            _make_level(120, yes_price=0.50),
            _make_level(130, yes_price=0.30),
        ]
        our = levels[1]
        next_down = find_adjacent_level(levels, our, "down")
        assert next_down is None


# ---------------------------------------------------------------------------
# evaluate_scale_up / evaluate_scale_down
# ---------------------------------------------------------------------------

class TestEvaluateScaling:
    def test_scale_up_triggers(self):
        """Scale up when price >= 0.80 and next level <= 0.50."""
        our = _make_level(120, yes_price=0.85)
        next_up = _make_level(130, yes_price=0.40)
        assert evaluate_scale_up(0.85, our, next_up) is True

    def test_scale_up_price_too_low(self):
        our = _make_level(120, yes_price=0.65)
        next_up = _make_level(130, yes_price=0.40)
        assert evaluate_scale_up(0.65, our, next_up) is False

    def test_scale_up_next_too_expensive(self):
        our = _make_level(120, yes_price=0.85)
        next_up = _make_level(130, yes_price=0.60)
        assert evaluate_scale_up(0.85, our, next_up) is False

    def test_scale_up_no_next(self):
        our = _make_level(120, yes_price=0.85)
        assert evaluate_scale_up(0.85, our, None) is False

    def test_scale_down_triggers(self):
        """Scale down when price < 85% of entry and lower level >= 0.70."""
        our = _make_level(120, yes_price=0.40)
        next_down = _make_level(110, yes_price=0.75)
        assert evaluate_scale_down(0.40, 0.67, our, next_down) is True

    def test_scale_down_price_not_low_enough(self):
        our = _make_level(120, yes_price=0.60)
        next_down = _make_level(110, yes_price=0.80)
        assert evaluate_scale_down(0.60, 0.67, our, next_down) is False

    def test_scale_down_next_not_safe_enough(self):
        our = _make_level(120, yes_price=0.40)
        next_down = _make_level(110, yes_price=0.50)
        assert evaluate_scale_down(0.40, 0.67, our, next_down) is False

    def test_scale_down_no_next(self):
        our = _make_level(120, yes_price=0.40)
        assert evaluate_scale_down(0.40, 0.67, our, None) is False

    def test_scale_down_zero_entry(self):
        our = _make_level(120, yes_price=0.40)
        next_down = _make_level(110, yes_price=0.80)
        assert evaluate_scale_down(0.40, 0.0, our, next_down) is False


# ---------------------------------------------------------------------------
# Cache validity
# ---------------------------------------------------------------------------

class TestCacheValidity:
    def test_none_is_invalid(self):
        assert is_cache_valid(None) is False

    def test_fresh_cache_valid(self):
        cached = CachedLevels(levels=(), fetched_at=time.time())
        assert is_cache_valid(cached) is True

    def test_stale_cache_invalid(self):
        cached = CachedLevels(levels=(), fetched_at=time.time() - 400)
        assert is_cache_valid(cached) is False

    def test_just_under_ttl_valid(self):
        cached = CachedLevels(levels=(), fetched_at=time.time() - 299)
        assert is_cache_valid(cached) is True

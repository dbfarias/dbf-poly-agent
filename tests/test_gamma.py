"""Tests for Gamma API client transformations."""

from bot.polymarket.gamma import _transform_clob_market, _transform_gamma_api_market


class TestTransformGammaApiMarket:
    def test_basic_fields(self):
        raw = {
            "conditionId": "0xabc",
            "question": "Will X happen?",
            "slug": "will-x-happen",
            "endDate": "2026-03-01T12:00:00Z",
            "endDateIso": "2026-03-01",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.92", "0.08"]',
            "volume": "1234.56",
            "liquidity": "500.0",
            "active": True,
            "closed": False,
            "archived": False,
            "groupItemTitle": "Sports",
            "clobTokenIds": '["tok1", "tok2"]',
            "acceptingOrders": True,
            "negRisk": False,
            "bestBid": 0.91,
            "bestAsk": 0.93,
            "volume24hr": 200.0,
        }
        result = _transform_gamma_api_market(raw)
        assert result["conditionId"] == "0xabc"
        assert result["question"] == "Will X happen?"
        # Uses endDate (full ISO), not endDateIso (date-only)
        assert result["endDateIso"] == "2026-03-01T12:00:00Z"
        assert result["volume"] == 1234.56
        assert result["liquidity"] == 500.0
        assert result["negRisk"] is False
        assert result["bestBid"] == 0.91
        assert result["bestAsk"] == 0.93
        assert result["volume24hr"] == 200.0

    def test_prefers_end_date_over_end_date_iso(self):
        raw = {
            "conditionId": "0x1",
            "endDate": "2026-03-01T15:30:00Z",
            "endDateIso": "2026-03-01",
        }
        result = _transform_gamma_api_market(raw)
        assert result["endDateIso"] == "2026-03-01T15:30:00Z"

    def test_falls_back_to_end_date_iso(self):
        raw = {"conditionId": "0x1", "endDateIso": "2026-03-01"}
        result = _transform_gamma_api_market(raw)
        assert result["endDateIso"] == "2026-03-01"

    def test_neg_risk_market(self):
        raw = {"conditionId": "0x1", "negRisk": True}
        result = _transform_gamma_api_market(raw)
        assert result["negRisk"] is True

    def test_missing_volume_defaults_zero(self):
        raw = {"conditionId": "0x1"}
        result = _transform_gamma_api_market(raw)
        assert result["volume"] == 0.0
        assert result["volume24hr"] == 0.0

    def test_none_volume_defaults_zero(self):
        raw = {"conditionId": "0x1", "volume": None, "volume24hr": None}
        result = _transform_gamma_api_market(raw)
        assert result["volume"] == 0.0
        assert result["volume24hr"] == 0.0


class TestTransformClobMarket:
    def test_basic_fields(self):
        raw = {
            "condition_id": "0xabc",
            "question": "Will Y happen?",
            "market_slug": "will-y-happen",
            "end_date_iso": "2026-04-01T00:00:00Z",
            "tokens": [
                {"outcome": "Yes", "price": 0.85, "token_id": "tok_yes"},
                {"outcome": "No", "price": 0.15, "token_id": "tok_no"},
            ],
            "tags": ["Crypto", "Politics"],
        }
        result = _transform_clob_market(raw)
        assert result["conditionId"] == "0xabc"
        assert result["question"] == "Will Y happen?"
        assert result["groupItemTitle"] == "Crypto"  # First non-generic tag

    def test_neg_risk_passed_through(self):
        raw = {"condition_id": "0x1", "tokens": [], "tags": [], "neg_risk": True}
        result = _transform_clob_market(raw)
        assert result["negRisk"] is True

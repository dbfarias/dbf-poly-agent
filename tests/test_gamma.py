"""Tests for Gamma API client transformations and GammaClient methods."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.polymarket.gamma import (
    GammaClient,
    _best_category,
    _transform_clob_market,
    _transform_gamma_api_market,
)
from bot.polymarket.types import GammaMarket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_gamma_market_dict(
    condition_id: str = "0xabc",
    question: str = "Will X happen?",
    active: bool = True,
    closed: bool = False,
    archived: bool = False,
    accepting_orders: bool = True,
    volume_24h: float = 100.0,
    end_date_iso: str = "2099-01-01T00:00:00Z",
) -> dict:
    """Build a minimal raw Gamma API market dict."""
    return {
        "conditionId": condition_id,
        "question": question,
        "slug": "will-x-happen",
        "endDate": end_date_iso,
        "endDateIso": end_date_iso,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.70", "0.30"]',
        "volume": "500.0",
        "liquidity": "200.0",
        "active": active,
        "closed": closed,
        "archived": archived,
        "groupItemTitle": "Sports",
        "clobTokenIds": '["tok1", "tok2"]',
        "acceptingOrders": accepting_orders,
        "negRisk": False,
        "bestBid": 0.69,
        "bestAsk": 0.71,
        "volume24hr": volume_24h,
    }


def make_clob_market_dict(
    condition_id: str = "0xdef",
    question: str = "Will Y happen?",
    active: bool = True,
    closed: bool = False,
    accepting_orders: bool = True,
    archived: bool = False,
) -> dict:
    return {
        "condition_id": condition_id,
        "question": question,
        "market_slug": "will-y-happen",
        "end_date_iso": "2099-06-01T00:00:00Z",
        "active": active,
        "closed": closed,
        "archived": archived,
        "accepting_orders": accepting_orders,
        "tokens": [
            {"outcome": "Yes", "price": 0.80, "token_id": "tok_yes"},
            {"outcome": "No", "price": 0.20, "token_id": "tok_no"},
        ],
        "tags": ["Crypto", "Politics"],
    }


def make_httpx_response(json_data, status_code: int = 200) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        error = httpx.HTTPStatusError(
            f"{status_code}",
            request=MagicMock(),
            response=resp,
        )
        resp.raise_for_status.side_effect = error
    return resp


def make_gamma_client() -> GammaClient:
    """Return a GammaClient with mocked HTTP clients."""
    client = GammaClient()
    client._clob_client = AsyncMock()
    client._gamma_client = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Module-level function tests
# ---------------------------------------------------------------------------


class TestTransformGammaApiMarket:
    def test_basic_fields(self):
        raw = make_gamma_market_dict()
        result = _transform_gamma_api_market(raw)
        assert result["conditionId"] == "0xabc"
        assert result["question"] == "Will X happen?"
        assert result["endDateIso"] == "2099-01-01T00:00:00Z"
        assert result["volume"] == 500.0
        assert result["liquidity"] == 200.0
        assert result["negRisk"] is False
        assert result["bestBid"] == 0.69
        assert result["bestAsk"] == 0.71
        assert result["volume24hr"] == 100.0

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
        raw = make_clob_market_dict()
        result = _transform_clob_market(raw)
        assert result["conditionId"] == "0xdef"
        assert result["question"] == "Will Y happen?"
        assert result["groupItemTitle"] == "Crypto"  # First non-generic tag

    def test_neg_risk_passed_through(self):
        raw = {"condition_id": "0x1", "tokens": [], "tags": [], "neg_risk": True}
        result = _transform_clob_market(raw)
        assert result["negRisk"] is True

    def test_outcomes_and_prices_serialized_as_json(self):
        raw = {
            "condition_id": "0x1",
            "tokens": [
                {"outcome": "Yes", "price": 0.65, "token_id": "t1"},
                {"outcome": "No", "price": 0.35, "token_id": "t2"},
            ],
            "tags": [],
        }
        result = _transform_clob_market(raw)
        assert json.loads(result["outcomes"]) == ["Yes", "No"]
        assert json.loads(result["outcomePrices"]) == [0.65, 0.35]
        assert json.loads(result["clobTokenIds"]) == ["t1", "t2"]

    def test_empty_tokens_gives_empty_lists(self):
        raw = {"condition_id": "0x1", "tokens": [], "tags": []}
        result = _transform_clob_market(raw)
        assert json.loads(result["outcomes"]) == []
        assert json.loads(result["outcomePrices"]) == []

    def test_all_generic_tags_give_empty_category(self):
        raw = {
            "condition_id": "0x1",
            "tokens": [],
            "tags": ["Politics", "Elections"],
        }
        result = _transform_clob_market(raw)
        assert result["groupItemTitle"] == ""

    def test_volume_and_liquidity_default_zero(self):
        raw = {"condition_id": "0x1", "tokens": [], "tags": []}
        result = _transform_clob_market(raw)
        assert result["volume"] == 0.0
        assert result["liquidity"] == 0.0

    def test_active_false_preserved(self):
        raw = {
            "condition_id": "0x1",
            "tokens": [],
            "tags": [],
            "active": False,
            "closed": True,
        }
        result = _transform_clob_market(raw)
        assert result["active"] is False
        assert result["closed"] is True


class TestBestCategory:
    """Tests for _best_category tag selection."""

    def test_returns_first_non_generic_tag(self):
        assert _best_category(["Crypto", "Politics"]) == "Crypto"

    def test_skips_generic_returns_specific(self):
        assert _best_category(["Politics", "Crypto"]) == "Crypto"

    def test_all_generic_returns_empty(self):
        assert _best_category(["Politics", "Elections"]) == ""

    def test_empty_list_returns_empty(self):
        assert _best_category([]) == ""

    def test_single_non_generic_tag(self):
        assert _best_category(["Sports"]) == "Sports"

    def test_single_generic_tag(self):
        assert _best_category(["Politics"]) == ""

    def test_all_generic_tags_covered(self):
        all_generic = [
            "Politics",
            "Elections",
            "Primaries",
            "primary elections",
            "US Election",
            "Midterms",
            "Global Elections",
        ]
        assert _best_category(all_generic) == ""

    def test_generic_mixed_with_specific(self):
        assert _best_category(["Midterms", "US Election", "Finance"]) == "Finance"


# ---------------------------------------------------------------------------
# GammaClient initialization / teardown
# ---------------------------------------------------------------------------


class TestGammaClientInit:
    @pytest.mark.asyncio
    async def test_initialize_creates_http_clients(self):
        """initialize() should set up both clob and gamma http clients."""
        client = GammaClient()
        assert client._clob_client is None
        assert client._gamma_client is None

        with patch("bot.polymarket.gamma.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            await client.initialize()

        assert mock_cls.call_count == 2

    @pytest.mark.asyncio
    async def test_close_calls_aclose_on_both(self):
        """close() should call aclose on both HTTP clients."""
        client = GammaClient()
        clob_mock = AsyncMock()
        gamma_mock = AsyncMock()
        client._clob_client = clob_mock
        client._gamma_client = gamma_mock

        await client.close()

        clob_mock.aclose.assert_awaited_once()
        gamma_mock.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_handles_none_clients(self):
        """close() is safe when clients were never initialized."""
        client = GammaClient()
        # Should not raise even though both clients are None
        await client.close()


# ---------------------------------------------------------------------------
# _fetch_gamma_markets
# ---------------------------------------------------------------------------


class TestFetchGammaMarkets:
    @pytest.mark.asyncio
    async def test_returns_parsed_markets(self):
        """Should parse active, non-closed markets into GammaMarket objects."""
        client = make_gamma_client()
        raw = [make_gamma_market_dict(condition_id="0xaaa")]
        client._gamma_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        result = await client._fetch_gamma_markets({"active": "true"})

        assert len(result) == 1
        assert isinstance(result[0], GammaMarket)
        assert result[0].condition_id == "0xaaa"

    @pytest.mark.asyncio
    async def test_skips_inactive_markets(self):
        """Markets with active=False should be filtered out."""
        client = make_gamma_client()
        raw = [make_gamma_market_dict(active=False)]
        client._gamma_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        result = await client._fetch_gamma_markets({})

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_closed_markets(self):
        """Markets with closed=True should be filtered out."""
        client = make_gamma_client()
        raw = [make_gamma_market_dict(closed=True)]
        client._gamma_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        result = await client._fetch_gamma_markets({})

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_invalid_market_with_parse_error(self):
        """A market that fails validation should be silently skipped."""
        client = make_gamma_client()
        # One valid, one that will cause a parse exception via patching validate
        raw = [make_gamma_market_dict(condition_id="0x1")]
        client._gamma_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        with patch(
            "bot.polymarket.gamma.GammaMarket.model_validate",
            side_effect=ValueError("bad"),
        ):
            result = await client._fetch_gamma_markets({})

        assert result == []

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        """HTTP errors should propagate (so retry decorator can handle them)."""
        client = make_gamma_client()
        resp = make_httpx_response({}, status_code=500)
        client._gamma_client.get = AsyncMock(return_value=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client._fetch_gamma_markets({})

    @pytest.mark.asyncio
    async def test_returns_multiple_valid_markets(self):
        """All active, non-closed markets should be parsed."""
        client = make_gamma_client()
        raw = [
            make_gamma_market_dict(condition_id="0x1"),
            make_gamma_market_dict(condition_id="0x2"),
            make_gamma_market_dict(condition_id="0x3", active=False),  # skipped
        ]
        client._gamma_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        result = await client._fetch_gamma_markets({})

        assert len(result) == 2
        ids = {m.condition_id for m in result}
        assert ids == {"0x1", "0x2"}


# ---------------------------------------------------------------------------
# _fetch_clob_markets
# ---------------------------------------------------------------------------


class TestFetchClobMarkets:
    @pytest.mark.asyncio
    async def test_returns_parsed_clob_markets(self):
        """Should parse active CLOB markets into GammaMarket objects."""
        client = make_gamma_client()
        raw_data = {"data": [make_clob_market_dict(condition_id="0xccc")]}
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        result = await client._fetch_clob_markets()

        assert len(result) == 1
        assert isinstance(result[0], GammaMarket)

    @pytest.mark.asyncio
    async def test_skips_inactive_clob_markets(self):
        """Inactive CLOB markets should be filtered."""
        client = make_gamma_client()
        raw_data = {
            "data": [
                make_clob_market_dict(active=False),
                make_clob_market_dict(condition_id="0xgood"),
            ]
        }
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        result = await client._fetch_clob_markets()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_closed_clob_markets(self):
        """Closed CLOB markets should be filtered."""
        client = make_gamma_client()
        raw_data = {"data": [make_clob_market_dict(closed=True)]}
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        result = await client._fetch_clob_markets()

        assert result == []

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        """Result should be capped to the given limit."""
        client = make_gamma_client()
        raw_data = {
            "data": [
                make_clob_market_dict(condition_id=f"0x{i}") for i in range(10)
            ]
        }
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        result = await client._fetch_clob_markets(limit=3)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_skips_invalid_market_with_parse_error(self):
        """Markets that fail validation should be silently skipped."""
        client = make_gamma_client()
        raw_data = {"data": [make_clob_market_dict()]}
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        with patch(
            "bot.polymarket.gamma.GammaMarket.model_validate",
            side_effect=ValueError("bad"),
        ):
            result = await client._fetch_clob_markets()

        assert result == []

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        """HTTP errors should propagate."""
        client = make_gamma_client()
        resp = make_httpx_response({}, status_code=503)
        client._clob_client.get = AsyncMock(return_value=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client._fetch_clob_markets()

    @pytest.mark.asyncio
    async def test_handles_empty_data_list(self):
        """Empty data list should return empty result."""
        client = make_gamma_client()
        raw_data = {"data": []}
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw_data)
        )

        result = await client._fetch_clob_markets()

        assert result == []


# ---------------------------------------------------------------------------
# get_markets
# ---------------------------------------------------------------------------


class TestGetMarkets:
    @pytest.mark.asyncio
    async def test_returns_gamma_markets_when_available(self):
        """get_markets() should prefer Gamma API result."""
        client = make_gamma_client()
        gamma_market = GammaMarket.model_validate(
            _transform_gamma_api_market(make_gamma_market_dict(condition_id="0xgamma"))
        )

        with patch.object(
            client, "_fetch_gamma_markets", new=AsyncMock(return_value=[gamma_market])
        ):
            result = await client.get_markets()

        assert len(result) == 1
        assert result[0].condition_id == "0xgamma"

    @pytest.mark.asyncio
    async def test_falls_back_to_clob_when_gamma_empty(self):
        """get_markets() should fall back to CLOB when Gamma returns empty list."""
        client = make_gamma_client()
        clob_market = GammaMarket.model_validate(
            _transform_clob_market(make_clob_market_dict(condition_id="0xclob"))
        )

        with (
            patch.object(
                client, "_fetch_gamma_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client, "_fetch_clob_markets", new=AsyncMock(return_value=[clob_market])
            ),
        ):
            result = await client.get_markets()

        assert len(result) == 1
        assert result[0].condition_id == "0xclob"

    @pytest.mark.asyncio
    async def test_falls_back_to_clob_when_gamma_raises(self):
        """get_markets() should fall back to CLOB when Gamma API throws."""
        client = make_gamma_client()
        clob_market = GammaMarket.model_validate(
            _transform_clob_market(make_clob_market_dict(condition_id="0xclob"))
        )

        with (
            patch.object(
                client,
                "_fetch_gamma_markets",
                new=AsyncMock(side_effect=Exception("network error")),
            ),
            patch.object(
                client, "_fetch_clob_markets", new=AsyncMock(return_value=[clob_market])
            ),
        ):
            result = await client.get_markets()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_passes_params_to_gamma(self):
        """get_markets() should pass correct query params to Gamma API."""
        client = make_gamma_client()
        mock_fetch = AsyncMock(return_value=[])
        clob_market = GammaMarket.model_validate(
            _transform_clob_market(make_clob_market_dict())
        )

        with (
            patch.object(client, "_fetch_gamma_markets", new=mock_fetch),
            patch.object(
                client, "_fetch_clob_markets", new=AsyncMock(return_value=[clob_market])
            ),
        ):
            await client.get_markets(limit=50, offset=10, active=True, closed=False)

        called_params = mock_fetch.call_args[0][0]
        assert called_params["limit"] == 50
        assert called_params["offset"] == 10
        assert called_params["active"] == "true"
        assert called_params["closed"] == "false"


# ---------------------------------------------------------------------------
# get_market (single)
# ---------------------------------------------------------------------------


class TestGetMarket:
    @pytest.mark.asyncio
    async def test_returns_gamma_market_for_valid_id(self):
        """get_market() should return a GammaMarket for a valid condition_id."""
        client = make_gamma_client()
        raw = make_clob_market_dict(condition_id="0xmkt1")
        client._clob_client.get = AsyncMock(
            return_value=make_httpx_response(raw)
        )

        result = await client.get_market("0xmkt1")

        assert result is not None
        assert isinstance(result, GammaMarket)

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        """get_market() should return None when the market is not found (404)."""
        client = make_gamma_client()
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 404
        error = httpx.HTTPStatusError("not found", request=MagicMock(), response=resp)
        resp.raise_for_status = MagicMock(side_effect=error)
        client._clob_client.get = AsyncMock(return_value=resp)

        result = await client.get_market("0xnonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_reraises_non_404_http_errors(self):
        """Non-404 HTTP errors should propagate from get_market()."""
        client = make_gamma_client()
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 503
        error = httpx.HTTPStatusError(
            "service unavailable", request=MagicMock(), response=resp
        )
        resp.raise_for_status = MagicMock(side_effect=error)
        client._clob_client.get = AsyncMock(return_value=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_market("0xmkt1")


# ---------------------------------------------------------------------------
# get_active_markets
# ---------------------------------------------------------------------------


class TestGetActiveMarkets:
    @pytest.mark.asyncio
    async def test_returns_accepting_non_archived_markets(self):
        """get_active_markets() should filter to accepting_orders + not archived."""
        client = make_gamma_client()
        good = GammaMarket.model_validate(
            _transform_gamma_api_market(
                make_gamma_market_dict(condition_id="0xgood", accepting_orders=True, archived=False)
            )
        )
        bad_no_orders = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xbad"), "acceptingOrders": False}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[good, bad_no_orders]),
        ):
            result = await client.get_active_markets()

        assert len(result) == 1
        assert result[0].condition_id == "0xgood"

    @pytest.mark.asyncio
    async def test_falls_back_to_clob_when_gamma_empty(self):
        """Should use CLOB fallback when Gamma returns empty list."""
        client = make_gamma_client()
        clob_market = GammaMarket.model_validate(
            _transform_clob_market(make_clob_market_dict(condition_id="0xclob"))
        )

        with (
            patch.object(
                client, "_fetch_gamma_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client, "_fetch_clob_markets", new=AsyncMock(return_value=[clob_market])
            ),
        ):
            result = await client.get_active_markets()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_clob_on_gamma_exception(self):
        """Should use CLOB fallback when Gamma API throws."""
        client = make_gamma_client()
        clob_market = GammaMarket.model_validate(
            _transform_clob_market(make_clob_market_dict(condition_id="0xclob"))
        )

        with (
            patch.object(
                client,
                "_fetch_gamma_markets",
                new=AsyncMock(side_effect=Exception("timeout")),
            ),
            patch.object(
                client, "_fetch_clob_markets", new=AsyncMock(return_value=[clob_market])
            ),
        ):
            result = await client.get_active_markets()

        assert len(result) == 1


# ---------------------------------------------------------------------------
# search_markets
# ---------------------------------------------------------------------------


class TestSearchMarkets:
    @pytest.mark.asyncio
    async def test_returns_matching_markets(self):
        """search_markets() should filter by case-insensitive substring match."""
        client = make_gamma_client()
        m1 = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0x1"), "question": "Will Bitcoin hit $100k?"}
            )
        )
        m2 = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0x2"), "question": "Will Ethereum rise?"}
            )
        )

        with patch.object(
            client, "get_active_markets", new=AsyncMock(return_value=[m1, m2])
        ):
            result = await client.search_markets("bitcoin")

        assert len(result) == 1
        assert "Bitcoin" in result[0].question

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self):
        """Query matching should be case-insensitive."""
        client = make_gamma_client()
        m = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(), "question": "Will ETHEREUM reach $5k?"}
            )
        )

        with patch.object(
            client, "get_active_markets", new=AsyncMock(return_value=[m])
        ):
            result = await client.search_markets("ethereum")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        """Result should be capped to limit."""
        client = make_gamma_client()
        markets = [
            GammaMarket.model_validate(
                _transform_gamma_api_market(
                    {**make_gamma_market_dict(condition_id=f"0x{i}"), "question": f"Will crypto{i} rise?"}
                )
            )
            for i in range(10)
        ]

        with patch.object(
            client, "get_active_markets", new=AsyncMock(return_value=markets)
        ):
            result = await client.search_markets("crypto", limit=3)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_match(self):
        """Should return empty list when query matches nothing."""
        client = make_gamma_client()
        m = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(), "question": "Will Bitcoin rise?"}
            )
        )

        with patch.object(
            client, "get_active_markets", new=AsyncMock(return_value=[m])
        ):
            result = await client.search_markets("ethereum")

        assert result == []


# ---------------------------------------------------------------------------
# get_short_term_markets
# ---------------------------------------------------------------------------


class TestGetShortTermMarkets:
    @pytest.mark.asyncio
    async def test_returns_markets_within_volume_threshold(self):
        """Should return markets with volume_24h >= min_volume_24h."""
        client = make_gamma_client()
        high_vol = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xhigh"), "volume24hr": 200.0}
            )
        )
        low_vol = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xlow"), "volume24hr": 10.0}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[high_vol, low_vol]),
        ):
            result = await client.get_short_term_markets(min_volume_24h=50.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xhigh"

    @pytest.mark.asyncio
    async def test_filters_archived_markets(self):
        """Archived markets should be excluded even if volume is sufficient."""
        client = make_gamma_client()
        archived = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xarch"),
                    "archived": True,
                    "volume24hr": 500.0,
                }
            )
        )

        with patch.object(
            client, "_fetch_gamma_markets", new=AsyncMock(return_value=[archived])
        ):
            result = await client.get_short_term_markets(min_volume_24h=50.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_non_accepting_markets(self):
        """Markets not accepting orders should be excluded."""
        client = make_gamma_client()
        not_accepting = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xna"),
                    "acceptingOrders": False,
                    "volume24hr": 500.0,
                }
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[not_accepting]),
        ):
            result = await client.get_short_term_markets(min_volume_24h=50.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_gamma_exception(self):
        """Should return empty list when Gamma API fails."""
        client = make_gamma_client()

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(side_effect=Exception("timeout")),
        ):
            result = await client.get_short_term_markets()

        assert result == []

    @pytest.mark.asyncio
    async def test_results_sorted_by_end_date(self):
        """Markets should be sorted ascending by end_date."""
        client = make_gamma_client()
        m1 = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xlater"),
                    "endDate": "2099-06-01T00:00:00Z",
                    "volume24hr": 200.0,
                }
            )
        )
        m2 = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xearlier"),
                    "endDate": "2099-03-01T00:00:00Z",
                    "volume24hr": 200.0,
                }
            )
        )

        with patch.object(
            client, "_fetch_gamma_markets", new=AsyncMock(return_value=[m1, m2])
        ):
            result = await client.get_short_term_markets(min_volume_24h=50.0)

        assert result[0].condition_id == "0xearlier"
        assert result[1].condition_id == "0xlater"

    @pytest.mark.asyncio
    async def test_market_with_no_end_date_sorted_last(self):
        """Markets with no end_date (None) should sort to the end."""
        client = make_gamma_client()
        no_date = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xnodate"),
                    "endDate": "",
                    "endDateIso": "",
                    "volume24hr": 200.0,
                }
            )
        )
        with_date = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xwithdate"),
                    "endDate": "2099-01-01T00:00:00Z",
                    "volume24hr": 200.0,
                }
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[no_date, with_date]),
        ):
            result = await client.get_short_term_markets(min_volume_24h=0.0)

        assert result[0].condition_id == "0xwithdate"
        assert result[1].condition_id == "0xnodate"


# ---------------------------------------------------------------------------
# get_near_resolution_markets
# ---------------------------------------------------------------------------


class TestGetNearResolutionMarkets:
    @pytest.mark.asyncio
    async def test_returns_short_term_markets_when_available(self):
        """Should delegate to get_short_term_markets when it returns results."""
        client = make_gamma_client()
        m = GammaMarket.model_validate(
            _transform_gamma_api_market(make_gamma_market_dict(condition_id="0xnear"))
        )

        with patch.object(
            client, "get_short_term_markets", new=AsyncMock(return_value=[m])
        ):
            result = await client.get_near_resolution_markets(hours=24.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xnear"

    @pytest.mark.asyncio
    async def test_fallback_client_side_filtering_when_short_term_empty(self):
        """When get_short_term_markets returns empty, should filter active markets."""
        from datetime import timedelta

        client = make_gamma_client()
        now_utc = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        soon = now_utc + timedelta(hours=12)
        far_future = now_utc + timedelta(hours=200)

        near_market = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xnear"),
                    "endDate": soon.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "volume24hr": 100.0,
                }
            )
        )
        far_market = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xfar"),
                    "endDate": far_future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "volume24hr": 100.0,
                }
            )
        )

        with (
            patch.object(
                client, "get_short_term_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client,
                "get_active_markets",
                new=AsyncMock(return_value=[near_market, far_market]),
            ),
        ):
            result = await client.get_near_resolution_markets(hours=24.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xnear"

    @pytest.mark.asyncio
    async def test_fallback_skips_markets_with_no_end_date(self):
        """Markets with no end_date should be skipped in client-side fallback."""
        client = make_gamma_client()
        no_date_market = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xnodate"),
                    "endDate": "",
                    "endDateIso": "",
                    "volume24hr": 100.0,
                }
            )
        )

        with (
            patch.object(
                client, "get_short_term_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client,
                "get_active_markets",
                new=AsyncMock(return_value=[no_date_market]),
            ),
        ):
            result = await client.get_near_resolution_markets(hours=48.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_fallback_skips_markets_already_expired(self):
        """Markets with end_date in the past should not be included."""
        from datetime import timedelta

        client = make_gamma_client()
        now_utc = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        past = now_utc - timedelta(hours=1)

        expired = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {
                    **make_gamma_market_dict(condition_id="0xexpired"),
                    "endDate": past.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "volume24hr": 100.0,
                }
            )
        )

        with (
            patch.object(
                client, "get_short_term_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client, "get_active_markets", new=AsyncMock(return_value=[expired])
            ),
        ):
            result = await client.get_near_resolution_markets(hours=48.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_fallback_handles_naive_datetime_end_date(self):
        """Markets whose end_date is timezone-naive should have UTC attached."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import PropertyMock

        client = make_gamma_client()
        now_utc = datetime.now(timezone.utc)
        # Naive datetime 12 hours from now (no tzinfo)
        naive_soon = now_utc.replace(tzinfo=None) + timedelta(hours=12)

        market = GammaMarket.model_validate(
            _transform_gamma_api_market(make_gamma_market_dict(condition_id="0xnaive"))
        )

        with (
            patch.object(
                client, "get_short_term_markets", new=AsyncMock(return_value=[])
            ),
            patch.object(
                client, "get_active_markets", new=AsyncMock(return_value=[market])
            ),
            patch.object(
                type(market),
                "end_date",
                new_callable=PropertyMock,
                return_value=naive_soon,
            ),
        ):
            result = await client.get_near_resolution_markets(hours=24.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xnaive"


# ---------------------------------------------------------------------------
# get_high_volume_markets
# ---------------------------------------------------------------------------


class TestGetHighVolumeMarkets:
    @pytest.mark.asyncio
    async def test_returns_only_accepting_order_markets(self):
        """Should return markets where accepting_orders is True."""
        client = make_gamma_client()
        accepting = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xyes"), "acceptingOrders": True}
            )
        )
        not_accepting = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xno"), "acceptingOrders": False}
            )
        )

        with patch.object(
            client, "get_markets", new=AsyncMock(return_value=[accepting, not_accepting])
        ):
            result = await client.get_high_volume_markets()

        assert len(result) == 1
        assert result[0].condition_id == "0xyes"

    @pytest.mark.asyncio
    async def test_passes_limit_to_get_markets(self):
        """Should forward limit parameter to get_markets()."""
        client = make_gamma_client()
        mock_get = AsyncMock(return_value=[])

        with patch.object(client, "get_markets", new=mock_get):
            await client.get_high_volume_markets(limit=25)

        mock_get.assert_awaited_once_with(limit=25)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_markets(self):
        """Should return empty list when no markets are available."""
        client = make_gamma_client()

        with patch.object(client, "get_markets", new=AsyncMock(return_value=[])):
            result = await client.get_high_volume_markets()

        assert result == []


# ---------------------------------------------------------------------------
# get_new_markets
# ---------------------------------------------------------------------------


class TestGetNewMarkets:
    @pytest.mark.asyncio
    async def test_returns_markets_above_min_volume(self):
        """Should filter markets by minimum total volume."""
        client = make_gamma_client()
        high_vol = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xhigh"), "volume": "200.0"}
            )
        )
        low_vol = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xlow"), "volume": "5.0"}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[high_vol, low_vol]),
        ):
            result = await client.get_new_markets(min_volume=10.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xhigh"

    @pytest.mark.asyncio
    async def test_filters_archived_and_non_accepting(self):
        """Should exclude archived and non-accepting markets."""
        client = make_gamma_client()
        archived = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xarch"), "archived": True}
            )
        )
        not_accepting = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xna"), "acceptingOrders": False}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[archived, not_accepting]),
        ):
            result = await client.get_new_markets()

        assert result == []

    @pytest.mark.asyncio
    async def test_passes_correct_params(self):
        """Should pass order=startDate ascending=false to Gamma API."""
        client = make_gamma_client()
        mock_fetch = AsyncMock(return_value=[])

        with patch.object(client, "_fetch_gamma_markets", new=mock_fetch):
            await client.get_new_markets(limit=50)

        params = mock_fetch.call_args[0][0]
        assert params["order"] == "startDate"
        assert params["ascending"] == "false"
        assert params["limit"] == 50

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        """Should return empty list when Gamma API fails."""
        client = make_gamma_client()

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(side_effect=Exception("timeout")),
        ):
            result = await client.get_new_markets()

        assert result == []


# ---------------------------------------------------------------------------
# get_trending_markets
# ---------------------------------------------------------------------------


class TestGetTrendingMarkets:
    @pytest.mark.asyncio
    async def test_returns_markets_above_min_volume_24h(self):
        """Should filter markets by minimum 24h volume."""
        client = make_gamma_client()
        trending = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xtrend"), "volume24hr": 500.0}
            )
        )
        low_activity = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xlow"), "volume24hr": 20.0}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[trending, low_activity]),
        ):
            result = await client.get_trending_markets(min_volume_24h=100.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xtrend"

    @pytest.mark.asyncio
    async def test_passes_correct_params(self):
        """Should pass order=volume24hr ascending=false to Gamma API."""
        client = make_gamma_client()
        mock_fetch = AsyncMock(return_value=[])

        with patch.object(client, "_fetch_gamma_markets", new=mock_fetch):
            await client.get_trending_markets(limit=75)

        params = mock_fetch.call_args[0][0]
        assert params["order"] == "volume24hr"
        assert params["ascending"] == "false"
        assert params["limit"] == 75

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        """Should return empty list when Gamma API fails."""
        client = make_gamma_client()

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(side_effect=Exception("timeout")),
        ):
            result = await client.get_trending_markets()

        assert result == []


# ---------------------------------------------------------------------------
# get_breaking_markets
# ---------------------------------------------------------------------------


class TestGetBreakingMarkets:
    @pytest.mark.asyncio
    async def test_returns_recent_high_activity_markets(self):
        """Should return markets with volume_24h above threshold."""
        client = make_gamma_client()
        breaking = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xbreak"), "volume24hr": 200.0}
            )
        )
        quiet = GammaMarket.model_validate(
            _transform_gamma_api_market(
                {**make_gamma_market_dict(condition_id="0xquiet"), "volume24hr": 10.0}
            )
        )

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(return_value=[breaking, quiet]),
        ):
            result = await client.get_breaking_markets(min_volume_24h=50.0)

        assert len(result) == 1
        assert result[0].condition_id == "0xbreak"

    @pytest.mark.asyncio
    async def test_passes_start_date_min_param(self):
        """Should include start_date_min param based on max_age_hours."""
        client = make_gamma_client()
        mock_fetch = AsyncMock(return_value=[])

        with patch.object(client, "_fetch_gamma_markets", new=mock_fetch):
            await client.get_breaking_markets(max_age_hours=12.0)

        params = mock_fetch.call_args[0][0]
        assert "start_date_min" in params
        assert params["order"] == "volume24hr"
        assert params["ascending"] == "false"

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        """Should return empty list when Gamma API fails."""
        client = make_gamma_client()

        with patch.object(
            client,
            "_fetch_gamma_markets",
            new=AsyncMock(side_effect=Exception("network")),
        ):
            result = await client.get_breaking_markets()

        assert result == []

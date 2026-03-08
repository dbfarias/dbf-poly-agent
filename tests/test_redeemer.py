"""Tests for PositionRedeemer with on-chain verification and NegRisk support."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.polymarket.redeemer import (
    CHAIN_ID,
    MIN_GAS_PRICE,
    PositionRedeemer,
    RedeemablePosition,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_w3():
    """Create a mock Web3 instance with connected RPC."""
    w3 = MagicMock()
    w3.is_connected.return_value = True
    w3.eth.gas_price = 50 * 10**9  # 50 gwei
    w3.eth.get_transaction_count.return_value = 42
    w3.eth.send_raw_transaction.return_value = b"\xab" * 32

    account = MagicMock()
    account.address = "0x1234567890abcdef1234567890abcdef12345678"
    account.sign_transaction.return_value = MagicMock(raw_transaction=b"\x00" * 100)
    w3.eth.account.from_key.return_value = account

    return w3


@pytest.fixture
def mock_ctf():
    """Create a mock CTF contract."""
    ctf = MagicMock()
    ctf.functions.payoutDenominator.return_value.call.return_value = 1
    ctf.functions.payoutNumerators.return_value.call.return_value = 1
    ctf.functions.balanceOf.return_value.call.return_value = 1000
    ctf.functions.isApprovedForAll.return_value.call.return_value = True
    ctf.functions.redeemPositions.return_value.build_transaction.return_value = {"tx": "data"}
    return ctf


@pytest.fixture
def mock_neg_risk():
    """Create a mock NegRisk adapter contract."""
    neg = MagicMock()
    neg.functions.redeemPositions.return_value.build_transaction.return_value = {"tx": "data"}
    return neg


PROXY_ADDRESS = "0x" + "aa" * 20  # valid hex address


@pytest.fixture
def redeemer(mock_w3, mock_ctf, mock_neg_risk):
    """Create a fully initialized PositionRedeemer with mocked web3."""
    r = PositionRedeemer(proxy_address=PROXY_ADDRESS)
    r._w3 = mock_w3
    r._ctf = mock_ctf
    r._neg_risk = mock_neg_risk
    r._account = mock_w3.eth.account.from_key("0x" + "ab" * 32)
    r._initialized = True
    return r


CONDITION_ID = "0x" + "aa" * 32


# ---------------------------------------------------------------------------
# Unit Tests: RedeemablePosition (immutable dataclass)
# ---------------------------------------------------------------------------

class TestRedeemablePosition:
    def test_frozen(self):
        pos = RedeemablePosition(
            condition_id="0xabc",
            token_id="12345",
            size=10.0,
            is_neg_risk=False,
            winning_index_sets=[1],
        )
        with pytest.raises(AttributeError):
            pos.size = 20.0  # type: ignore[misc]

    def test_fields(self):
        pos = RedeemablePosition(
            condition_id="0xabc",
            token_id="99",
            size=5.5,
            is_neg_risk=True,
            winning_index_sets=[1, 2],
        )
        assert pos.condition_id == "0xabc"
        assert pos.token_id == "99"
        assert pos.size == 5.5
        assert pos.is_neg_risk is True
        assert pos.winning_index_sets == [1, 2]


# ---------------------------------------------------------------------------
# Unit Tests: Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_default_proxy_from_env(self):
        with patch.dict(os.environ, {"POLY_PROXY_ADDRESS": "0xenvproxy"}):
            r = PositionRedeemer()
            assert r._proxy_address == "0xenvproxy"

    def test_explicit_proxy_overrides_env(self):
        with patch.dict(os.environ, {"POLY_PROXY_ADDRESS": "0xenvproxy"}):
            r = PositionRedeemer(proxy_address="0xexplicit")
            assert r._proxy_address == "0xexplicit"

    def test_no_proxy_defaults_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove POLY_PROXY_ADDRESS if present
            os.environ.pop("POLY_PROXY_ADDRESS", None)
            r = PositionRedeemer()
            assert r._proxy_address == ""


# ---------------------------------------------------------------------------
# Unit Tests: On-chain checks
# ---------------------------------------------------------------------------

class TestOnChainChecks:
    def test_is_resolved_true(self, redeemer, mock_ctf):
        mock_ctf.functions.payoutDenominator.return_value.call.return_value = 1
        assert redeemer.is_resolved(CONDITION_ID) is True

    def test_is_resolved_false(self, redeemer, mock_ctf):
        mock_ctf.functions.payoutDenominator.return_value.call.return_value = 0
        assert redeemer.is_resolved(CONDITION_ID) is False

    def test_winning_index_sets_outcome_0_wins(self, redeemer, mock_ctf):
        """Outcome 0 wins → index set [1]."""
        def numerator_side_effect(cond, index):
            mock = MagicMock()
            mock.call.return_value = 1 if index == 0 else 0
            return mock
        mock_ctf.functions.payoutNumerators.side_effect = numerator_side_effect
        assert redeemer.get_winning_index_sets(CONDITION_ID) == [1]

    def test_winning_index_sets_outcome_1_wins(self, redeemer, mock_ctf):
        """Outcome 1 wins → index set [2]."""
        def numerator_side_effect(cond, index):
            mock = MagicMock()
            mock.call.return_value = 1 if index == 1 else 0
            return mock
        mock_ctf.functions.payoutNumerators.side_effect = numerator_side_effect
        assert redeemer.get_winning_index_sets(CONDITION_ID) == [2]

    def test_winning_index_sets_both_win(self, redeemer, mock_ctf):
        """Both outcomes win (refund scenario) → [1, 2]."""
        def numerator_side_effect(cond, index):
            mock = MagicMock()
            mock.call.return_value = 1
            return mock
        mock_ctf.functions.payoutNumerators.side_effect = numerator_side_effect
        assert redeemer.get_winning_index_sets(CONDITION_ID) == [1, 2]

    def test_winning_index_sets_none_win(self, redeemer, mock_ctf):
        """No outcome wins → empty list."""
        def numerator_side_effect(cond, index):
            mock = MagicMock()
            mock.call.return_value = 0
            return mock
        mock_ctf.functions.payoutNumerators.side_effect = numerator_side_effect
        assert redeemer.get_winning_index_sets(CONDITION_ID) == []

    def test_get_balance(self, redeemer, mock_ctf):
        mock_ctf.functions.balanceOf.return_value.call.return_value = 5000
        balance = redeemer.get_balance("12345")
        assert balance == 5000

    def test_get_balance_custom_address(self, redeemer, mock_ctf):
        redeemer.get_balance("12345", address="0x" + "bb" * 20)
        mock_ctf.functions.balanceOf.assert_called()

    def test_is_approved_for_neg_risk(self, redeemer, mock_ctf):
        mock_ctf.functions.isApprovedForAll.return_value.call.return_value = True
        assert redeemer.is_approved_for_neg_risk() is True

    def test_is_not_approved_for_neg_risk(self, redeemer, mock_ctf):
        mock_ctf.functions.isApprovedForAll.return_value.call.return_value = False
        assert redeemer.is_approved_for_neg_risk() is False


# ---------------------------------------------------------------------------
# Unit Tests: Gas handling
# ---------------------------------------------------------------------------

class TestGasHandling:
    def test_gas_price_above_minimum(self, redeemer, mock_w3):
        mock_w3.eth.gas_price = 50 * 10**9
        assert redeemer._gas_price() == 50 * 10**9

    def test_gas_price_below_minimum_uses_floor(self, redeemer, mock_w3):
        mock_w3.eth.gas_price = 5 * 10**9  # 5 gwei, below 30 gwei floor
        assert redeemer._gas_price() == MIN_GAS_PRICE


# ---------------------------------------------------------------------------
# Unit Tests: redeem()
# ---------------------------------------------------------------------------

class TestRedeem:
    @pytest.mark.asyncio
    async def test_redeem_standard_market(self, redeemer, mock_ctf, mock_w3):
        tx_hash = await redeemer.redeem(CONDITION_ID)
        assert tx_hash is not None
        mock_ctf.functions.redeemPositions.assert_called_once()

    @pytest.mark.asyncio
    async def test_redeem_neg_risk_market(self, redeemer, mock_neg_risk):
        tx_hash = await redeemer.redeem(CONDITION_ID, is_neg_risk=True)
        assert tx_hash is not None
        mock_neg_risk.functions.redeemPositions.assert_called_once()

    @pytest.mark.asyncio
    async def test_redeem_skips_unresolved(self, redeemer, mock_ctf):
        mock_ctf.functions.payoutDenominator.return_value.call.return_value = 0
        result = await redeemer.redeem(CONDITION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_redeem_skips_no_winners(self, redeemer, mock_ctf):
        """Resolved but no winning outcomes → skip."""
        def numerator_side_effect(cond, index):
            mock = MagicMock()
            mock.call.return_value = 0
            return mock
        mock_ctf.functions.payoutNumerators.side_effect = numerator_side_effect
        result = await redeemer.redeem(CONDITION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_redeem_with_explicit_index_sets(self, redeemer, mock_ctf):
        """Explicit index_sets bypasses auto-detection."""
        tx_hash = await redeemer.redeem(CONDITION_ID, index_sets=[2])
        assert tx_hash is not None

    @pytest.mark.asyncio
    async def test_redeem_exception_returns_none(self, redeemer, mock_ctf):
        mock_ctf.functions.redeemPositions.side_effect = Exception("rpc error")
        result = await redeemer.redeem(CONDITION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_redeem_not_initialized_auto_inits(self):
        """If not initialized, redeem tries to initialize first."""
        r = PositionRedeemer(proxy_address="0xtest")
        r._initialized = False
        with patch.object(r, "initialize", new_callable=AsyncMock, return_value=False):
            result = await r.redeem(CONDITION_ID)
        assert result is None


# ---------------------------------------------------------------------------
# Unit Tests: get_redeemable_positions()
# ---------------------------------------------------------------------------

def _mock_aiohttp_session(api_response, status=200):
    """Helper to create a properly nested aiohttp mock."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=api_response)

    # The response object is used as async context manager from session.get()
    mock_get_cm = AsyncMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_get_cm

    # The session itself is used as async context manager from ClientSession()
    mock_cs_cm = MagicMock()
    mock_cs_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cs_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_cs_cm


class TestGetRedeemablePositions:
    @pytest.mark.asyncio
    async def test_returns_resolved_positions(self, redeemer, mock_ctf):
        api_response = [
            {"conditionId": CONDITION_ID, "tokenId": "111", "size": "10.0", "negRisk": False},
            {"conditionId": "0x" + "bb" * 32, "tokenId": "222", "size": "5.0", "negRisk": True},
        ]

        with patch("aiohttp.ClientSession", return_value=_mock_aiohttp_session(api_response)):
            positions = await redeemer.get_redeemable_positions()

        assert len(positions) == 2
        assert positions[0].condition_id == CONDITION_ID
        assert positions[0].is_neg_risk is False
        assert positions[1].is_neg_risk is True

    @pytest.mark.asyncio
    async def test_skips_zero_size(self, redeemer, mock_ctf):
        api_response = [
            {"conditionId": CONDITION_ID, "tokenId": "111", "size": "0", "negRisk": False},
        ]

        with patch("aiohttp.ClientSession", return_value=_mock_aiohttp_session(api_response)):
            positions = await redeemer.get_redeemable_positions()

        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_skips_unresolved(self, redeemer, mock_ctf):
        mock_ctf.functions.payoutDenominator.return_value.call.return_value = 0

        api_response = [
            {"conditionId": CONDITION_ID, "tokenId": "111", "size": "10.0", "negRisk": False},
        ]

        with patch("aiohttp.ClientSession", return_value=_mock_aiohttp_session(api_response)):
            positions = await redeemer.get_redeemable_positions()

        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, redeemer):
        with patch("aiohttp.ClientSession", return_value=_mock_aiohttp_session([], status=500)):
            positions = await redeemer.get_redeemable_positions()

        assert positions == []

    @pytest.mark.asyncio
    async def test_not_initialized_returns_empty(self):
        r = PositionRedeemer(proxy_address="0xtest")
        r._initialized = False
        with patch.object(r, "initialize", new_callable=AsyncMock, return_value=False):
            result = await r.get_redeemable_positions()
        assert result == []


# ---------------------------------------------------------------------------
# Unit Tests: redeem_all()
# ---------------------------------------------------------------------------

class TestRedeemAll:
    @pytest.mark.asyncio
    async def test_redeems_all_positions(self, redeemer):
        positions = [
            RedeemablePosition("0xaaa", "111", 10.0, False, [1]),
            RedeemablePosition("0xbbb", "222", 5.0, True, [2]),
        ]
        mock_get = AsyncMock(return_value=positions)
        mock_redeem = AsyncMock(side_effect=["0xtx1", "0xtx2"])
        with patch.object(redeemer, "get_redeemable_positions", mock_get):
            with patch.object(redeemer, "redeem", mock_redeem):
                hashes = await redeemer.redeem_all()
        assert hashes == ["0xtx1", "0xtx2"]

    @pytest.mark.asyncio
    async def test_skips_failed_redeems(self, redeemer):
        positions = [
            RedeemablePosition("0xaaa", "111", 10.0, False, [1]),
            RedeemablePosition("0xbbb", "222", 5.0, False, [2]),
        ]
        mock_get = AsyncMock(return_value=positions)
        mock_redeem = AsyncMock(side_effect=["0xtx1", None])
        with patch.object(redeemer, "get_redeemable_positions", mock_get):
            with patch.object(redeemer, "redeem", mock_redeem):
                hashes = await redeemer.redeem_all()
        assert hashes == ["0xtx1"]

    @pytest.mark.asyncio
    async def test_no_positions_returns_empty(self, redeemer):
        mock_get = AsyncMock(return_value=[])
        with patch.object(redeemer, "get_redeemable_positions", mock_get):
            hashes = await redeemer.redeem_all()
        assert hashes == []


# ---------------------------------------------------------------------------
# Unit Tests: tx_params
# ---------------------------------------------------------------------------

class TestTxParams:
    def test_tx_params_structure(self, redeemer, mock_w3):
        params = redeemer._tx_params()
        assert params["chainId"] == CHAIN_ID
        assert params["gas"] == 200_000
        assert params["nonce"] == 42
        assert params["gasPrice"] >= MIN_GAS_PRICE


# ---------------------------------------------------------------------------
# Integration: initialize()
# ---------------------------------------------------------------------------

class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_derives_proxy_when_empty(self):
        """When no proxy address given, derive it from private key."""
        r = PositionRedeemer(proxy_address="")

        mock_account = MagicMock()
        mock_account.address = "0x" + "ab" * 20

        # Set up state manually (web3 import is complex to mock)
        r._w3 = MagicMock()
        r._ctf = MagicMock()
        r._neg_risk = MagicMock()
        r._account = mock_account
        r._initialized = True

        # Simulate proxy derivation path
        with patch(
            "bot.polymarket.client.derive_proxy_wallet",
            return_value="0xderived",
        ):
            if not r._proxy_address:
                from bot.polymarket.client import derive_proxy_wallet
                r._proxy_address = derive_proxy_wallet(
                    mock_account.address,
                )

        assert r._proxy_address == "0xderived"

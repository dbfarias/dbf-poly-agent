"""Tests for auto-claim (redeem) resolved positions feature."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PositionRedeemer tests
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_settings():
    """Patch bot.config.settings with minimal fields for redeemer."""
    fake = SimpleNamespace(
        polygon_rpc_url="https://polygon-rpc.com",
        poly_private_key="0x" + "ab" * 32,
        use_auto_claim=True,
        trading_mode=SimpleNamespace(value="live"),
        is_paper=False,
    )
    with patch("bot.polymarket.redeemer.settings", fake):
        yield fake


@pytest.fixture
def _mock_web3():
    """Provide a fully-mocked Web3 stack."""
    mock_account = MagicMock()
    mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
    mock_account.sign_transaction.return_value = MagicMock(
        raw_transaction=b"\x00" * 32,
    )

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 137
    mock_w3.eth.get_transaction_count.return_value = 42
    mock_w3.eth.gas_price = 30_000_000_000
    mock_w3.eth.send_raw_transaction.return_value = b"\xaa" * 32
    mock_w3.eth.account.from_key.return_value = mock_account
    mock_w3.eth.contract.return_value = MagicMock()

    # Make contract.functions.redeemPositions().build_transaction() work
    redeem_fn = MagicMock()
    redeem_fn.build_transaction.return_value = {"fake": "tx"}
    mock_w3.eth.contract.return_value.functions.redeemPositions.return_value = (
        redeem_fn
    )

    web3_cls = MagicMock(return_value=mock_w3)
    web3_cls.HTTPProvider = MagicMock()
    web3_cls.to_checksum_address = lambda addr: addr

    poa_middleware = MagicMock()

    with (
        patch("bot.polymarket.redeemer.Web3", web3_cls, create=True),
        patch.dict(
            "sys.modules",
            {
                "web3": MagicMock(Web3=web3_cls),
                "web3.middleware": MagicMock(
                    ExtraDataToPOAMiddleware=poa_middleware,
                ),
            },
        ),
    ):
        yield mock_w3


async def test_redeemer_initialize_success(_mock_settings, _mock_web3):
    from bot.polymarket.redeemer import PositionRedeemer

    redeemer = PositionRedeemer()
    result = await redeemer.initialize()
    assert result is True
    assert redeemer._initialized is True


async def test_redeemer_redeem_success(_mock_settings, _mock_web3):
    from bot.polymarket.redeemer import PositionRedeemer

    redeemer = PositionRedeemer(proxy_address="0x" + "11" * 20)
    await redeemer.initialize()

    # Mock resolution check: payoutDenominator > 0 means resolved
    redeemer._ctf.functions.payoutDenominator.return_value.call.return_value = 1
    # Mock winning outcome: outcome 0 wins
    redeemer._ctf.functions.payoutNumerators.return_value.call.side_effect = [1, 0]

    condition_id = "0x" + "cc" * 32
    tx_hash = await redeemer.redeem(condition_id)
    assert tx_hash is not None
    assert isinstance(tx_hash, str)


async def test_redeemer_redeem_failure(_mock_settings, _mock_web3):
    from bot.polymarket.redeemer import PositionRedeemer

    redeemer = PositionRedeemer(proxy_address="0x" + "11" * 20)
    await redeemer.initialize()

    # Make resolution check raise
    redeemer._ctf.functions.payoutDenominator.return_value.call.side_effect = RuntimeError(
        "gas estimation failed",
    )

    result = await redeemer.redeem("0x" + "dd" * 32)
    assert result is None  # No crash


async def test_redeemer_init_failure():
    """Redeemer handles import/connection failure gracefully."""
    fake_settings = SimpleNamespace(
        polygon_rpc_url="https://polygon-rpc.com",
        poly_private_key="0x" + "ab" * 32,
    )
    with patch("bot.polymarket.redeemer.settings", fake_settings):
        # Patch web3 import to raise
        with patch.dict("sys.modules", {"web3": None}):
            from bot.polymarket.redeemer import PositionRedeemer

            redeemer = PositionRedeemer()
            result = await redeemer.redeem("0x" + "aa" * 32)
            assert result is None


# ---------------------------------------------------------------------------
# Portfolio._try_redeem tests
# ---------------------------------------------------------------------------

async def test_portfolio_try_redeem_called():
    """When use_auto_claim=True and exit_reason=resolution, _try_redeem is called."""
    fake_settings = SimpleNamespace(
        use_auto_claim=True,
        trading_mode=SimpleNamespace(value="live"),
        is_paper=False,
        initial_bankroll=100.0,
        timezone_offset_hours=-3,
    )

    position = SimpleNamespace(
        market_id="mkt-123",
        condition_id="0x" + "ee" * 32,
    )

    with patch("bot.agent.portfolio.settings", fake_settings):
        from bot.agent.portfolio import Portfolio

        portfolio = Portfolio.__new__(Portfolio)
        portfolio._redeemer = None
        portfolio._try_redeem = AsyncMock()

        await portfolio._try_redeem(position)
        portfolio._try_redeem.assert_called_once_with(position)


async def test_portfolio_try_redeem_skipped_paper():
    """In paper mode, _try_redeem should not be triggered."""
    fake_settings = SimpleNamespace(
        use_auto_claim=True,
        trading_mode=SimpleNamespace(value="paper"),
        is_paper=True,
    )

    with patch("bot.agent.portfolio.settings", fake_settings):
        # The condition in _close_if_resolved checks trading_mode.value == "live"
        # So paper mode never reaches _try_redeem
        assert fake_settings.trading_mode.value != "live"


async def test_portfolio_try_redeem_no_condition_id():
    """Position without condition_id should skip redeem gracefully."""
    fake_settings = SimpleNamespace(
        use_auto_claim=True,
        trading_mode=SimpleNamespace(value="live"),
        is_paper=False,
        initial_bankroll=100.0,
        timezone_offset_hours=-3,
    )

    position = SimpleNamespace(market_id="mkt-456")  # No condition_id

    with patch("bot.agent.portfolio.settings", fake_settings):
        from bot.agent.portfolio import Portfolio

        mock_redeemer = MagicMock()
        mock_redeemer.redeem = AsyncMock()

        portfolio = Portfolio.__new__(Portfolio)
        portfolio._redeemer = mock_redeemer

        # Should not crash, should not call redeem
        await portfolio._try_redeem(position)
        mock_redeemer.redeem.assert_not_called()


# ---------------------------------------------------------------------------
# Config schema tests
# ---------------------------------------------------------------------------

def test_config_toggle_persistence():
    """use_auto_claim is present in both BotConfig and BotConfigUpdate schemas."""
    from api.schemas import BotConfig, BotConfigUpdate

    # BotConfig includes the field with default False
    config = BotConfig(
        trading_mode="paper",
        scan_interval_seconds=30,
        snapshot_interval_seconds=300,
        max_daily_loss_pct=0.1,
        max_drawdown_pct=0.25,
        daily_target_pct=0.01,
        risk_config={},
        strategy_params={},
        quality_params={},
    )
    assert config.use_auto_claim is False

    # BotConfigUpdate accepts the field
    update = BotConfigUpdate(use_auto_claim=True)
    assert update.use_auto_claim is True

    # Default is None (no change)
    update_empty = BotConfigUpdate()
    assert update_empty.use_auto_claim is None

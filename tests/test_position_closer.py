"""Tests for bot/agent/position_closer.py — PositionCloser class."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.position_closer import PositionCloser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(
    market_id="mkt1",
    token_id="token1",
    size=10.0,
    current_price=0.50,
    avg_price=0.55,
    question="Will X?",
    outcome="Yes",
    category="crypto",
    strategy="time_decay",
    unrealized_pnl=None,
    created_at=None,
):
    """Return a lightweight position mock with common attributes."""
    pos = MagicMock()
    pos.market_id = market_id
    pos.token_id = token_id
    pos.size = size
    pos.current_price = current_price
    pos.avg_price = avg_price
    pos.question = question
    pos.outcome = outcome
    pos.category = category
    pos.strategy = strategy
    pos.unrealized_pnl = (
        unrealized_pnl if unrealized_pnl is not None else (current_price - avg_price) * size
    )
    pos.created_at = created_at if created_at is not None else datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    return pos


def _make_trade(status="filled", trade_id=42):
    """Return a lightweight trade mock."""
    trade = MagicMock()
    trade.status = status
    trade.id = trade_id
    return trade


def _make_signal(
    market_id="mkt_new",
    edge=0.06,
    strategy="value_betting",
    question="New signal?",
    token_id="token_new",
    market_price=0.60,
    outcome="Yes",
    estimated_prob=0.66,
    metadata=None,
):
    """Return a lightweight signal mock."""
    sig = MagicMock()
    sig.market_id = market_id
    sig.edge = edge
    sig.strategy = strategy
    sig.question = question
    sig.token_id = token_id
    sig.side = MagicMock()
    sig.side.value = "BUY"
    sig.market_price = market_price
    sig.outcome = outcome
    sig.estimated_prob = estimated_prob
    sig.metadata = metadata or {"category": "crypto"}
    return sig


def _make_closer():
    """Build a PositionCloser with mocked dependencies."""
    om = AsyncMock()
    pf = AsyncMock()
    rm = MagicMock()
    closer = PositionCloser(order_manager=om, portfolio=pf, risk_manager=rm)
    return closer, om, pf, rm


# ---------------------------------------------------------------------------
# Patches applied to every test
# ---------------------------------------------------------------------------

_PATCH_PREFIX = "bot.agent.position_closer"


def _common_patches():
    """Return a dict of context-manager patches used by most tests."""
    return {
        "log_exit": patch(f"{_PATCH_PREFIX}.log_exit_triggered", new_callable=AsyncMock),
        "log_closed": patch(f"{_PATCH_PREFIX}.log_position_closed", new_callable=AsyncMock),
        "log_rebalance": patch(f"{_PATCH_PREFIX}.log_rebalance", new_callable=AsyncMock),
        "event_bus": patch(f"{_PATCH_PREFIX}.event_bus"),
        "async_session": patch(f"{_PATCH_PREFIX}.async_session"),
        "settings": patch(f"{_PATCH_PREFIX}.settings"),
    }


def _enter_patches(patches: dict) -> dict:
    """Enter all patches and return the mocks keyed by the same names."""
    mocks = {}
    for key, p in patches.items():
        mocks[key] = p.start()
    return mocks


def _stop_patches(patches: dict):
    for p in patches.values():
        p.stop()


# ---------------------------------------------------------------------------
# 1. close_position — paper mode (trade.status == "filled")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_paper_filled():
    """Paper mode: trade is 'filled' immediately — records PnL, updates DB, emits event."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pos = _make_position()
        trade = _make_trade(status="filled", trade_id=7)
        om.close_position = AsyncMock(return_value=trade)
        pf.record_trade_close = AsyncMock(return_value=-0.50)

        # Set up async_session context manager
        mock_session = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.update_status = AsyncMock()
        mock_repo.close_trade_for_position = AsyncMock()

        mocks["async_session"].return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mocks["async_session"].return_value.__aexit__ = AsyncMock(return_value=False)
        mocks["event_bus"].emit = AsyncMock()

        with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await closer.close_position(pos)

        # Portfolio PnL recorded
        pf.record_trade_close.assert_awaited_once_with(pos.market_id, pos.current_price)
        rm.update_daily_pnl.assert_called_once_with(-0.50)

        # Event emitted with correct args
        mocks["event_bus"].emit.assert_awaited_once()
        call_kwargs = mocks["event_bus"].emit.call_args
        assert call_kwargs[1]["pnl"] == -0.50
        assert call_kwargs[1]["side"] == "SELL"
        assert call_kwargs[1]["trade_event"] == "sell_filled"

        # Activity log
        mocks["log_closed"].assert_awaited_once()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 2. close_position — live mode (trade.status == "pending")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_live_pending():
    """Live mode: trade is 'pending' — should NOT record PnL, only log pending."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pos = _make_position()
        trade = _make_trade(status="pending")
        om.close_position = AsyncMock(return_value=trade)
        mocks["event_bus"].emit = AsyncMock()

        await closer.close_position(pos)

        pf.record_trade_close.assert_not_awaited()
        rm.update_daily_pnl.assert_not_called()
        mocks["event_bus"].emit.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 2b. close_position — exit_reason propagated to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_propagates_exit_reason():
    """exit_reason param is passed to close_trade_for_position and log."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pos = _make_position()
        trade = _make_trade(status="filled", trade_id=8)
        om.close_position = AsyncMock(return_value=trade)
        pf.record_trade_close = AsyncMock(return_value=0.25)

        mock_session = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.update_status = AsyncMock()
        mock_repo.close_trade_for_position = AsyncMock()

        mocks["async_session"].return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mocks["async_session"].return_value.__aexit__ = AsyncMock(return_value=False)
        mocks["event_bus"].emit = AsyncMock()

        with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await closer.close_position(pos, exit_reason="stop_loss (15% loss)")

        # Verify exit_reason propagated to DB
        mock_repo.close_trade_for_position.assert_awaited_once_with(
            pos.market_id, 0.25, "stop_loss (15% loss)",
        )

        # Verify exit_reason propagated to activity log
        log_call = mocks["log_closed"].call_args
        assert log_call.kwargs["exit_reason"] == "stop_loss (15% loss)"
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 3. close_position — order_manager returns None (early return)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_order_manager_returns_none():
    """If order_manager.close_position returns None, exit immediately."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pos = _make_position()
        om.close_position = AsyncMock(return_value=None)
        mocks["event_bus"].emit = AsyncMock()

        await closer.close_position(pos)

        pf.record_trade_close.assert_not_awaited()
        rm.update_daily_pnl.assert_not_called()
        mocks["event_bus"].emit.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 4. handle_sell_fill — records PnL, writes DB, emits event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_sell_fill_success():
    """handle_sell_fill records PnL, updates DB, emits trade_filled event."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pf.record_trade_close = AsyncMock(return_value=1.25)

        mock_session = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.update_status = AsyncMock()
        mock_repo.close_trade_for_position = AsyncMock()
        mocks["async_session"].return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mocks["async_session"].return_value.__aexit__ = AsyncMock(return_value=False)
        mocks["event_bus"].emit = AsyncMock()

        with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await closer.handle_sell_fill(
                market_id="mkt1",
                sell_price=0.70,
                trade_id=99,
                shares=10.0,
                strategy="time_decay",
                question="Will X?",
            )

        pf.record_trade_close.assert_awaited_once_with("mkt1", 0.70)
        rm.update_daily_pnl.assert_called_once_with(1.25)

        mocks["event_bus"].emit.assert_awaited_once()
        call_kwargs = mocks["event_bus"].emit.call_args
        assert call_kwargs[1]["pnl"] == 1.25
        assert call_kwargs[1]["size"] == 10.0
        assert call_kwargs[1]["side"] == "SELL"
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 5. handle_sell_fill — uses strategy/question params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_sell_fill_uses_strategy_and_question():
    """strategy and question params are forwarded to log_position_closed and event_bus."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pf.record_trade_close = AsyncMock(return_value=0.0)

        mock_session = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.update_status = AsyncMock()
        mock_repo.close_trade_for_position = AsyncMock()
        mocks["async_session"].return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mocks["async_session"].return_value.__aexit__ = AsyncMock(return_value=False)
        mocks["event_bus"].emit = AsyncMock()

        with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await closer.handle_sell_fill(
                market_id="mkt2",
                sell_price=0.55,
                trade_id=100,
                shares=5.0,
                strategy="arbitrage",
                question="Question B?",
            )

        log_call = mocks["log_closed"].call_args
        assert log_call[1]["strategy"] == "arbitrage"
        assert log_call[1]["question"] == "Question B?"

        event_call = mocks["event_bus"].emit.call_args
        assert event_call[1]["strategy"] == "arbitrage"
        assert event_call[1]["question"] == "Question B?"
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 6. handle_sell_fill — DB error caught and logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_sell_fill_db_error_caught():
    """DB errors in handle_sell_fill are caught — event still emitted."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        pf.record_trade_close = AsyncMock(return_value=0.5)

        # Make async_session raise inside the block
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mocks["async_session"].return_value = mock_ctx
        mocks["event_bus"].emit = AsyncMock()

        await closer.handle_sell_fill(
            market_id="mkt1",
            sell_price=0.60,
            trade_id=50,
            shares=8.0,
        )

        # PnL still recorded in portfolio even though DB fails
        pf.record_trade_close.assert_awaited_once()
        # Event still emitted after the try/except
        mocks["event_bus"].emit.assert_awaited_once()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 7. handle_order_fill — creates position and emits event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_order_fill_creates_position():
    """handle_order_fill records a trade open and emits buy_filled event."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        closer, om, pf, rm = _make_closer()
        signal = _make_signal()
        mocks["event_bus"].emit = AsyncMock()

        await closer.handle_order_fill(signal, shares=15.0, actual_price=signal.market_price)

        pf.record_trade_open.assert_awaited_once_with(
            market_id=signal.market_id,
            token_id=signal.token_id,
            question=signal.question,
            outcome=signal.outcome,
            category="crypto",
            strategy=signal.strategy,
            side="BUY",
            size=15.0,
            price=signal.market_price,
        )

        call_kwargs = mocks["event_bus"].emit.call_args
        assert call_kwargs[0][0] == "trade_filled"
        assert call_kwargs[1]["trade_event"] == "buy_filled"
        assert call_kwargs[1]["size"] == 15.0
        assert call_kwargs[1]["side"] == "BUY"
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 8. try_rebalance — valid candidates → returns (worst_pos, trade) tuple
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_valid_candidate():
    """With a valid losing position, try_rebalance closes it and returns the tuple."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        pos = _make_position(
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        trade = _make_trade(status="filled")
        om.close_position = AsyncMock(return_value=trade)

        result = await closer.try_rebalance(signal, [pos])

        assert result is not None
        closed_pos, close_trade = result
        assert closed_pos is pos
        assert close_trade is trade
        om.close_position.assert_awaited_once()
        mocks["log_rebalance"].assert_awaited_once()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 9. try_rebalance — no candidates → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_no_candidates():
    """If all positions are winning, try_rebalance returns None."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        # All positions are profitable
        pos = _make_position(
            unrealized_pnl=1.0,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        result = await closer.try_rebalance(signal, [pos])
        assert result is None
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 10. try_rebalance — edge below threshold → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_edge_below_threshold():
    """If signal.edge < 0.03, try_rebalance returns None immediately."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.01)  # below 1.5% threshold
        pos = _make_position(unrealized_pnl=-0.50)

        result = await closer.try_rebalance(signal, [pos])
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 11. try_rebalance — close_position fails → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_close_fails():
    """If order_manager.close_position returns None, try_rebalance returns None."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        pos = _make_position(
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        om.close_position = AsyncMock(return_value=None)

        result = await closer.try_rebalance(signal, [pos])
        assert result is None
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 12. try_rebalance — skips winning positions (unrealized_pnl > 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_skips_winning_positions():
    """Positions with positive unrealized PnL are excluded from candidates."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        winning = _make_position(
            market_id="winner",
            unrealized_pnl=2.0,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        result = await closer.try_rebalance(signal, [winning])
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 13. try_rebalance — skips recently held positions (< 300s)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_skips_recently_created():
    """Positions held for less than 300 seconds are skipped."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        recent = _make_position(
            unrealized_pnl=-0.50,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),  # only 60s old
        )

        result = await closer.try_rebalance(signal, [recent])
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 14. try_rebalance — picks worst loser from multiple candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_picks_worst_loser():
    """Given multiple losing positions, the one with worst PnL % is closed."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        old_time = datetime(2025, 12, 1, tzinfo=timezone.utc)

        # mild loser: avg=0.50, current=0.48 → pnl_pct = -0.04
        mild = _make_position(
            market_id="mild",
            avg_price=0.50,
            current_price=0.48,
            unrealized_pnl=-0.20,
            created_at=old_time,
        )
        # heavy loser: avg=0.60, current=0.45 → pnl_pct = -0.25
        heavy = _make_position(
            market_id="heavy",
            avg_price=0.60,
            current_price=0.45,
            unrealized_pnl=-1.50,
            created_at=old_time,
        )
        trade = _make_trade(status="filled")
        om.close_position = AsyncMock(return_value=trade)

        result = await closer.try_rebalance(signal, [mild, heavy])

        assert result is not None
        closed_pos, _ = result
        assert closed_pos.market_id == "heavy"
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 15. try_rebalance — live mode skips positions < 5 shares
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_live_skips_small_positions():
    """In live mode, positions below $1.00 notional are excluded from rebalance."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = False  # live mode
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        small = _make_position(
            size=1.2,  # 1.2 × $0.50 = $0.60 < $1.00 notional
            current_price=0.50,
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        result = await closer.try_rebalance(signal, [small])
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


@pytest.mark.asyncio
async def test_try_rebalance_live_allows_above_notional():
    """In live mode, positions above $1.00 notional with < 5 shares are allowed."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = False  # live mode
        closer, om, pf, rm = _make_closer()
        signal = _make_signal(edge=0.05)
        small = _make_position(
            size=4.5,  # 4.5 × $0.50 = $2.25 > $1.00 notional
            current_price=0.50,
            avg_price=0.55,  # losing position
            unrealized_pnl=-0.225,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        close_trade = MagicMock()
        close_trade.status = "pending"
        om.close_position = AsyncMock(return_value=close_trade)

        result = await closer.try_rebalance(signal, [small])
        assert result is not None
        om.close_position.assert_awaited_once()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 17. try_rebalance — urgency lowers effective min edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_rebalance_urgency_lowers_threshold():
    """With urgency=1.5, effective min_rebalance_edge is lowered."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        # min_rebalance_edge = 0.015 (default)
        # Signal edge = 0.01 → normally rejected (0.01 < 0.015)
        # With urgency=1.5 → effective_min = 0.015 / 1.5 = 0.01 → accepted
        signal = _make_signal(edge=0.01)
        pos = _make_position(
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        trade = _make_trade(status="filled")
        om.close_position = AsyncMock(return_value=trade)

        result = await closer.try_rebalance(signal, [pos], urgency=1.5)
        assert result is not None
    finally:
        _stop_patches(patches)


@pytest.mark.asyncio
async def test_try_rebalance_no_urgency_uses_original():
    """With urgency=1.0, original min_rebalance_edge applies."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        # Signal edge = 0.01 → rejected (0.01 < 0.015)
        signal = _make_signal(edge=0.01)
        pos = _make_position(
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        result = await closer.try_rebalance(signal, [pos], urgency=1.0)
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


# ---------------------------------------------------------------------------
# 18. Ghost position detection — sell failure tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sell_failure_increments_count():
    """Each failed sell (trade=None) increments _sell_fail_count."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        pos = _make_position(market_id="ghost_mkt")
        om.close_position = AsyncMock(return_value=None)

        await closer.close_position(pos)
        assert closer._sell_fail_count["ghost_mkt"] == 1

        await closer.close_position(pos)
        assert closer._sell_fail_count["ghost_mkt"] == 2
    finally:
        _stop_patches(patches)


@pytest.mark.asyncio
async def test_successful_sell_resets_fail_count():
    """A successful sell resets the failure counter."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        pos = _make_position(market_id="reset_mkt")
        # First, fail twice
        om.close_position = AsyncMock(return_value=None)
        await closer.close_position(pos)
        await closer.close_position(pos)
        assert closer._sell_fail_count["reset_mkt"] == 2

        # Now succeed — need full mock setup for filled path
        trade = _make_trade(status="filled")
        om.close_position = AsyncMock(return_value=trade)
        pf.record_trade_close = AsyncMock(return_value=0.10)
        mocks["event_bus"].emit = AsyncMock()
        mock_session = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.update_status = AsyncMock()
        mock_repo.close_trade_for_position = AsyncMock()
        mocks["async_session"].return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mocks["async_session"].return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await closer.close_position(pos)
        assert "reset_mkt" not in closer._sell_fail_count
    finally:
        _stop_patches(patches)


@pytest.mark.asyncio
async def test_rebalance_skips_stuck_positions():
    """Positions with 3+ sell failures are skipped by try_rebalance."""
    patches = _common_patches()
    mocks = _enter_patches(patches)
    try:
        mocks["settings"].is_paper = True
        closer, om, pf, rm = _make_closer()
        # Mark a position as stuck
        closer._sell_fail_count["stuck_mkt"] = 3
        signal = _make_signal(edge=0.05)
        pos = _make_position(
            market_id="stuck_mkt",
            unrealized_pnl=-0.50,
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        result = await closer.try_rebalance(signal, [pos])
        assert result is None
        om.close_position.assert_not_awaited()
    finally:
        _stop_patches(patches)


def test_stuck_positions_property():
    """stuck_positions returns market_ids with 3+ failures."""
    closer, _, _, _ = _make_closer()
    closer._sell_fail_count = {"mkt_a": 3, "mkt_b": 1, "mkt_c": 5}
    stuck = closer.stuck_positions
    assert "mkt_a" in stuck
    assert "mkt_c" in stuck
    assert "mkt_b" not in stuck
    assert len(stuck) == 2

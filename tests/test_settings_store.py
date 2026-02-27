"""Tests for settings persistence across restarts."""

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import CapitalTier, TierConfig, settings
from bot.data.models import Base
from bot.data.repositories import SettingsRepository


@pytest.fixture
async def settings_engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def settings_session_factory(settings_engine):
    return async_sessionmaker(
        settings_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
def fake_engine():
    """Fake TradingEngine with real-enough attributes for load_and_apply."""
    engine = MagicMock()

    strategy = MagicMock()
    strategy.name = "time_decay"
    strategy.MAX_HOURS_TO_RESOLUTION = 720.0
    strategy.MIN_EDGE = 0.015

    strategy2 = MagicMock()
    strategy2.name = "value_betting"
    strategy2.MAX_HOURS_TO_RESOLUTION = 168.0

    engine.analyzer = MagicMock()
    engine.analyzer.strategies = [strategy, strategy2]
    engine.analyzer.MAX_SPREAD = 0.04
    engine.analyzer.MAX_CATEGORY_POSITIONS = 2
    engine.analyzer.MIN_BID_RATIO = 0.50
    engine.analyzer.MIN_VOLUME_24H = 50.0
    engine.analyzer.STOP_LOSS_PCT = 0.40
    engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
    engine.analyzer.DEFAULT_EXIT_PRICE = 0.70

    return engine


# ── Repository tests ──


@pytest.mark.asyncio
async def test_set_many_and_get_all(settings_session_factory):
    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({"global.scan_interval_seconds": "30", "global.max_daily_loss_pct": "0.08"})
        result = await repo.get_all()

    assert result["global.scan_interval_seconds"] == "30"
    assert result["global.max_daily_loss_pct"] == "0.08"


@pytest.mark.asyncio
async def test_upsert_overwrites(settings_session_factory):
    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({"global.scan_interval_seconds": "30"})
        await repo.set_many({"global.scan_interval_seconds": "60"})
        result = await repo.get_all()

    assert result["global.scan_interval_seconds"] == "60"


@pytest.mark.asyncio
async def test_empty_db_returns_empty(settings_session_factory):
    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        result = await repo.get_all()

    assert result == {}


# ── SettingsStore.save_from_update tests ──


@pytest.mark.asyncio
async def test_save_global_settings(settings_session_factory):
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate(scan_interval_seconds=30, max_daily_loss_pct=0.08)

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        # Patch async_session for the store
        import bot.data.settings_store as store_mod

        original = store_mod.async_session
        store_mod.async_session = settings_session_factory
        try:
            count = await SettingsStore.save_from_update(update, CapitalTier.TIER1)
        finally:
            store_mod.async_session = original

    assert count == 2

    # Verify persisted
    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        all_s = await repo.get_all()

    assert json.loads(all_s["global.scan_interval_seconds"]) == 30
    assert json.loads(all_s["global.max_daily_loss_pct"]) == 0.08


@pytest.mark.asyncio
async def test_save_tier_config(settings_session_factory):
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate(tier_config={"max_positions": 8, "kelly_fraction": 0.25})

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.save_from_update(update, CapitalTier.TIER2)
    finally:
        store_mod.async_session = original

    assert count == 2

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        all_s = await repo.get_all()

    assert json.loads(all_s["tier.tier2.max_positions"]) == 8
    assert json.loads(all_s["tier.tier2.kelly_fraction"]) == 0.25


@pytest.mark.asyncio
async def test_save_strategy_params(settings_session_factory):
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate(
        strategy_params={"time_decay": {"MAX_HOURS_TO_RESOLUTION": 72}}
    )

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.save_from_update(update, CapitalTier.TIER1)
    finally:
        store_mod.async_session = original

    assert count == 1

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        all_s = await repo.get_all()

    assert json.loads(all_s["strategy.time_decay.MAX_HOURS_TO_RESOLUTION"]) == 72


@pytest.mark.asyncio
async def test_save_quality_params(settings_session_factory):
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate(quality_params={"max_spread": 0.03, "stop_loss_pct": 0.35})

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.save_from_update(update, CapitalTier.TIER1)
    finally:
        store_mod.async_session = original

    assert count == 2

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        all_s = await repo.get_all()

    assert json.loads(all_s["quality.max_spread"]) == 0.03


@pytest.mark.asyncio
async def test_save_empty_update_returns_zero(settings_session_factory):
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate()

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.save_from_update(update, CapitalTier.TIER1)
    finally:
        store_mod.async_session = original

    assert count == 0


# ── SettingsStore.load_and_apply tests ──


@pytest.mark.asyncio
async def test_load_and_apply_global(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    # Seed settings
    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({"global.scan_interval_seconds": json.dumps(45)})

    original_val = settings.scan_interval_seconds

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
    finally:
        store_mod.async_session = original
        # Restore original
        settings.scan_interval_seconds = original_val

    assert count == 1


@pytest.mark.asyncio
async def test_load_and_apply_tier(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({"tier.tier2.max_positions": json.dumps(10)})

    original_val = TierConfig.get(CapitalTier.TIER2)["max_positions"]

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
        assert TierConfig.get(CapitalTier.TIER2)["max_positions"] == 10
    finally:
        store_mod.async_session = original
        TierConfig.update(CapitalTier.TIER2, {"max_positions": original_val})

    assert count == 1


@pytest.mark.asyncio
async def test_load_and_apply_strategy(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many(
            {"strategy.time_decay.MAX_HOURS_TO_RESOLUTION": json.dumps(72)}
        )

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
    finally:
        store_mod.async_session = original

    assert count == 1
    assert fake_engine.analyzer.strategies[0].MAX_HOURS_TO_RESOLUTION == 72


@pytest.mark.asyncio
async def test_load_and_apply_quality(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({"quality.max_spread": json.dumps(0.03)})

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
    finally:
        store_mod.async_session = original

    assert count == 1
    assert fake_engine.analyzer.MAX_SPREAD == 0.03


@pytest.mark.asyncio
async def test_empty_db_returns_zero(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
    finally:
        store_mod.async_session = original

    assert count == 0


@pytest.mark.asyncio
async def test_invalid_keys_ignored(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({
            "tier.bogus_tier.max_positions": json.dumps(5),
            "strategy.nonexistent.FOO": json.dumps(99),
            "quality.nonexistent_param": json.dumps(1),
            "unknown_prefix.something": json.dumps(1),
        })

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
    finally:
        store_mod.async_session = original

    assert count == 0


@pytest.mark.asyncio
async def test_multiple_tiers(settings_session_factory, fake_engine):
    from bot.data.settings_store import SettingsStore

    async with settings_session_factory() as session:
        repo = SettingsRepository(session)
        await repo.set_many({
            "tier.tier1.max_positions": json.dumps(4),
            "tier.tier2.max_positions": json.dumps(8),
        })

    orig_t1 = TierConfig.get(CapitalTier.TIER1)["max_positions"]
    orig_t2 = TierConfig.get(CapitalTier.TIER2)["max_positions"]

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory
    try:
        count = await SettingsStore.load_and_apply(fake_engine)
        assert TierConfig.get(CapitalTier.TIER1)["max_positions"] == 4
        assert TierConfig.get(CapitalTier.TIER2)["max_positions"] == 8
    finally:
        store_mod.async_session = original
        TierConfig.update(CapitalTier.TIER1, {"max_positions": orig_t1})
        TierConfig.update(CapitalTier.TIER2, {"max_positions": orig_t2})

    assert count == 2


@pytest.mark.asyncio
async def test_round_trip_save_then_load(settings_session_factory, fake_engine):
    """Full round-trip: save via update then load on a fresh engine."""
    from api.schemas import BotConfigUpdate
    from bot.data.settings_store import SettingsStore

    update = BotConfigUpdate(
        scan_interval_seconds=25,
        strategy_params={"time_decay": {"MAX_HOURS_TO_RESOLUTION": 72}},
        quality_params={"max_spread": 0.02},
        tier_config={"kelly_fraction": 0.30},
    )

    import bot.data.settings_store as store_mod

    original = store_mod.async_session
    store_mod.async_session = settings_session_factory

    orig_scan = settings.scan_interval_seconds
    try:
        saved = await SettingsStore.save_from_update(update, CapitalTier.TIER1)
        assert saved == 4

        loaded = await SettingsStore.load_and_apply(fake_engine)
        assert loaded == 4
        assert settings.scan_interval_seconds == 25
    finally:
        store_mod.async_session = original
        settings.scan_interval_seconds = orig_scan

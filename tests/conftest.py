"""Shared test fixtures for bot logic and API endpoint tests."""

import os

# Set API_SECRET_KEY before any bot.config import so the validator passes
os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.data.models import Base

TEST_API_KEY = os.environ["API_SECRET_KEY"]


@pytest.fixture
async def async_engine():
    """In-memory async SQLite engine with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine):
    """Async DB session bound to a per-test in-memory engine.

    Each test gets a fresh database via function-scoped engine; no explicit
    rollback is needed since the entire in-memory DB is discarded after yield.
    """
    session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def mock_engine():
    """MagicMock trading engine with portfolio, risk_manager, and cache."""
    engine = MagicMock()

    # Portfolio
    engine.portfolio.get_overview.return_value = {
        "total_equity": 10.0,
        "cash_balance": 8.0,
        "polymarket_balance": None,
        "positions_value": 2.0,
        "unrealized_pnl": 0.5,
        "realized_pnl_today": 0.1,
        "open_positions": 1,
        "peak_equity": 10.0,
        "tier": "tier1",
        "is_paper": True,
        "wallet_address": None,
    }
    engine.portfolio.total_equity = 10.0
    engine.portfolio.tier = MagicMock()
    engine.portfolio.tier.value = "tier1"
    # Make tier work with TierConfig.get() — use the actual CapitalTier
    from bot.config import CapitalTier

    engine.portfolio.tier = CapitalTier.TIER1

    # Risk manager
    engine.risk_manager = MagicMock()
    engine.risk_manager.get_risk_metrics.return_value = {
        "tier": "tier1",
        "bankroll": 10.0,
        "peak_equity": 10.0,
        "current_drawdown_pct": 0.0,
        "max_drawdown_limit_pct": 0.25,
        "daily_pnl": 0.0,
        "daily_loss_limit_pct": 0.10,
        "max_positions": 1,
        "is_paused": False,
    }
    engine.risk_manager.pause = MagicMock()
    engine.risk_manager.resume = MagicMock()

    # Cache
    engine.cache = MagicMock()
    engine.cache.get_all_markets.return_value = []

    return engine


@pytest.fixture
async def client(db_session, mock_engine):
    """Async HTTP client for API tests with mocked DB and engine."""
    from fastapi import FastAPI

    from api.dependencies import get_db, get_engine
    from api.routers import config, markets, portfolio, risk, strategies, trades

    # Build a minimal app without the lifespan (no real bot startup)
    test_app = FastAPI()
    test_app.include_router(portfolio.router)
    test_app.include_router(trades.router)
    test_app.include_router(strategies.router)
    test_app.include_router(markets.router)
    test_app.include_router(risk.router)
    test_app.include_router(config.router)

    # Override get_db to yield the test session
    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_engine] = lambda: mock_engine

    # Also patch bot.main.engine for routes that call get_engine() directly
    # (not via Depends). get_engine() does `from bot.main import engine`.
    with patch("bot.main.engine", mock_engine):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": TEST_API_KEY},
        ) as ac:
            yield ac

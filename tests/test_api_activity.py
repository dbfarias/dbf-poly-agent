"""Tests for activity API endpoints — event log with pagination and filters."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.dependencies import get_engine
from api.middleware import verify_api_key
from bot.data.models import Base, BotActivity

TEST_API_KEY = os.environ["API_SECRET_KEY"]


@pytest.fixture
async def activity_engine():
    """In-memory async SQLite engine with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def activity_session_factory(activity_engine):
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        activity_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
async def activity_client(activity_session_factory):
    """Async HTTP client wired to the activity router with in-memory DB."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from api.routers import activity

    test_app = FastAPI()
    test_app.include_router(activity.router)

    async def override_verify(_=None):
        return "test-user"

    mock_engine = MagicMock()
    test_app.dependency_overrides[verify_api_key] = override_verify
    test_app.dependency_overrides[get_engine] = lambda: mock_engine

    # Patch async_session in the activity router module to use our in-memory DB
    original_async_session = activity.async_session
    activity.async_session = activity_session_factory

    try:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": TEST_API_KEY},
        ) as ac:
            yield ac
    finally:
        activity.async_session = original_async_session


@pytest.fixture
async def seed_activities(activity_session_factory):
    """Insert sample activity events into the in-memory database."""
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        BotActivity(
            timestamp=now,
            event_type="signal_found",
            level="info",
            title="Signal detected",
            detail="Edge 3.5% on crypto market",
            metadata_json='{"edge": 0.035}',
            market_id="mkt_1",
            strategy="time_decay",
        ),
        BotActivity(
            timestamp=now,
            event_type="trade_placed",
            level="success",
            title="Trade executed",
            detail="Bought 10 shares at $0.85",
            metadata_json='{"shares": 10}',
            market_id="mkt_1",
            strategy="time_decay",
        ),
        BotActivity(
            timestamp=now,
            event_type="risk_limit",
            level="warning",
            title="Approaching daily loss limit",
            detail="Daily PnL at -8%",
            metadata_json="{}",
            market_id="",
            strategy="",
        ),
        BotActivity(
            timestamp=now,
            event_type="signal_found",
            level="info",
            title="Signal detected",
            detail="Edge 2.1% on sports market",
            metadata_json='{"edge": 0.021}',
            market_id="mkt_2",
            strategy="arbitrage",
        ),
        BotActivity(
            timestamp=now,
            event_type="error_occurred",
            level="error",
            title="API timeout",
            detail="Polymarket API timed out after 30s",
            metadata_json="{}",
            market_id="",
            strategy="",
        ),
    ]

    async with activity_session_factory() as session:
        session.add_all(rows)
        await session.commit()


# ---------------------------------------------------------------------------
# GET /api/activity/
# ---------------------------------------------------------------------------


class TestGetActivity:
    async def test_returns_events_with_pagination(
        self, activity_client, seed_activities
    ):
        """Returns events with total count and has_more flag."""
        resp = await activity_client.get("/api/activity/?limit=3&offset=0")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 5
        assert len(data["events"]) == 3
        assert data["has_more"] is True

        # Each event has required fields
        event = data["events"][0]
        assert "id" in event
        assert "timestamp" in event
        assert "event_type" in event
        assert "level" in event
        assert "title" in event
        assert "detail" in event

    async def test_returns_empty_when_no_events(self, activity_client):
        """Returns empty events list when no data exists."""
        resp = await activity_client.get("/api/activity/")
        assert resp.status_code == 200
        data = resp.json()

        assert data["events"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    async def test_filter_by_event_type(
        self, activity_client, seed_activities
    ):
        """Filters events by event_type query parameter."""
        resp = await activity_client.get(
            "/api/activity/?event_type=signal_found"
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 2
        assert len(data["events"]) == 2
        for event in data["events"]:
            assert event["event_type"] == "signal_found"

    async def test_filter_by_level(
        self, activity_client, seed_activities
    ):
        """Filters events by level query parameter."""
        resp = await activity_client.get("/api/activity/?level=warning")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 1
        assert len(data["events"]) == 1
        assert data["events"][0]["level"] == "warning"
        assert data["events"][0]["event_type"] == "risk_limit"

    async def test_filter_by_strategy(
        self, activity_client, seed_activities
    ):
        """Filters events by strategy query parameter."""
        resp = await activity_client.get("/api/activity/?strategy=time_decay")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 2
        assert len(data["events"]) == 2
        for event in data["events"]:
            assert event["strategy"] == "time_decay"


# ---------------------------------------------------------------------------
# GET /api/activity/event-types
# ---------------------------------------------------------------------------


class TestGetEventTypes:
    async def test_returns_distinct_event_types_sorted(
        self, activity_client, seed_activities
    ):
        """Returns distinct event types in alphabetical order."""
        resp = await activity_client.get("/api/activity/event-types")
        assert resp.status_code == 200
        data = resp.json()

        assert isinstance(data, list)
        assert len(data) == 4
        assert data == sorted(data)
        assert "signal_found" in data
        assert "trade_placed" in data
        assert "risk_limit" in data
        assert "error_occurred" in data

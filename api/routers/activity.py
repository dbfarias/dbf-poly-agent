"""Activity log API — human-readable bot decision log."""

import json
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.middleware import verify_api_key
from api.schemas import ActivityEvent, ActivityResponse, LlmDailyCost
from bot.config import settings as bot_settings
from bot.data.database import async_session
from bot.data.models import BotActivity
from bot.data.repositories import PortfolioSnapshotRepository

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/", response_model=ActivityResponse)
async def get_activity(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None, max_length=100),
    level: str | None = Query(default=None, max_length=50),
    strategy: str | None = Query(default=None, max_length=100),
    _: str = Depends(verify_api_key),
):
    """Get paginated activity log with optional filters."""
    async with async_session() as session:
        query = select(BotActivity).order_by(BotActivity.timestamp.desc())
        count_query = select(func.count(BotActivity.id))

        if event_type:
            query = query.where(BotActivity.event_type == event_type)
            count_query = count_query.where(BotActivity.event_type == event_type)
        if level:
            query = query.where(BotActivity.level == level)
            count_query = count_query.where(BotActivity.level == level)
        if strategy:
            query = query.where(BotActivity.strategy == strategy)
            count_query = count_query.where(BotActivity.strategy == strategy)

        total = await session.scalar(count_query) or 0
        result = await session.execute(query.offset(offset).limit(limit))
        rows = result.scalars().all()

    events = []
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json) if row.metadata_json else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        events.append(ActivityEvent(
            id=row.id,
            timestamp=row.timestamp,
            event_type=row.event_type,
            level=row.level,
            title=row.title,
            detail=row.detail,
            metadata=metadata,
            market_id=row.market_id,
            strategy=row.strategy,
        ))

    return ActivityResponse(
        events=events,
        total=total,
        has_more=(offset + limit) < total,
    )


@router.get("/event-types")
async def get_event_types(_: str = Depends(verify_api_key)):
    """Get list of distinct event types in the activity log."""
    async with async_session() as session:
        result = await session.execute(
            select(BotActivity.event_type)
            .distinct()
            .order_by(BotActivity.event_type)
        )
        return [row[0] for row in result.all()]


@router.get("/llm-costs", response_model=list[LlmDailyCost])
async def get_llm_costs(
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Daily LLM cost breakdown vs trading PnL."""
    # Fetch all LLM activity rows (use injected db session for both queries)
    llm_types = ("llm_debate", "llm_review", "llm_risk_debate")
    result = await db.execute(
        select(BotActivity)
        .where(BotActivity.event_type.in_(llm_types))
        .order_by(BotActivity.timestamp.asc())
    )
    rows = result.scalars().all()

    offset = timedelta(hours=bot_settings.timezone_offset_hours)

    # Group costs by date and event_type
    cost_by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {"llm_debate": 0.0, "llm_review": 0.0, "llm_risk_debate": 0.0},
    )
    for row in rows:
        local_ts = row.timestamp + offset
        day = local_ts.strftime("%Y-%m-%d")
        try:
            meta = json.loads(row.metadata_json) if row.metadata_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        cost = float(meta.get("cost_usd", 0.0))
        cost_by_day[day][row.event_type] += cost

    # Build daily PnL lookup from portfolio snapshots
    snap_repo = PortfolioSnapshotRepository(db)
    snapshots = await snap_repo.get_equity_curve(days=365)
    pnl_by_day: dict[str, float] = {}
    day_data: dict[str, dict] = defaultdict(
        lambda: {"first": None, "last": None},
    )
    for s in snapshots:
        local_ts = s.timestamp + offset
        day = local_ts.strftime("%Y-%m-%d")
        entry = day_data[day]
        if entry["first"] is None:
            entry["first"] = s
        entry["last"] = s
    for day, data in day_data.items():
        pnl_by_day[day] = data["last"].total_equity - data["first"].total_equity

    # Merge: all days that have either costs or PnL
    all_days = sorted(set(cost_by_day.keys()) | set(pnl_by_day.keys()))
    result_list = []
    for day in all_days:
        costs = cost_by_day.get(day, {"llm_debate": 0.0, "llm_review": 0.0, "llm_risk_debate": 0.0})
        debate_cost = costs["llm_debate"]
        review_cost = costs["llm_review"]
        risk_debate_cost = costs["llm_risk_debate"]
        total_cost = debate_cost + review_cost + risk_debate_cost

        # Skip days with no LLM activity
        if total_cost == 0.0:
            continue

        daily_pnl = pnl_by_day.get(day, 0.0)
        result_list.append(LlmDailyCost(
            date=day,
            debate_cost=round(debate_cost, 5),
            review_cost=round(review_cost, 5),
            risk_debate_cost=round(risk_debate_cost, 5),
            total_cost=round(total_cost, 5),
            daily_pnl=round(daily_pnl, 4),
            net_profit=round(daily_pnl - total_cost, 4),
        ))

    return result_list

"""Activity log API — human-readable bot decision log."""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from api.middleware import verify_api_key
from api.schemas import ActivityEvent, ActivityResponse
from bot.data.database import async_session
from bot.data.models import BotActivity

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/", response_model=ActivityResponse)
async def get_activity(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    level: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
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

"""Markets API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_engine
from api.middleware import verify_api_key
from api.schemas import MarketOpportunity
from bot.data.repositories import MarketScanRepository

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/scanner", response_model=list[MarketOpportunity])
async def get_scanner(
    limit: int = 20,
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    repo = MarketScanRepository(db)
    scans = await repo.get_recent_opportunities(limit=limit)
    return [
        MarketOpportunity(
            market_id=s.market_id,
            question=s.question,
            category=s.category,
            yes_price=s.yes_price,
            no_price=s.no_price,
            volume=s.volume,
            liquidity=s.liquidity,
            end_date=s.end_date,
            hours_to_resolution=s.hours_to_resolution,
            signal_strategy=s.signal_strategy,
            signal_edge=s.signal_edge,
            signal_confidence=s.signal_confidence,
        )
        for s in scans
    ]


@router.get("/opportunities")
async def get_opportunities(_: str = Depends(verify_api_key)):
    engine = get_engine()
    cache_markets = engine.cache.get_all_markets()
    return {
        "total_cached": len(cache_markets),
        "markets": [
            {
                "id": m.id,
                "question": m.question,
                "yes_price": m.yes_price,
                "no_price": m.no_price,
                "volume": m.volume,
                "end_date": m.end_date_iso,
            }
            for m in cache_markets[:50]
        ],
    }

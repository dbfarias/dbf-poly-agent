"""Research engine endpoints — news sentiment and market research data."""

from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/status")
async def get_research_status(_: str = Depends(verify_api_key)):
    """Return research engine status: cache stats, last scan time."""
    engine = get_engine()
    research_cache = engine.research_cache
    research_engine = engine.research_engine

    return {
        "running": research_engine._running,
        "scan_interval_seconds": research_engine.SCAN_INTERVAL,
        "max_markets": research_engine.MAX_MARKETS,
        **research_cache.stats,
    }


@router.get("/markets")
async def get_research_markets(_: str = Depends(verify_api_key)):
    """Return all cached research results with sentiment data."""
    engine = get_engine()
    results = engine.research_cache.get_all()

    return [
        {
            "market_id": r.market_id,
            "keywords": list(r.keywords),
            "sentiment_score": round(r.sentiment_score, 4),
            "confidence": round(r.confidence, 2),
            "research_multiplier": round(r.research_multiplier, 3),
            "crypto_sentiment": round(r.crypto_sentiment, 4),
            "updated_at": r.updated_at.isoformat(),
            "article_count": len(r.news_items),
            "top_headlines": [
                {
                    "title": item.title,
                    "source": item.source,
                    "sentiment": round(item.sentiment, 3),
                    "published": item.published.isoformat(),
                }
                for item in r.news_items[:5]
            ],
        }
        for r in sorted(results, key=lambda x: abs(x.sentiment_score), reverse=True)
    ]


@router.get("/markets/{market_id}")
async def get_market_research(market_id: str, _: str = Depends(verify_api_key)):
    """Return detailed research for a specific market."""
    engine = get_engine()
    result = engine.research_cache.get(market_id)

    if result is None:
        return {"error": "No research data for this market", "market_id": market_id}

    return {
        "market_id": result.market_id,
        "keywords": list(result.keywords),
        "sentiment_score": round(result.sentiment_score, 4),
        "confidence": round(result.confidence, 2),
        "research_multiplier": round(result.research_multiplier, 3),
        "crypto_sentiment": round(result.crypto_sentiment, 4),
        "updated_at": result.updated_at.isoformat(),
        "article_count": len(result.news_items),
        "headlines": [
            {
                "title": item.title,
                "source": item.source,
                "sentiment": round(item.sentiment, 3),
                "url": item.url,
                "published": item.published.isoformat(),
            }
            for item in result.news_items
        ],
    }

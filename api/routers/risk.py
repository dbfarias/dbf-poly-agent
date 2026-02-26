"""Risk management API endpoints."""

from fastapi import APIRouter

from api.dependencies import get_engine
from api.schemas import RiskLimits, RiskMetrics
from bot.config import TierConfig

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/metrics", response_model=RiskMetrics)
async def get_risk_metrics():
    engine = get_engine()
    metrics = engine.risk_manager.get_risk_metrics(engine.portfolio.total_equity)
    return RiskMetrics(**metrics)


@router.get("/limits", response_model=RiskLimits)
async def get_risk_limits():
    engine = get_engine()
    tier = engine.portfolio.tier
    config = TierConfig.get(tier)
    return RiskLimits(tier=tier.value, **config)

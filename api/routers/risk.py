"""Risk management API endpoints."""

from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.schemas import RiskLimits, RiskMetrics
from bot.config import RiskConfig

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/metrics", response_model=RiskMetrics)
async def get_risk_metrics(_: str = Depends(verify_api_key)):
    engine = get_engine()
    metrics = engine.risk_manager.get_risk_metrics(engine.portfolio.total_equity)
    return RiskMetrics(**metrics)


@router.get("/limits", response_model=RiskLimits)
async def get_risk_limits(_: str = Depends(verify_api_key)):
    config = RiskConfig.get()
    return RiskLimits(**config)

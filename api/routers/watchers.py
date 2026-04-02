"""Trade Watcher API endpoints."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.rate_limit import limiter
from api.schemas import (
    CreateWatcherRequest,
    WatcherDecisionResponse,
    WatcherDetailResponse,
    WatcherResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/watchers", tags=["watchers"])


def _watcher_to_response(w) -> WatcherResponse:
    return WatcherResponse(
        id=w.id,
        created_at=w.created_at,
        updated_at=w.updated_at,
        market_id=w.market_id,
        token_id=w.token_id,
        question=w.question,
        outcome=w.outcome,
        keywords=w.keywords,
        thesis=w.thesis,
        max_exposure_usd=w.max_exposure_usd,
        stop_loss_pct=w.stop_loss_pct,
        max_age_hours=w.max_age_hours,
        check_interval_sec=w.check_interval_sec,
        status=w.status,
        current_exposure=w.current_exposure,
        avg_entry_price=w.avg_entry_price,
        scale_count=w.scale_count,
        max_scale_count=w.max_scale_count,
        highest_price=w.highest_price,
        last_check_at=w.last_check_at,
        last_news_at=w.last_news_at,
        end_date=w.end_date,
        source_strategy=w.source_strategy,
        auto_created=w.auto_created,
    )


def _decision_to_response(d) -> WatcherDecisionResponse:
    return WatcherDecisionResponse(
        id=d.id,
        created_at=d.created_at,
        watcher_id=d.watcher_id,
        decision=d.decision,
        signals_json=d.signals_json,
        reasoning=d.reasoning,
        action_taken=d.action_taken,
        size_usd=d.size_usd,
        price_at_decision=d.price_at_decision,
    )


@router.post("", status_code=201)
@limiter.limit("10/minute")
async def create_watcher(
    request: Request,
    body: CreateWatcherRequest,
    engine=Depends(get_engine),
    _auth: str = Depends(verify_api_key),
) -> WatcherResponse:
    """Create a new Trade Watcher."""
    watcher = await engine.watcher_manager.create_watcher(
        market_id=body.market_id,
        token_id=body.token_id,
        question=body.question,
        outcome=body.outcome,
        keywords=body.keywords,
        thesis=body.thesis,
        current_price=body.current_price,
        current_exposure=body.current_exposure,
        max_exposure_usd=body.max_exposure_usd,
        stop_loss_pct=body.stop_loss_pct,
        max_age_hours=body.max_age_hours,
    )
    if watcher is None:
        raise HTTPException(
            status_code=409,
            detail="Watcher rejected: max count reached or duplicate market",
        )
    return _watcher_to_response(watcher)


@router.get("")
@limiter.limit("30/minute")
async def list_watchers(
    request: Request,
    engine=Depends(get_engine),
    _auth: str = Depends(verify_api_key),
) -> list[WatcherResponse]:
    """List all watchers (active + completed + killed)."""
    watchers = await engine.watcher_manager.get_all_watchers()
    return [_watcher_to_response(w) for w in watchers]


@router.get("/{watcher_id}")
@limiter.limit("30/minute")
async def get_watcher_detail(
    request: Request,
    watcher_id: int,
    engine=Depends(get_engine),
    _auth: str = Depends(verify_api_key),
) -> WatcherDetailResponse:
    """Get watcher detail with recent decisions."""
    watcher = await engine.watcher_manager.get_watcher(watcher_id)
    if watcher is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    decisions = await engine.watcher_manager.get_decisions(watcher_id)
    return WatcherDetailResponse(
        watcher=_watcher_to_response(watcher),
        decisions=[_decision_to_response(d) for d in decisions],
    )


@router.post("/{watcher_id}/kill")
@limiter.limit("10/minute")
async def kill_watcher(
    request: Request,
    watcher_id: int,
    engine=Depends(get_engine),
    _auth: str = Depends(verify_api_key),
) -> dict:
    """Kill an active watcher."""
    success = await engine.watcher_manager.kill_watcher(watcher_id, reason="api_manual")
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Watcher not found or not active",
        )
    return {"success": True, "watcher_id": watcher_id}


@router.post("/{watcher_id}/scale")
@limiter.limit("10/minute")
async def scale_watcher(
    request: Request,
    watcher_id: int,
    engine=Depends(get_engine),
    _auth: str = Depends(verify_api_key),
) -> dict:
    """Manual scale up for a watcher (Phase 3 — not yet implemented)."""
    raise HTTPException(status_code=501, detail="Manual scale not yet implemented")

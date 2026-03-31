"""Backtest API endpoint -- run strategy backtests from the dashboard."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.middleware import verify_api_key
from bot.backtesting.data_loader import load_market_history
from bot.backtesting.engine import run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    """Request body for running a backtest."""

    strategy: str = Field(description="Strategy name (e.g. 'sports_favorite')")
    market_slug: str = Field(description="Market URL slug")
    trade_size: float = Field(default=5.0, ge=0.1, le=1000.0)
    initial_balance: float = Field(default=100.0, ge=1.0, le=100000.0)
    fee_rate: float = Field(default=0.02, ge=0.0, le=0.1)
    fee_exponent: float = Field(default=2.0, ge=0.0, le=5.0)
    # Strategy params
    entry_price_min: float = Field(default=0.70, ge=0.01, le=0.99)
    entry_price_max: float = Field(default=0.90, ge=0.01, le=0.99)
    take_profit_pct: float = Field(default=0.15, ge=0.01, le=1.0)
    stop_loss_pct: float = Field(default=0.25, ge=0.01, le=1.0)


def _build_entry_condition(
    price_min: float,
    price_max: float,
) -> callable:
    """Build an entry condition from price range params."""

    def entry(price: float, _index: int, _ticks: list) -> bool:
        return price_min <= price <= price_max

    return entry


def _build_exit_condition(
    take_profit_pct: float,
    stop_loss_pct: float,
) -> callable:
    """Build an exit condition from TP/SL params."""

    def exit_check(entry_price: float, current_price: float, _hold_seconds: float) -> str | None:
        if current_price >= entry_price * (1.0 + take_profit_pct):
            return "take_profit"
        if current_price <= entry_price * (1.0 - stop_loss_pct):
            return "stop_loss"
        return None

    return exit_check


@router.post("/run")
async def run_backtest_endpoint(
    request: BacktestRequest,
    _: str = Depends(verify_api_key),
) -> dict:
    """Run a backtest for a strategy on historical market data.

    Fetches trade history from Polymarket Data API, simulates the
    strategy with the given parameters, and returns performance metrics.
    """
    try:
        history = await load_market_history(slug=request.market_slug)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch market data: {e}") from e

    if not history.ticks:
        raise HTTPException(status_code=404, detail="No trade data available for this market")

    entry_cond = _build_entry_condition(request.entry_price_min, request.entry_price_max)
    exit_cond = _build_exit_condition(request.take_profit_pct, request.stop_loss_pct)

    result = await run_backtest(
        strategy_name=request.strategy,
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=request.trade_size,
        initial_balance=request.initial_balance,
        fee_rate=request.fee_rate,
        fee_exponent=request.fee_exponent,
    )

    return result.to_dict()

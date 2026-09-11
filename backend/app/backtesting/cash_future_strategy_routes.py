"""API surface for historical Cash-Future strategy runs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.backtesting.cash_future_historical_loader import (
    CashFutureHistoricalLoader,
    CashFutureHistorySelection,
)
from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_catalog import HistoricalCatalog
from app.core.config import settings
from app.scanner.cash_future_history import CashFutureHistoryPoint

router = APIRouter(prefix="/api/v1/backtesting/cash-future", tags=["Cash-Future Backtesting"])


class StrategyPointRequest(BaseModel):
    timestamp: datetime
    symbol: str
    contract_month: str
    cash_price: float
    future_price: float
    gap: float
    gap_pct: float = 0.0
    lot_size: int = Field(gt=0)
    margin_required: float = Field(ge=0)
    volume: int = Field(default=0, ge=0)
    oi: int = Field(default=0, ge=0)
    cash_bid: float | None = Field(default=None, gt=0)
    cash_ask: float | None = Field(default=None, gt=0)
    future_bid: float | None = Field(default=None, gt=0)
    future_ask: float | None = Field(default=None, gt=0)
    charges: float = Field(default=0.0, ge=0)
    funding_cost: float = Field(default=0.0, ge=0)
    expiry_date: date | None = None


class StrategyRunRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(default="1", min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    contract_month: str | None = None
    execution_model: str = Field(default="gap", pattern="^(gap|bid_ask)$")
    charges_per_trade: float = Field(default=0.0, ge=0)
    funding_cost_per_trade: float = Field(default=0.0, ge=0)
    initial_capital: float = Field(default=10_000_000.0, gt=0)
    points: list[StrategyPointRequest] | None = None
    spot_instrument: str | None = None
    exchange: str = "NFO"
    underlying: str | None = None
    timeframe: str = "1m"
    mode: str = Field(default="CURRENT", pattern="^(CURRENT|NEAR)$")
    source: str = "angelone"

    @model_validator(mode="after")
    def validate_input_mode(self):
        if self.points is None:
            if self.start_date is None or self.end_date is None:
                raise ValueError("start_date and end_date are required when points are omitted")
            if not self.spot_instrument or not self.underlying:
                raise ValueError("spot_instrument and underlying are required when points are omitted")
        elif not self.points:
            raise ValueError("points cannot be empty")
        return self


# API-safe built-in strategy registry. The callable runner remains generic;
# additional strategy adapters can be registered without changing execution.
def _strategy_registry() -> dict[str, Any]:
    return {
        "gap_threshold": lambda current, history: (
            "BUY" if current.gap > 0 else "SELL" if current.gap < 0 else "HOLD"
        ),
    }


def _load_points(request: StrategyRunRequest) -> tuple[CashFutureHistoryPoint, ...] | Any:
    if request.points is not None:
        return tuple(CashFutureHistoryPoint(**point.model_dump()) for point in request.points)

    assert request.start_date is not None and request.end_date is not None
    assert request.spot_instrument is not None and request.underlying is not None
    catalog = HistoricalCatalog(settings.BACKTEST_DATA_DB)
    contracts = ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
    try:
        selection = CashFutureHistorySelection(
            spot_instrument=request.spot_instrument,
            exchange=request.exchange,
            underlying=request.underlying,
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=request.timeframe,
            contract_month=request.contract_month,
            mode=request.mode,
            source=request.source,
        )
        return CashFutureHistoricalLoader(catalog, contracts).iter_points(selection)
    except Exception:
        catalog.close()
        contracts.close()
        raise


@router.post("/strategy-run")
def strategy_run(request: StrategyRunRequest):
    strategy = _strategy_registry().get(request.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"unknown Cash-Future strategy: {request.strategy_id}")

    try:
        points = _load_points(request)
        result = run_cash_future_strategy(
            points,
            strategy,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            config=CashFutureStrategyConfig(
                initial_capital=request.initial_capital,
                execution_model=request.execution_model,
                charges_per_trade=request.charges_per_trade,
                funding_cost_per_trade=request.funding_cost_per_trade,
                start_date=request.start_date,
                end_date=request.end_date,
                contract_month=request.contract_month,
            ),
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if request.points is None:
            # The loader's generator is consumed synchronously by the runner.
            # Close the per-request SQLite handles after execution.
            try:
                points.gi_frame.f_locals["self"].catalog.close()
                points.gi_frame.f_locals["self"].contract_catalog.close()
            except (AttributeError, KeyError, TypeError):
                pass

    return {
        "status": "success",
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "initial_capital": result.initial_capital,
        "final_capital": result.final_capital,
        "net_profit": result.net_profit,
        "signal_count": len(result.signals),
        "trade_count": len(result.trades),
        "signals": result.signals,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
    }


__all__ = ["router"]

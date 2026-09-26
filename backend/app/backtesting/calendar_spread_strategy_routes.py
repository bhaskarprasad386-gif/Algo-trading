"""API surface for historical Calendar-Spread strategy runs.

This first-class route intentionally accepts normalized Near/Far executable
quotes directly. It does not fabricate historical contracts or prices.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.backtesting.arbitrage_backtest_suite import build_strategy_adapter
from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.result_ledger import BacktestResultLedger
from app.core.config import settings

router = APIRouter(
    prefix="/api/v1/backtesting/calendar-spread",
    tags=["Calendar-Spread Backtesting"],
)


class CalendarSpreadPointRequest(BaseModel):
    timestamp: datetime
    underlying: str = Field(min_length=1)
    near_expiry: int = Field(ge=0)
    far_expiry: int = Field(ge=0)
    near_bid: float = Field(ge=0)
    near_ask: float = Field(ge=0)
    far_bid: float = Field(ge=0)
    far_ask: float = Field(ge=0)
    lot_size: int = Field(gt=0)
    strike: float | None = Field(default=None, ge=0)
    option_type: str | None = None

    @model_validator(mode="after")
    def validate_quotes(self):
        if self.far_expiry <= self.near_expiry:
            raise ValueError("far_expiry must be later than near_expiry")
        if self.near_ask < self.near_bid or self.far_ask < self.far_bid:
            raise ValueError("calendar bid/ask quotes are invalid")
        if self.option_type is not None and self.option_type not in {"CALL", "PUT"}:
            raise ValueError("option_type must be CALL or PUT")
        return self


class CalendarSpreadRunRequest(BaseModel):
    strategy_id: str = Field(default="calendar-spread", min_length=1)
    strategy_version: str = Field(default="1", min_length=1)
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None
    direction: str = Field(default="LONG_NEAR_SHORT_FAR")
    fees_per_unit: float = Field(default=0.0, ge=0)
    initial_capital: float = Field(default=100_000_000.0, gt=0)
    points: list[CalendarSpreadPointRequest]

    @model_validator(mode="after")
    def validate_window(self):
        if self.strategy_id != "calendar-spread":
            raise ValueError("strategy_id must be calendar-spread")
        if self.direction not in {"LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"}:
            raise ValueError("invalid calendar spread direction")
        if not self.points:
            raise ValueError("points cannot be empty")
        if self.start_timestamp and self.end_timestamp and self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp cannot be before start_timestamp")
        if self.start_timestamp and self.end_timestamp:
            if self.start_timestamp.tzinfo and self.end_timestamp.tzinfo:
                pass
        return self


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def _normalise_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialise_row(row):
    return {
        "trade_id": row["trade_id"],
        "timestamp_ns": row["timestamp_ns"],
        "instrument": row["instrument"],
        "side": row["side"],
        "quantity": row["quantity"],
        "entry_price": row["entry_price"],
        "exit_price": row["exit_price"],
        "gross_pnl": row["gross_pnl"],
        "fees": row["fees"],
        "slippage": row["slippage"],
        "net_pnl": row["net_pnl"],
        "contract": row["contract"],
        "expiry": row["expiry"],
        "strike": row["strike"],
        "leg": row["leg"],
        "data_resolution": row["data_resolution"],
    }


@router.post("/strategy-run")
def strategy_run(request: CalendarSpreadRunRequest):
    start = _normalise_timestamp(request.start_timestamp) if request.start_timestamp else min(
        (_normalise_timestamp(point.timestamp) for point in request.points)
    )
    end = _normalise_timestamp(request.end_timestamp) if request.end_timestamp else max(
        (_normalise_timestamp(point.timestamp) for point in request.points)
    )
    points = [
        point for point in request.points
        if start <= _normalise_timestamp(point.timestamp) <= end
    ]
    if not points:
        raise HTTPException(status_code=422, detail="no calendar-spread points inside requested timestamp window")

    timestamps = [_ns(point.timestamp) for point in points]
    start_ns, end_ns = min(timestamps), max(timestamps)
    resolution = BacktestResolution(
        resolution="s",
        source="provided-calendar-spread-points",
        start_ns=start_ns,
        end_ns=end_ns,
    )
    run_id = f"calendar-spread-{uuid4().hex}"
    parameters = {
        "direction": request.direction,
        "fees_per_unit": request.fees_per_unit,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
    }
    spec = BacktestRunSpec(
        run_id=run_id,
        strategy_id="calendar-spread",
        strategy_version=request.strategy_version,
        instrument=points[0].underlying,
        start_ns=start_ns,
        end_ns=end_ns,
        resolution=resolution,
        parameters=parameters,
        initial_capital=request.initial_capital,
    )
    ledger = BacktestResultLedger(settings.BACKTEST_RESULT_LEDGER_DB)
    writer = BacktestRunWriter(ledger, spec)
    service = HistoricalArbitrageBacktestService(writer)

    def events():
        for point in sorted(points, key=lambda item: _ns(item.timestamp)):
            yield {
                "timestamp_ns": _ns(point.timestamp),
                "data_resolution": "s",
                "near": {
                    "timestamp_ns": _ns(point.timestamp),
                    "underlying": point.underlying,
                    "expiry": point.near_expiry,
                    "bid": point.near_bid,
                    "ask": point.near_ask,
                    "lot_size": point.lot_size,
                    "strike": point.strike,
                    "option_type": point.option_type,
                },
                "far": {
                    "timestamp_ns": _ns(point.timestamp),
                    "underlying": point.underlying,
                    "expiry": point.far_expiry,
                    "bid": point.far_bid,
                    "ask": point.far_ask,
                    "lot_size": point.lot_size,
                    "strike": point.strike,
                    "option_type": point.option_type,
                },
            }

    try:
        result = service.run_strategy(
            "calendar-spread",
            events(),
            parameters={
                "direction": request.direction,
                "fees_per_unit": request.fees_per_unit,
            },
        )
        trades = [_serialise_row(row) for row in ledger.trades(run_id)]
        return {
            "status": "success",
            "run_id": run_id,
            "strategy_id": "calendar-spread",
            "strategy_version": request.strategy_version,
            "direction": request.direction,
            "start_timestamp": start.isoformat(),
            "end_timestamp": end.isoformat(),
            "completed_trades": result.completed_trades,
            "unresolved_trades": result.unresolved_trades,
            "net_profit": result.realized_pnl,
            "trade_count": len(trades),
            "trades": trades,
        }
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        ledger.close()


@router.get("/strategy-run/{run_id}")
def strategy_run_result(run_id: str):
    ledger = BacktestResultLedger(settings.BACKTEST_RESULT_LEDGER_DB)
    try:
        try:
            run = ledger.run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        trades = [_serialise_row(row) for row in ledger.trades(run_id)]
        return {
            "status": run["status"],
            "run_id": run_id,
            "trades": trades,
            "trade_count": len(trades),
            "net_profit": ledger.trade_net_pnl(run_id),
        }
    finally:
        ledger.close()



REPLAY_TIMEFRAMES = ("1s", "30s", "1m", "5m", "15m", "30m", "1h")
_REPLAY_SECONDS = {"1s": 1, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


class CalendarSpreadReplayRequest(BaseModel):
    trading_date: date
    underlying: str = Field(min_length=1)
    timeframe: str = Field(default="1m")
    points: list[CalendarSpreadPointRequest]

    @model_validator(mode="after")
    def validate_replay(self):
        if self.timeframe not in REPLAY_TIMEFRAMES:
            raise ValueError("invalid replay timeframe")
        if not self.points:
            raise ValueError("points cannot be empty")
        return self


def _replay_deltas(points: list[CalendarSpreadPointRequest]) -> list[float]:
    ordered = sorted((_normalise_timestamp(point.timestamp) for point in points))
    return [
        (current - previous).total_seconds()
        for previous, current in zip(ordered, ordered[1:])
        if current > previous
    ]


def _available_replay_intervals(points: list[CalendarSpreadPointRequest]) -> list[str]:
    deltas = _replay_deltas(points)
    if not deltas:
        return ["1m", "5m", "15m", "30m", "1h"]
    minimum = min(deltas)
    return [name for name, seconds in _REPLAY_SECONDS.items() if minimum <= seconds]


@router.post("/replay")
def calendar_spread_replay(request: CalendarSpreadReplayRequest):
    points = sorted(request.points, key=lambda point: _normalise_timestamp(point.timestamp))
    available = _available_replay_intervals(points)
    return {
        "status": "success",
        "trading_date": request.trading_date,
        "underlying": request.underlying.strip().upper(),
        "timeframe": request.timeframe,
        "source_timeframe": "1s" if "1s" in available and _replay_deltas(points) and min(_replay_deltas(points)) < 30 else (
            "30s" if "30s" in available and _replay_deltas(points) and min(_replay_deltas(points)) < 60 else "1m"
        ),
        "source_min_interval_seconds": min(_replay_deltas(points)) if _replay_deltas(points) else None,
        "available_replay_intervals": available,
        "count": len(points),
        "series": [
            {
                "timestamp": _normalise_timestamp(point.timestamp).isoformat(),
                "underlying": point.underlying,
                "near_expiry": point.near_expiry,
                "far_expiry": point.far_expiry,
                "near_bid": point.near_bid,
                "near_ask": point.near_ask,
                "far_bid": point.far_bid,
                "far_ask": point.far_ask,
                "lot_size": point.lot_size,
                "strike": point.strike,
                "option_type": point.option_type,
            }
            for point in points
        ],
    }


class CalendarSpreadHistoricalReplayRequest(BaseModel):
    underlying: str = Field(min_length=1)
    exchange: str = Field(default="NFO", min_length=1)
    start_date: date
    end_date: date
    near_contract_month: str
    far_contract_month: str
    source: str = Field(default="angelone", min_length=1)
    source_timeframe: str = Field(default="1s", min_length=1)
    replay_timeframe: str = Field(default="1s")
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None

    @model_validator(mode="after")
    def validate_historical_replay(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.replay_timeframe not in REPLAY_TIMEFRAMES:
            raise ValueError("invalid replay timeframe")
        if self.start_timestamp and self.end_timestamp and self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp cannot be before start_timestamp")
        return self


def _historical_point_row(point):
    return {
        "timestamp": point.timestamp.isoformat(),
        "underlying": point.underlying,
        "near_expiry": point.near_expiry.isoformat(),
        "far_expiry": point.far_expiry.isoformat(),
        "near_bid": point.near_bid,
        "near_ask": point.near_ask,
        "far_bid": point.far_bid,
        "far_ask": point.far_ask,
        "lot_size": point.lot_size,
        "strike": point.strike,
        "option_type": point.option_type,
    }


@router.post("/historical-replay")
def calendar_spread_historical_replay(request: CalendarSpreadHistoricalReplayRequest):
    from app.backtesting.calendar_spread_historical_loader import (
        CalendarSpreadHistoricalLoader,
        CalendarSpreadHistorySelection,
    )
    from app.backtesting.contract_master import ContractMasterCatalog
    from app.backtesting.historical_catalog import HistoricalCatalog

    selection = CalendarSpreadHistorySelection(
        underlying=request.underlying.strip().upper(),
        exchange=request.exchange.strip().upper(),
        start_date=request.start_date,
        end_date=request.end_date,
        near_contract_month=request.near_contract_month.strip(),
        far_contract_month=request.far_contract_month.strip(),
        timeframe=request.source_timeframe,
        source=request.source.strip(),
    )
    data_catalog = HistoricalCatalog(settings.BACKTEST_DATA_DB)
    contract_catalog = ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
    try:
        loader = CalendarSpreadHistoricalLoader(data_catalog, contract_catalog)
        points = list(loader.iter_points(selection))
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        data_catalog.close()
        contract_catalog.close()

    start = _normalise_timestamp(request.start_timestamp) if request.start_timestamp else None
    end = _normalise_timestamp(request.end_timestamp) if request.end_timestamp else None
    if start is not None:
        points = [point for point in points if point.timestamp >= start]
    if end is not None:
        points = [point for point in points if point.timestamp <= end]
    if not points:
        raise HTTPException(status_code=422, detail="no historical Calendar Spread points inside requested window")

    source_points = [
        CalendarSpreadPointRequest(
            timestamp=point.timestamp,
            underlying=point.underlying,
            near_expiry=point.near_expiry.toordinal(),
            far_expiry=point.far_expiry.toordinal(),
            near_bid=point.near_bid,
            near_ask=point.near_ask,
            far_bid=point.far_bid,
            far_ask=point.far_ask,
            lot_size=point.lot_size,
            strike=point.strike,
            option_type=point.option_type,
        )
        for point in points
    ]
    available = _available_replay_intervals(source_points)
    if request.replay_timeframe not in available:
        raise HTTPException(
            status_code=422,
            detail=f"replay timeframe {request.replay_timeframe} is not supported by source cadence; available={available}",
        )
    return {
        "status": "success",
        "underlying": selection.underlying,
        "near_contract_month": selection.near_contract_month,
        "far_contract_month": selection.far_contract_month,
        "source_timeframe": request.source_timeframe,
        "replay_timeframe": request.replay_timeframe,
        "source_min_interval_seconds": min(_replay_deltas(source_points)) if len(source_points) > 1 else None,
        "available_replay_intervals": available,
        "count": len(points),
        "series": [_historical_point_row(point) for point in points],
    }


__all__ = ["router", "REPLAY_TIMEFRAMES"]

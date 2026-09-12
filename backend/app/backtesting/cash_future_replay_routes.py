"""Durable paired Cash-Future replay API."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.backtesting.cash_future_historical_loader import CashFutureHistoricalLoader, CashFutureHistorySelection
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_catalog import HistoricalCatalog
from app.core.config import settings
from app.scanner.cash_future_history import build_graph_series

REPLAY_TIMEFRAMES = ("1s", "1m", "5m", "15m", "30m")

router = APIRouter(tags=["Cash-Future Backtesting"])


def _available_replay_intervals(points) -> list[str]:
    """Expose only intervals supported by the requested source cadence; never claim fake 1s data."""
    deltas = [
        int((current.timestamp - previous.timestamp).total_seconds())
        for previous, current in zip(points, points[1:])
        if current.timestamp > previous.timestamp
    ]
    min_delta = min(deltas) if deltas else None
    if min_delta is None:
        return ["1m", "5m", "15m", "30m"]
    available: list[str] = []
    cadence_seconds = {"1s": 1, "1m": 60, "5m": 300, "15m": 900, "30m": 1800}
    for interval, seconds in cadence_seconds.items():
        if min_delta <= seconds:
            available.append(interval)
    return available


@router.get("/replay")
def cash_future_replay(
    trading_date: date = Query(...),
    symbol: str = Query(..., min_length=1),
    contract_month: str | None = Query(None),
    timeframe: str = Query("1m", pattern="^(1s|1m|5m|15m|30m)$"),
    mode: str = Query("CURRENT", pattern="^(CURRENT|NEAR)$"),
    source: str = Query("angelone", min_length=1),
    spot_instrument: str | None = Query(None),
    exchange: str = Query("NSE", min_length=1),
):
    """Return actual paired Cash/Future/Gap observations; never fabricate resolution."""
    underlying = symbol.strip().upper()
    spot = (spot_instrument or underlying).strip().upper()
    catalog = HistoricalCatalog(settings.BACKTEST_DATA_DB)
    contracts = ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
    try:
        selection = CashFutureHistorySelection(
            spot_instrument=spot,
            exchange=exchange.strip().upper(),
            underlying=underlying,
            start_date=trading_date,
            end_date=trading_date,
            timeframe=timeframe.strip(),
            contract_month=contract_month,
            mode=mode.upper(),
            source=source.strip(),
        )
        points = list(CashFutureHistoricalLoader(catalog, contracts).iter_points(selection))
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        catalog.close()
        contracts.close()

    if not points:
        raise HTTPException(status_code=404, detail="no paired Cash-Future history found for the requested date/symbol")

    points.sort(key=lambda point: point.timestamp)
    deltas = [
        int((current.timestamp - previous.timestamp).total_seconds())
        for previous, current in zip(points, points[1:])
        if current.timestamp > previous.timestamp
    ]
    min_delta = min(deltas) if deltas else None
    available = _available_replay_intervals(points)

    series = [
        {
            "timestamp": point.timestamp.isoformat(),
            "cash_price": point.cash_price,
            "future_price": point.future_price,
            "gap": point.gap,
            "gap_pct": point.gap_pct,
            "contract_month": point.contract_month,
            "lot_size": point.lot_size,
            "margin_required": point.margin_required,
            "volume": point.volume,
            "oi": point.oi,
            "cash_bid": point.cash_bid,
            "cash_ask": point.cash_ask,
            "future_bid": point.future_bid,
            "future_ask": point.future_ask,
            "charges": point.charges,
            "funding_cost": point.funding_cost,
        }
        for point in points
    ]
    return {
        "status": "success",
        "trading_date": trading_date,
        "symbol": underlying,
        "contract_month": contract_month,
        "contracts_seen": sorted({point.contract_month for point in points}),
        "mode": mode.upper(),
        "timeframe": timeframe.strip(),
        "source": source.strip(),
        "count": len(series),
        "first_timestamp": points[0].timestamp.isoformat(),
        "last_timestamp": points[-1].timestamp.isoformat(),
        "source_min_interval_seconds": min_delta,
        "available_replay_intervals": available,
        "series": series,
        "graph": build_graph_series(points, contract_month=contract_month),
    }


__all__ = ["router", "REPLAY_TIMEFRAMES"]

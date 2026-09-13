"""Materialize the completed all-stock Cash-Future catalog into backtest history."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.scanner.cash_future_catalog_materializer import materialize_cash_future_history

from .cash_future_universe import CashFutureFnoUniverse
from .cash_future_universe_download_plan import CashFutureUniverseDownloadPlan
from .historical_catalog import HistoricalCatalog


def materialize_cash_future_universe_history(
    db: Session,
    catalog: HistoricalCatalog,
    *,
    download_plan: CashFutureUniverseDownloadPlan,
    universe: CashFutureFnoUniverse,
    source: str = "angelone",
    timeframe: str = "1m",
    margin_required: float = 0.0,
    batch_size: int = 1000,
) -> int:
    """Convert synchronized catalog bars to durable CashFutureHistory rows.

    The download plan is the source of truth for instruments; contract metadata
    comes from the resolved stock universe. Each contract is materialized
    independently, so contracts never get mixed.
    """
    metadata = {
        (item.underlying.upper(), item.future_token): item
        for item in universe.stocks
    }
    inserted = 0
    for job in download_plan.jobs:
        for request in job.futures:
            parts = request.instrument.split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"invalid future instrument: {request.instrument}")
            item = metadata.get((job.underlying.upper(), parts[1]))
            if item is None:
                raise ValueError(
                    f"download plan future has no universe metadata: {request.instrument}"
                )
            inserted += materialize_cash_future_history(
                db,
                catalog,
                source=source,
                spot_instrument=job.spot.request.instrument,
                future_instrument=request.instrument,
                symbol=item.underlying,
                contract_month=item.contract_month,
                lot_size=item.lot_size,
                margin_required=margin_required,
                expiry_date=item.expiry,
                timeframe=timeframe,
                start_ns=request.start_ns,
                end_ns=request.end_ns,
                batch_size=batch_size,
            )
    return inserted


__all__ = ["materialize_cash_future_universe_history"]

"""Historical F&O stock-universe selection for backtests.

The universe is resolved from persisted instrument/master data rather than a
hard-coded list. Index derivatives are excluded because Cash-Future requires
an individual equity/cash leg.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument


@dataclass(frozen=True)
class FnoStockInstrument:
    symbol: str
    token: str
    exchange: str
    instrument_type: str


def _is_stock_future(row: Instrument) -> bool:
    exchange = (row.exchange or "").upper()
    instrument_type = (row.instrument_type or "").upper()
    symbol = (row.symbol or "").upper()
    if exchange not in {"NFO", "NSEFO", "NSE_FO", "F&O", "FO"}:
        return False
    if not any(kind in instrument_type for kind in ("FUTSTK", "STOCK_FUTURE", "FUTURE_STOCK")):
        return False
    # Index contracts must never enter a stock Cash-Future backtest.
    if any(index_name in symbol for index_name in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")):
        return False
    return True


def load_fno_stock_universe(db: Session) -> list[FnoStockInstrument]:
    """Return every eligible persisted stock-future instrument, deterministically."""
    rows = db.scalars(select(Instrument).order_by(Instrument.symbol, Instrument.token)).all()
    unique: dict[str, FnoStockInstrument] = {}
    for row in rows:
        if not _is_stock_future(row):
            continue
        key = row.symbol.upper()
        unique.setdefault(
            key,
            FnoStockInstrument(
                symbol=key,
                token=row.token,
                exchange=row.exchange,
                instrument_type=row.instrument_type or "",
            ),
        )
    return list(unique.values())


def universe_coverage(universe: list[FnoStockInstrument]) -> dict[str, object]:
    """Return an auditable coverage summary for the selected stock universe."""
    symbols = [item.symbol for item in universe]
    return {
        "universe": "FULL_FNO_STOCK",
        "symbols_total": len(symbols),
        "symbols": symbols,
        "index_derivatives_excluded": True,
        "selection_source": "persisted_instruments",
    }

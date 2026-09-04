"""Bounded, durable result storage for large backtests."""

from __future__ import annotations

from datetime import date, datetime
import json
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BacktestJobResultChunk


def persist_trade_chunk(db: Session, *, job_id: str, sequence: int, symbol: str, trades: Sequence[object]) -> BacktestJobResultChunk:
    """Write one bounded trade chunk and commit it immediately."""
    if not job_id or not symbol or sequence < 0:
        raise ValueError("invalid result chunk identity")
    if not trades:
        raise ValueError("result chunk cannot be empty")
    payload = [{
        "entry_timestamp": getattr(t, "entry_timestamp"),
        "exit_timestamp": getattr(t, "exit_timestamp"),
        "entry_price": getattr(t, "entry_price"),
        "exit_price": getattr(t, "exit_price"),
        "quantity": getattr(t, "quantity"),
        "gross_pnl": getattr(t, "gross_pnl"),
        "costs": getattr(t, "costs"),
        "net_pnl": getattr(t, "net_pnl"),
    } for t in trades]
    encoded = json.dumps(payload, default=_json_default, separators=(",", ":"), allow_nan=False)
    item = db.scalar(select(BacktestJobResultChunk).where(
        BacktestJobResultChunk.job_id == job_id,
        BacktestJobResultChunk.sequence == sequence,
    ))
    if item is None:
        item = BacktestJobResultChunk(job_id=job_id, sequence=sequence, symbol=symbol, result_json=encoded)
        db.add(item)
    else:
        item.symbol = symbol
        item.result_json = encoded
    db.commit()
    return item


def _json_default(value: object):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unsupported result value: {type(value).__name__}")

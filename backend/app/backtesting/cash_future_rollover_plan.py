"""Historical Cash-Future contract segments with explicit rollover boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .contract_master import ContractMasterCatalog, ContractRecord


@dataclass(frozen=True)
class CashFutureSegment:
    start: date
    end: date
    future: ContractRecord


def build_rollover_segments(
    catalog: ContractMasterCatalog,
    *,
    exchange: str,
    underlying: str,
    start: date,
    end: date,
    mode: str,
) -> tuple[CashFutureSegment, ...]:
    """Resolve a future independently for each historical date and coalesce identity runs.

    This deliberately fails closed when a historical snapshot is unavailable. It never
    invents an expired token or silently substitutes today's contract.
    """
    if end < start:
        raise ValueError("end must not precede start")
    mode = mode.upper()
    if mode not in {"CURRENT", "NEAR"}:
        raise ValueError("mode must be CURRENT or NEAR")

    segments: list[CashFutureSegment] = []
    cursor = start
    while cursor <= end:
        contract = catalog.resolve(exchange=exchange, underlying=underlying, as_of=cursor, mode=mode)
        if segments and segments[-1].future.token == contract.token:
            previous = segments[-1]
            segments[-1] = CashFutureSegment(previous.start, cursor, previous.future)
        else:
            segments.append(CashFutureSegment(cursor, cursor, contract))
        cursor += timedelta(days=1)
    return tuple(segments)


def build_mode_segments(
    catalog: ContractMasterCatalog,
    *,
    exchange: str,
    underlying: str,
    start: date,
    end: date,
    mode: str,
) -> tuple[tuple[CashFutureSegment, ...], ...]:
    """Return CURRENT/NEAR segments; BOTH returns both independent legs."""
    normalized = mode.upper()
    if normalized == "BOTH":
        modes = ("CURRENT", "NEAR")
    elif normalized in {"CURRENT", "NEAR"}:
        modes = (normalized,)
    else:
        raise ValueError("mode must be CURRENT, NEAR or BOTH")
    return tuple(
        build_rollover_segments(catalog, exchange=exchange, underlying=underlying,
                                start=start, end=end, mode=leg)
        for leg in modes
    )

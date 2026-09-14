"""Historical Cash-Future contract segments with explicit rollover boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from collections.abc import Iterable

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
    session_days: Iterable[date] | None = None,
) -> tuple[CashFutureSegment, ...]:
    """Resolve a future only on requested historical session days.

    This deliberately fails closed when a historical snapshot is unavailable. It never
    invents an expired token or silently substitutes today's contract. When session_days
    is supplied, weekends/holidays outside those sessions are not treated as data days.
    """
    if end < start:
        raise ValueError("end must not precede start")
    mode = mode.upper()
    if mode not in {"CURRENT", "NEAR"}:
        raise ValueError("mode must be CURRENT or NEAR")

    days = tuple(sorted({day for day in (session_days or ()) if start <= day <= end}))
    if session_days is None:
        days = tuple(start + timedelta(days=i) for i in range((end - start).days + 1))
    if not days:
        return ()

    segments: list[CashFutureSegment] = []
    for current_day in days:
        contract = catalog.resolve(exchange=exchange, underlying=underlying, as_of=current_day, mode=mode)
        same_contract = bool(segments) and segments[-1].future.token == contract.token
        # With an explicit session calendar, adjacent entries in `days` are adjacent
        # trading sessions even when a weekend/holiday lies between their calendar dates.
        # Keep one segment for the same contract instead of creating duplicate download
        # requests for the same token. Without an explicit calendar, retain the legacy
        # calendar-day contiguity rule.
        contiguous = (
            same_contract
            and (
                session_days is not None
                or segments[-1].end + timedelta(days=1) == current_day
            )
        )
        if contiguous:
            previous = segments[-1]
            segments[-1] = CashFutureSegment(previous.start, current_day, previous.future)
        else:
            segments.append(CashFutureSegment(current_day, current_day, contract))
    return tuple(segments)


def build_mode_segments(
    catalog: ContractMasterCatalog,
    *,
    exchange: str,
    underlying: str,
    start: date,
    end: date,
    mode: str,
    session_days: Iterable[date] | None = None,
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
        build_rollover_segments(
            catalog,
            exchange=exchange,
            underlying=underlying,
            start=start,
            end=end,
            mode=leg,
            session_days=session_days,
        )
        for leg in modes
    )

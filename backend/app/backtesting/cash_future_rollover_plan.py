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
    if type(start) is not date or type(end) is not date:
        raise TypeError("start and end must be dates")
    if not isinstance(exchange, str) or not exchange.strip():
        raise ValueError("exchange is required")
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError("underlying is required")
    if not isinstance(mode, str):
        raise ValueError("mode must be CURRENT or NEAR")
    if end < start:
        raise ValueError("end must not precede start")
    mode = mode.strip().upper()
    if mode not in {"CURRENT", "NEAR"}:
        raise ValueError("mode must be CURRENT or NEAR")

    if session_days is None:
        # A calendar-day range must not manufacture weekend trading sessions. Explicit
        # session_days remains the escape hatch for an exchange-specific holiday calendar.
        days = tuple(
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
        )
    else:
        days = tuple(sorted({day for day in session_days if start <= day <= end}))
    if not days:
        return ()

    segments: list[CashFutureSegment] = []
    for current_day in days:
        contract = catalog.resolve(exchange=exchange.strip(), underlying=underlying.strip().upper(), as_of=current_day, mode=mode)
        same_contract = bool(segments) and (
            segments[-1].future.exchange,
            segments[-1].future.symbol,
            segments[-1].future.token,
            segments[-1].future.expiry,
            segments[-1].future.instrument_type,
            segments[-1].future.underlying,
            segments[-1].future.lot_size,
            segments[-1].future.tick_size,
        ) == (
            contract.exchange,
            contract.symbol,
            contract.token,
            contract.expiry,
            contract.instrument_type,
            contract.underlying,
            contract.lot_size,
            contract.tick_size,
        )
        # A contract identity is the durable segment boundary. The same historical
        # contract may span weekends/holidays, and splitting it by calendar gaps would
        # create duplicate requests for the same token. Session-aware consumers already
        # prevent weekend/holiday rows from being treated as market data.
        contiguous = same_contract
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

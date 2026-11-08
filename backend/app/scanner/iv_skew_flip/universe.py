"""Fail-closed contract universe selection for the IV Skew / IV Flip scanner.

This module only resolves Angel One instrument-master rows. It does not subscribe,
open sockets, place orders, or alter any existing scanner universe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ResolvedOptionContract:
    symbol: str
    token: str
    exchange: str
    expiry: str
    strike: float
    option_type: str
    lot_size: int


@dataclass(frozen=True)
class ResolvedOptionUniverse:
    underlying: str
    expiry_type: str
    expiry: str
    spot_exchange: str
    option_exchange: str
    strike_step: float
    strikes: tuple[float, ...]
    contracts: tuple[ResolvedOptionContract, ...]

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(contract.token for contract in self.contracts)


def _parse_expiry(value: object) -> date | None:
    raw = str(value or "").strip()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(raw.upper(), fmt).date()
        except ValueError:
            continue
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def resolve_option_universe(
    instruments: Iterable[Mapping[str, object]],
    *,
    underlying: str,
    expiry_type: str,
    spot: float,
    today: date,
    strike_count: int = 7,
) -> ResolvedOptionUniverse:
    """Resolve one exact expiry and a centered odd number of available strikes.

    WEEKLY excludes the final listed expiry in each calendar month (the
    monthly contract), then selects the nearest remaining expiry. MONTHLY
    selects the final listed expiry in the nearest month with future contracts.
    This prevents the same monthly contract from appearing in both streams.
    """
    symbol = str(underlying).strip().upper()
    kind = str(expiry_type).strip().upper()
    if symbol not in {"NIFTY", "BANKNIFTY"}:
        raise ValueError("only NIFTY and BANKNIFTY are configured for this scanner")
    allowed = {"NIFTY": {"WEEKLY", "MONTHLY"}, "BANKNIFTY": {"MONTHLY"}}[symbol]
    if kind not in allowed:
        expected = " or ".join(sorted(allowed))
        raise ValueError(f"{symbol} is configured for {expected} expiry only")
    if (
        isinstance(spot, bool)
        or not isinstance(spot, (int, float))
        or not isfinite(float(spot))
        or spot <= 0
    ):
        raise ValueError("a finite positive live spot price is required")
    if strike_count < 3 or strike_count % 2 != 1:
        raise ValueError("strike_count must be an odd integer of at least 3")

    option_exchange = "NFO"
    spot_exchange = "NSE"
    rows: list[tuple[date, str, float, str, int, str, str]] = []
    for item in instruments:
        if str(item.get("exch_seg", "")).strip().upper() != option_exchange:
            continue
        if str(item.get("instrumenttype", "")).strip().upper() != "OPTIDX":
            continue
        if str(item.get("name", "")).strip().upper() != symbol:
            continue
        expiry = _parse_expiry(item.get("expiry"))
        if expiry is None or expiry < today:
            continue
        option_symbol = str(item.get("symbol", "")).strip().upper()
        option_type = "CE" if option_symbol.endswith("CE") else "PE" if option_symbol.endswith("PE") else ""
        token = str(item.get("token", "")).strip()
        lot_size = _positive_int(item.get("lotsize"))
        raw_strike = item.get("strike")
        if isinstance(raw_strike, bool):
            continue
        try:
            strike = float(raw_strike) / 100.0
        except (TypeError, ValueError, OverflowError):
            continue
        if not option_type or not token or lot_size is None or not isfinite(strike) or strike <= 0:
            continue
        rows.append((expiry, str(item.get("expiry")).strip(), strike, option_type, lot_size, token, str(item.get("symbol", "")).strip()))

    if not rows:
        raise LookupError(f"no valid {symbol} option contracts found in instrument master")

    expiry_dates = sorted({row[0] for row in rows})
    # The instrument master is the source of truth for the actual expiry
    # calendar. For each month, its final listed expiry is the monthly
    # contract; weekly selection must never reuse that same date.
    monthly_expiries = {
        max(day for day in expiry_dates if (day.year, day.month) == (year, month))
        for year, month in {(day.year, day.month) for day in expiry_dates}
    }
    if kind == "WEEKLY":
        weekly_expiries = [day for day in expiry_dates if day not in monthly_expiries]
        if not weekly_expiries:
            raise LookupError(f"no distinct weekly {symbol} expiry found in instrument master")
        selected_date = weekly_expiries[0]
    else:
        nearest_month = (expiry_dates[0].year, expiry_dates[0].month)
        selected_date = max(
            day for day in expiry_dates
            if (day.year, day.month) == nearest_month
        )
    selected_rows = [row for row in rows if row[0] == selected_date]
    raw_expiries = {row[1] for row in selected_rows}
    if len(raw_expiries) != 1:
        raise LookupError("instrument master has conflicting expiry representations for selected contracts")
    strikes = sorted({row[2] for row in selected_rows})
    if len(strikes) < strike_count:
        raise LookupError(f"only {len(strikes)} strikes available for {symbol}; {strike_count} required")

    atm_index = min(range(len(strikes)), key=lambda idx: abs(strikes[idx] - float(spot)))
    half = strike_count // 2
    start = max(0, min(atm_index - half, len(strikes) - strike_count))
    selected_strikes = tuple(strikes[start:start + strike_count])
    if len(selected_strikes) != strike_count:
        raise LookupError("could not form a complete centered strike window")
    steps = [b - a for a, b in zip(selected_strikes, selected_strikes[1:])]
    strike_step = min(steps) if steps and all(step > 0 for step in steps) else 0.0
    if strike_step <= 0:
        raise LookupError("invalid strike ladder")

    contracts = []
    seen: set[tuple[float, str]] = set()
    seen_tokens: set[str] = set()
    for row_expiry, _, strike, option_type, lot_size, token, trading_symbol in selected_rows:
        if strike not in selected_strikes:
            continue
        key = (strike, option_type)
        if token in seen_tokens:
            raise LookupError(f"duplicate instrument token in {symbol} option universe: {token}")
        seen_tokens.add(token)
        if key in seen:
            raise LookupError(f"duplicate {symbol} contract for strike={strike} type={option_type}")
        seen.add(key)
        contracts.append(ResolvedOptionContract(
            symbol=trading_symbol,
            token=token, exchange=option_exchange,
            expiry=row_expiry.isoformat(), strike=strike,
            option_type=option_type, lot_size=lot_size,
        ))
    expected_pairs = {(strike, side) for strike in selected_strikes for side in ("CE", "PE")}
    if seen != expected_pairs:
        missing = sorted(expected_pairs - seen)
        raise LookupError(f"incomplete CE/PE strike window; missing={missing}")
    contracts.sort(key=lambda item: (item.strike, item.option_type))
    return ResolvedOptionUniverse(
        underlying=symbol, expiry_type=kind, expiry=selected_date.isoformat(),
        spot_exchange=spot_exchange, option_exchange=option_exchange,
        strike_step=strike_step, strikes=selected_strikes,
        contracts=tuple(contracts),
    )

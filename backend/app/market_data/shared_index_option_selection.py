"""Strict contract selection for the isolated shared index market-data runner.

This module selects only the locked NIFTY weekly and BANKNIFTY monthly option
windows. It is data-only and deliberately does not create broker connections.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable


def _parse_expiry(value: object) -> date:
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid expiry: {value!r}")


def _strike(value: object) -> float:
    raw = float(value)
    if raw <= 0:
        raise ValueError("strike must be positive")
    # Angel One index option master stores strikes in paise-like x100 units.
    return raw / 100.0


@dataclass(frozen=True)
class IndexOptionContract:
    symbol: str
    underlying: str
    expiry: str
    strike: float
    option_type: str
    token: str
    trading_symbol: str
    lot_size: int | None = None


@dataclass(frozen=True)
class IndexOptionSelection:
    underlying: str
    expiry: str
    atm_strike: float
    radius: int
    contracts: tuple[IndexOptionContract, ...]

    @property
    def contract_count(self) -> int:
        return len(self.contracts)

    @property
    def strikes(self) -> tuple[float, ...]:
        return tuple(sorted({c.strike for c in self.contracts}))


_LOCKED_UNIVERSE = {
    "NIFTY": {"expiry_type": "WEEKLY", "radius": 6},
    "BANKNIFTY": {"expiry_type": "MONTHLY", "radius": 5},
}


def select_locked_index_options(
    instruments: Iterable[dict[str, Any]],
    *,
    underlying: str,
    expiry: str,
    spot_price: float,
) -> IndexOptionSelection:
    """Select a complete symmetric strike window; reject partial chains.

    Caller supplies the already-classified expiry. Expiry matching is by calendar
    date. ATM is the closest *listed* strike; ties resolve to the lower strike.
    """
    symbol = str(underlying).strip().upper()
    if symbol not in _LOCKED_UNIVERSE:
        raise ValueError(f"underlying outside locked universe: {symbol}")
    price = float(spot_price)
    if price <= 0:
        raise ValueError("spot_price must be positive")
    target_expiry = _parse_expiry(expiry)
    radius = int(_LOCKED_UNIVERSE[symbol]["radius"])

    rows: list[dict[str, Any]] = []
    for row in instruments:
        if str(row.get("exch_seg", "")).strip().upper() != "NFO":
            continue
        if str(row.get("name", "")).strip().upper() != symbol:
            continue
        if str(row.get("instrumenttype", "")).strip().upper() != "OPTIDX":
            continue
        try:
            row_expiry = _parse_expiry(row.get("expiry"))
            strike = _strike(row.get("strike"))
        except (TypeError, ValueError):
            continue
        if row_expiry != target_expiry:
            continue
        suffix = str(row.get("symbol", "")).strip().upper()[-2:]
        if suffix not in {"CE", "PE"}:
            continue
        token = str(row.get("token", "")).strip()
        trading_symbol = str(row.get("symbol", "")).strip()
        if not token or not trading_symbol:
            continue
        try:
            lot_size = int(float(row.get("lotsize", 0))) or None
        except (TypeError, ValueError):
            lot_size = None
        rows.append({
            "strike": strike,
            "option_type": suffix,
            "token": token,
            "trading_symbol": trading_symbol,
            "lot_size": lot_size,
        })

    strikes = sorted({row["strike"] for row in rows})
    if len(strikes) < radius * 2 + 1:
        raise LookupError(
            f"{symbol} {target_expiry}: insufficient listed strikes for ATM +/-{radius}"
        )
    atm_index = min(range(len(strikes)), key=lambda index: (abs(strikes[index] - price), strikes[index]))
    start, stop = atm_index - radius, atm_index + radius + 1
    if start < 0 or stop > len(strikes):
        raise LookupError(f"{symbol} {target_expiry}: complete symmetric ATM window unavailable")
    selected_strikes = strikes[start:stop]
    if len(selected_strikes) != radius * 2 + 1:
        raise LookupError(f"{symbol} {target_expiry}: strike window is incomplete")

    by_pair: dict[tuple[float, str], dict[str, Any]] = {}
    for row in rows:
        pair = (row["strike"], row["option_type"])
        if row["strike"] not in selected_strikes:
            continue
        if pair in by_pair:
            raise LookupError(f"duplicate option contract for {symbol} {pair}")
        by_pair[pair] = row

    contracts: list[IndexOptionContract] = []
    for strike in selected_strikes:
        for suffix in ("CE", "PE"):
            row = by_pair.get((strike, suffix))
            if row is None:
                raise LookupError(f"missing {suffix} contract for {symbol} strike {strike}")
            contracts.append(IndexOptionContract(
                symbol=row["trading_symbol"],
                underlying=symbol,
                expiry=target_expiry.isoformat(),
                strike=strike,
                option_type=suffix,
                token=row["token"],
                trading_symbol=row["trading_symbol"],
                lot_size=row["lot_size"],
            ))
    expected = (radius * 2 + 1) * 2
    if len(contracts) != expected or len({c.token for c in contracts}) != expected:
        raise LookupError(f"{symbol} {target_expiry}: contract count/token validation failed")
    return IndexOptionSelection(
        underlying=symbol,
        expiry=target_expiry.isoformat(),
        atm_strike=strikes[atm_index],
        radius=radius,
        contracts=tuple(contracts),
    )


__all__ = [
    "IndexOptionContract",
    "IndexOptionSelection",
    "select_locked_index_options",
]

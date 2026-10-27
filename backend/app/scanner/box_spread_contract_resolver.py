"""Exact four-contract resolution for Box Spread paper trading.

This module is deliberately standalone: it does not start feeds, place orders,
write snapshots, or call the live Box Spread scanner. Contract identity comes
only from concrete SyntheticSubscription records selected by the existing
Box Spread subscription builder; token/symbol values are never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.market_data.live_synthetic_stream import SyntheticSubscription


BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
_EXCHANGE_TYPE_BY_SEGMENT = {"NFO": 2, "BFO": 4}


class BoxSpreadContractResolutionError(ValueError):
    """The requested Box Spread does not map to one exact, valid contract set."""


@dataclass(frozen=True)
class BoxSpreadLegContract:
    """An exact exchange/token/symbol identity for one option leg."""

    role: str
    exchange_type: int
    token: str
    symbol: str
    underlying: str
    instrument_class: str
    expiry: str
    option_type: str
    strike: float
    lot_size: int


@dataclass(frozen=True)
class ResolvedBoxSpreadContracts:
    """Four uniquely resolved option contracts for one Box Spread."""

    underlying: str
    instrument_class: str
    expiry: str
    low_strike: float
    high_strike: float
    direction: str
    exchange_segment: str
    lot_size: int
    low_ce: BoxSpreadLegContract
    high_ce: BoxSpreadLegContract
    high_pe: BoxSpreadLegContract
    low_pe: BoxSpreadLegContract

    @property
    def legs(self) -> tuple[BoxSpreadLegContract, ...]:
        """Stable four-leg order, independent of LONG/SHORT execution actions."""
        return (self.low_ce, self.high_ce, self.high_pe, self.low_pe)


def _normalise_expiry(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise BoxSpreadContractResolutionError(f"invalid contract expiry: {value!r}")


def _normalise_strike(value: object, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BoxSpreadContractResolutionError(f"{field} must be a valid strike") from None
    if not result.is_finite() or result <= 0:
        raise BoxSpreadContractResolutionError(f"{field} must be a positive finite strike")
    return result.normalize()


def _positive_lot_size(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise BoxSpreadContractResolutionError(f"{field} must be a positive integer")
    try:
        number = int(value)  # SyntheticSubscription lot_size is already an integer.
    except (TypeError, ValueError, OverflowError):
        raise BoxSpreadContractResolutionError(f"{field} must be a positive integer") from None
    if number <= 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise BoxSpreadContractResolutionError(f"{field} must be a positive integer")
    return number


def resolve_box_spread_contracts(
    *,
    subscriptions: Iterable[SyntheticSubscription],
    underlying: str,
    instrument_class: str,
    expiry: str | date | datetime,
    low_strike: float,
    high_strike: float,
    direction: str,
    expected_lot_size: int | None = None,
) -> ResolvedBoxSpreadContracts:
    """Resolve a Box Spread to its exact four subscribed option contracts.

    The subscription list is the contract authority. This function intentionally
    rejects ambiguous/malformed mappings rather than deriving a token or
    tradingsymbol from a human-readable opportunity.
    """
    symbol = str(underlying or "").strip().upper()
    cls = str(instrument_class or "").strip().upper()
    side = str(direction or "").strip().upper()
    if not symbol:
        raise BoxSpreadContractResolutionError("underlying is required")
    if cls not in {"STOCK", "INDEX"}:
        raise BoxSpreadContractResolutionError("instrument_class must be STOCK or INDEX")
    if side not in {"LONG", "SHORT"}:
        raise BoxSpreadContractResolutionError("direction must be LONG or SHORT")

    expiry_iso = _normalise_expiry(expiry)
    low = _normalise_strike(low_strike, field="low_strike")
    high = _normalise_strike(high_strike, field="high_strike")
    if low >= high:
        raise BoxSpreadContractResolutionError("low_strike must be below high_strike")

    segment = "BFO" if cls == "INDEX" and symbol in BSE_INDEX_SYMBOLS else "NFO"
    expected_exchange_type = _EXCHANGE_TYPE_BY_SEGMENT[segment]
    required = {
        ("CE", low): "LOW_CE",
        ("CE", high): "HIGH_CE",
        ("PE", high): "HIGH_PE",
        ("PE", low): "LOW_PE",
    }

    # First isolate the requested chain/expiry/strikes. Unrelated subscriptions
    # (including other underlyings and expiries) are allowed in a shared registry.
    matches: dict[tuple[str, Decimal], list[SyntheticSubscription]] = {
        key: [] for key in required
    }
    for item in subscriptions:
        item_underlying = str(item.underlying or "").strip().upper()
        item_class = str(item.instrument_class or "").strip().upper()
        if item_underlying != symbol or item_class != cls:
            continue
        option_type = str(item.option_type or "").strip().upper()
        if option_type not in {"CE", "PE"} or item.strike is None:
            continue
        # A shared subscription registry can contain stale or partially formed
        # entries. An unreadable expiry cannot be this exact requested contract;
        # ignore it here so it cannot break unrelated scanner/paper resolutions.
        try:
            item_expiry = _normalise_expiry(item.expiry)
        except BoxSpreadContractResolutionError:
            continue
        if item_expiry != expiry_iso:
            continue
        item_strike = _normalise_strike(item.strike, field="subscription strike")
        key = (option_type, item_strike)
        if key in matches:
            matches[key].append(item)

    resolved: dict[str, BoxSpreadLegContract] = {}
    identities: set[tuple[int, str]] = set()
    symbols: set[str] = set()
    lot_sizes: set[int] = set()

    for key, role in required.items():
        candidates = matches[key]
        if not candidates:
            option_type, strike = key
            raise BoxSpreadContractResolutionError(
                f"missing exact contract for {role}: {symbol} {expiry_iso} "
                f"{strike} {option_type}"
            )
        if len(candidates) != 1:
            raise BoxSpreadContractResolutionError(
                f"ambiguous duplicate subscriptions for {role}"
            )
        item = candidates[0]
        try:
            exchange_type = int(item.exchange_type)
        except (TypeError, ValueError, OverflowError):
            raise BoxSpreadContractResolutionError(
                f"{role} has invalid exchange type"
            ) from None
        if exchange_type != expected_exchange_type:
            raise BoxSpreadContractResolutionError(
                f"{role} exchange mismatch: expected {segment} ({expected_exchange_type}), "
                f"got {exchange_type}"
            )

        token = str(item.token or "").strip()
        tradingsymbol = str(item.symbol or "").strip()
        if not token:
            raise BoxSpreadContractResolutionError(f"{role} has no exact broker token")
        if not tradingsymbol:
            raise BoxSpreadContractResolutionError(f"{role} has no exact tradingsymbol")
        identity = (exchange_type, token)
        if identity in identities:
            raise BoxSpreadContractResolutionError(
                f"duplicate broker contract identity detected at {role}"
            )
        if tradingsymbol.upper() in symbols:
            raise BoxSpreadContractResolutionError(
                f"duplicate tradingsymbol detected at {role}"
            )
        if not tradingsymbol.upper().endswith(key[0]):
            raise BoxSpreadContractResolutionError(
                f"{role} tradingsymbol does not match option type {key[0]}"
            )
        identities.add(identity)
        symbols.add(tradingsymbol.upper())

        lot_size = _positive_lot_size(item.lot_size, field=f"{role} lot_size")
        lot_sizes.add(lot_size)
        resolved[role] = BoxSpreadLegContract(
            role=role,
            exchange_type=exchange_type,
            token=token,
            symbol=tradingsymbol,
            underlying=symbol,
            instrument_class=cls,
            expiry=expiry_iso,
            option_type=key[0],
            strike=float(key[1]),
            lot_size=lot_size,
        )

    if len(lot_sizes) != 1:
        raise BoxSpreadContractResolutionError(
            "all four option contracts must have the same lot size"
        )
    lot_size = next(iter(lot_sizes))
    if expected_lot_size is not None:
        expected_lot = _positive_lot_size(expected_lot_size, field="expected_lot_size")
        if lot_size != expected_lot:
            raise BoxSpreadContractResolutionError(
                f"opportunity lot size mismatch: expected {expected_lot}, contracts use {lot_size}"
            )

    return ResolvedBoxSpreadContracts(
        underlying=symbol,
        instrument_class=cls,
        expiry=expiry_iso,
        low_strike=float(low),
        high_strike=float(high),
        direction=side,
        exchange_segment=segment,
        lot_size=lot_size,
        low_ce=resolved["LOW_CE"],
        high_ce=resolved["HIGH_CE"],
        high_pe=resolved["HIGH_PE"],
        low_pe=resolved["LOW_PE"],
    )


__all__ = [
    "BoxSpreadContractResolutionError",
    "BoxSpreadLegContract",
    "ResolvedBoxSpreadContracts",
    "resolve_box_spread_contracts",
]

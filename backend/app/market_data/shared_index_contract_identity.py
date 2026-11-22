"""Strict broker-neutral contract identity and descriptor matching.

No token cross-broker assumptions: tokens are intentionally excluded from canonical
identity. Ambiguous or incomplete contracts fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from .registry import InstrumentDescriptor

_LOCKED = frozenset({"NIFTY", "BANKNIFTY"})
_EXPIRY_FORMATS = ("%Y-%m-%d", "%d%b%Y", "%d-%b-%Y", "%d%b%y", "%d-%b-%y")


def _expiry_date(value: str | None) -> date:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("expiry is required for contract mapping")
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported expiry format for strict mapping: {text!r}")


@dataclass(frozen=True, order=True)
class SharedIndexContractIdentity:
    exchange: str
    segment: str
    underlying: str
    instrument_type: str
    expiry: date | None = None
    strike: float | None = None
    option_type: str | None = None

    def __post_init__(self):
        exchange = str(self.exchange).strip().upper()
        segment = str(self.segment).strip().upper()
        if not exchange or not segment:
            raise ValueError("exchange and segment are required")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "segment", segment)
        underlying = str(self.underlying).strip().upper()
        kind = str(self.instrument_type).strip().lower()
        if underlying not in _LOCKED:
            raise ValueError("identity is outside locked index universe")
        if kind not in {"index", "future", "option"}:
            raise ValueError("unsupported instrument type for shared-index identity")
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "instrument_type", kind)
        if kind == "index":
            if any(v is not None for v in (self.expiry, self.strike, self.option_type)):
                raise ValueError("index identity cannot include option/future fields")
        elif kind == "future":
            if self.expiry is None or self.strike is not None or self.option_type is not None:
                raise ValueError("future identity requires expiry only")
        else:
            if self.expiry is None or self.strike is None or self.option_type not in {"CE", "PE"}:
                raise ValueError("option identity requires expiry, strike and CE/PE")
            if isinstance(self.strike, bool) or not isinstance(self.strike, (int, float)) or self.strike <= 0:
                raise ValueError("option strike must be positive and numeric")


def canonical_identity(descriptor: InstrumentDescriptor) -> SharedIndexContractIdentity:
    """Build canonical identity from explicit descriptor fields, never token/symbol heuristics."""
    if not isinstance(descriptor, InstrumentDescriptor):
        raise TypeError("descriptor must be InstrumentDescriptor")
    underlying = str(descriptor.underlying or "").strip().upper()
    kind = str(descriptor.instrument_type or "").strip().lower()
    if underlying not in _LOCKED:
        raise ValueError("descriptor underlying is missing or unlocked")
    if kind == "index":
        return SharedIndexContractIdentity(descriptor.exchange, descriptor.segment, underlying, kind)
    if kind in {"future", "option"}:
        expiry = _expiry_date(descriptor.expiry)
        if kind == "future":
            return SharedIndexContractIdentity(descriptor.exchange, descriptor.segment, underlying, kind, expiry=expiry)
        right = str(descriptor.option_type or "").strip().upper()
        strike = descriptor.strike
        if isinstance(strike, bool) or not isinstance(strike, (int, float)) or strike <= 0:
            raise ValueError("option strike is missing or invalid")
        return SharedIndexContractIdentity(descriptor.exchange, descriptor.segment, underlying, kind, expiry=expiry, strike=float(strike), option_type=right)
    raise ValueError("unsupported instrument type")


def match_contracts(primary: Iterable[InstrumentDescriptor], secondary: Iterable[InstrumentDescriptor]) -> dict[SharedIndexContractIdentity, tuple[InstrumentDescriptor, InstrumentDescriptor]]:
    """Pair exact canonical contracts; reject duplicates and skip unmatched contracts."""
    def indexed(items):
        out = {}
        for descriptor in items:
            identity = canonical_identity(descriptor)
            if identity in out:
                raise ValueError(f"duplicate canonical contract identity: {identity}")
            out[identity] = descriptor
        return out
    left, right = indexed(primary), indexed(secondary)
    return {identity: (left[identity], right[identity]) for identity in left.keys() & right.keys()}

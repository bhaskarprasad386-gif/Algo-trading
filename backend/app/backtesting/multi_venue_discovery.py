"""Phase 9 shared multi-venue instrument normalization.

The same discovery contract is used for NSE, BSE and commodity venues;
strategy code remains venue-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SUPPORTED_SEGMENTS = frozenset({"NFO", "BFO", "MCX"})
VENUE_BY_SEGMENT = {"NFO": "NSE", "BFO": "BSE", "MCX": "MCX"}
INSTRUMENT_CLASS_BY_TYPE = {
    "OPTSTK": "STOCK",
    "OPTIDX": "INDEX",
    "FUTSTK": "STOCK",
    "FUTIDX": "INDEX",
    "OPTFUT": "COMMODITY",
    "FUTCOM": "COMMODITY",
    "OPTCOM": "COMMODITY",
}


@dataclass(frozen=True)
class VenueInstrument:
    token: str
    venue: str
    segment: str
    underlying: str
    instrument_class: str
    expiry: str
    lot_size: int
    symbol: str

    def __post_init__(self) -> None:
        if not self.token or not self.underlying or not self.symbol:
            raise ValueError("token, underlying and symbol are required")
        if self.segment not in SUPPORTED_SEGMENTS:
            raise ValueError("unsupported segment")
        if self.venue != VENUE_BY_SEGMENT[self.segment]:
            raise ValueError("venue/segment mismatch")
        if self.instrument_class not in {"STOCK", "INDEX", "COMMODITY"}:
            raise ValueError("unsupported instrument class")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


class MultiVenueInstrumentDiscovery:
    """Normalize concrete provider rows without creating strategy-specific stores."""

    @staticmethod
    def discover(rows: Iterable[Mapping[str, Any]], *, segments: Iterable[str] = SUPPORTED_SEGMENTS) -> tuple[VenueInstrument, ...]:
        allowed = {str(s).strip().upper() for s in segments}
        result: list[VenueInstrument] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            segment = str(row.get("exch_seg", "")).strip().upper()
            typ = str(row.get("instrumenttype", "")).strip().upper()
            if segment not in allowed or typ not in INSTRUMENT_CLASS_BY_TYPE:
                continue
            token = str(row.get("token", "")).strip()
            symbol = str(row.get("symbol", "")).strip()
            underlying = str(row.get("name", "")).strip().upper()
            expiry = str(row.get("expiry", "")).strip()
            try:
                lot_size = int(str(row.get("lotsize", "0")).strip())
            except ValueError:
                continue
            identity = (segment, token)
            if not token or not symbol or not underlying or not expiry or lot_size <= 0:
                continue
            if identity in seen:
                raise ValueError(f"duplicate instrument identity: {identity}")
            seen.add(identity)
            result.append(
                VenueInstrument(
                    token=token,
                    venue=VENUE_BY_SEGMENT[segment],
                    segment=segment,
                    underlying=underlying,
                    instrument_class=INSTRUMENT_CLASS_BY_TYPE[typ],
                    expiry=expiry,
                    lot_size=lot_size,
                    symbol=symbol,
                )
            )
        return tuple(sorted(result, key=lambda x: (x.venue, x.instrument_class, x.underlying, x.expiry, x.token)))

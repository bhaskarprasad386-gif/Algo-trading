"""Resolve NSE cash-market tokens from an Angel One instrument master."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class HistoricalCashSelection:
    """Provider cash instrument selected for an underlying symbol."""

    underlying: str
    token: str
    symbol: str
    exchange: str = "NSE"

    @property
    def instrument(self) -> str:
        return f"{self.exchange}:{self.token}:{self.symbol}"


class HistoricalCashTokenResolver:
    """Resolve an NSE equity token without confusing it with an NFO contract."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self._by_underlying: dict[str, HistoricalCashSelection] = {}
        for row in rows:
            if str(row.get("exch_seg", "")).strip().upper() != "NSE":
                continue
            symbol = str(row.get("symbol", "")).strip().upper()
            underlying = str(row.get("name", "")).strip().upper()
            token = str(row.get("token", "")).strip()
            instrument_type = str(row.get("instrumenttype", "")).strip().upper()
            if not symbol.endswith("-EQ") or not underlying or not token:
                continue
            if instrument_type not in {"", "EQ"}:
                continue
            selection = HistoricalCashSelection(underlying, token, symbol)
            existing = self._by_underlying.get(underlying)
            if existing is None:
                self._by_underlying[underlying] = selection
                continue
            if existing.token != token or existing.symbol != symbol:
                raise ValueError(f"ambiguous NSE cash token for {underlying}")

    def resolve(self, underlying: str) -> HistoricalCashSelection:
        key = underlying.strip().upper()
        if not key:
            raise ValueError("underlying is required")
        try:
            return self._by_underlying[key]
        except KeyError:
            raise LookupError(f"no NSE cash equity token for {key}") from None

    def resolve_token(self, underlying: str) -> str:
        return self.resolve(underlying).token

"""Strict, offline parsers for Angel One JSON and Shoonya scrip-master rows.

Parsers never fetch credentials or network data. They return accepted mappings
and row-level quarantine reasons; callers must enforce a rejection threshold
before publishing a complete snapshot.
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .provider_contracts import (
    BrokerContractToken, CanonicalContractKey, ContractMapping,
)


@dataclass(frozen=True)
class QuarantinedMasterRow:
    row_number: int
    reason: str


@dataclass(frozen=True)
class MasterParseReport:
    broker: str
    source_sha256: str
    mappings: tuple[ContractMapping, ...]
    quarantined: tuple[QuarantinedMasterRow, ...]
    total_rows: int

    @property
    def accepted_rows(self) -> int:
        return len(self.mappings)

    @property
    def rejected_rows(self) -> int:
        return len(self.quarantined)


def _value(row: Mapping[str, Any], *names: str) -> Any:
    normalized = {str(key).strip().lower().replace("_", "").replace(" ", ""): value
                  for key, value in row.items()}
    for name in names:
        key = name.strip().lower().replace("_", "").replace(" ", "")
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return None


def _required(row: Mapping[str, Any], *names: str) -> str:
    value = _value(row, *names)
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"missing required field: {names[0]}")
    return result


def _expiry(value: Any) -> date:
    raw = str(value or "").strip()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.upper(), fmt).date()
        except ValueError:
            continue
    raise ValueError("invalid or missing expiry")


def _decimal(value: Any, name: str, *, scale: Decimal = Decimal("1")) -> Decimal:
    try:
        number = Decimal(str(value).strip()) / scale
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"invalid {name}")
    return number.normalize()


def _positive_int(value: Any, name: str) -> int:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError(f"invalid {name}")
    return int(number)


def _instrument_type(raw: str) -> tuple[str, str | None]:
    value = raw.strip().upper()
    if value in {"OPTIDX", "OPTSTK", "OPTION", "OPT"}:
        return "OPTION", value
    if value in {"FUTIDX", "FUTSTK", "FUTURE", "FUT"}:
        return "FUTURE", value
    if value in {"EQ", "EQUITY", "CASH"}:
        return "EQUITY", value
    if value in {"INDEX", "INDICES"}:
        return "INDEX", value
    if value in {"COM", "COMMODITY"}:
        return "COMMODITY", value
    raise ValueError(f"unsupported instrument type: {raw}")


def _mapping(
    row: Mapping[str, Any], *, broker: str, strike_scale: Decimal,
    angel: bool,
) -> ContractMapping:
    exchange = _required(row, "exch_seg", "exchange", "exch").upper()
    segment = _required(row, "segment", "exch_seg", "exchange", "exch").upper()
    token = _required(row, "token", "tk")
    symbol = _required(row, "tradingsymbol", "trading_symbol", "tsym", "symbol")
    raw_type = _required(row, "instrumenttype", "instrument", "instname", "instrument_type")
    kind, provider_type = _instrument_type(raw_type)
    underlying = str(_value(row, "name", "underlying", "root", "dname", "symbol") or "").strip().upper()
    if not underlying:
        # A conservative fallback for cash/index instruments only. Derivatives
        # require explicit underlying/root metadata to avoid symbol guessing.
        if kind in {"EQUITY", "INDEX"}:
            underlying = symbol.upper()
        else:
            raise ValueError("missing underlying/root metadata")

    expiry = None
    strike = None
    option_type = None
    if kind in {"OPTION", "FUTURE"}:
        expiry = _expiry(_value(row, "expiry", "expdate"))
    if kind == "OPTION":
        raw_side = str(_value(row, "opttype", "optiontype", "option_type") or "").strip().upper()
        if raw_side not in {"CE", "PE"}:
            raise ValueError("invalid option right")
        option_type = raw_side
        strike = _decimal(_value(row, "strike", "strikeprice", "strike_price"), "strike", scale=strike_scale)

    lot_raw = _value(row, "lotsize", "lot_size", "ls")
    lot_size = _positive_int(lot_raw, "lot_size")
    tick_raw = _value(row, "tick_size", "ticksize", "ti")
    tick_size = _decimal(tick_raw, "tick_size", scale=tick_scale)
    key = CanonicalContractKey(
        exchange=exchange, segment=segment, underlying=underlying,
        instrument_type=kind, expiry=expiry, strike=strike,
        option_type=option_type,
    )
    provider = BrokerContractToken(broker, exchange, segment, token)
    status = str(_value(row, "status", "tradable", "is_tradable") or "Y").strip().upper()
    tradable = status not in {"N", "NO", "FALSE", "INACTIVE", "DISABLED", "0"}
    return ContractMapping(
        key=key, provider=provider, trading_symbol=symbol,
        lot_size=lot_size, tick_size=tick_size, tradable=tradable,
    )


def _parse_rows(
    rows: Iterable[Mapping[str, Any]], *, broker: str,
    raw_bytes: bytes, strike_scale: Decimal,
) -> MasterParseReport:
    accepted: list[ContractMapping] = []
    rejected: list[QuarantinedMasterRow] = []
    seen_tokens: set[BrokerContractToken] = set()
    for index, row in enumerate(rows, start=1):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("row is not a mapping")
            item = _mapping(row, broker=broker, strike_scale=strike_scale, tick_scale=tick_scale)
            if item.provider in seen_tokens:
                raise ValueError("duplicate broker token in source master")
            seen_tokens.add(item.provider)
            accepted.append(item)
        except (TypeError, ValueError) as exc:
            rejected.append(QuarantinedMasterRow(index, str(exc)))
    return MasterParseReport(
        broker=broker, source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        mappings=tuple(accepted), quarantined=tuple(rejected),
        total_rows=len(accepted) + len(rejected),
    )


class AngelOneMasterParser:
    """Parse Angel One OpenAPIScripMaster JSON rows; strike is paise-like /100."""

    @staticmethod
    def parse(payload: bytes | str | Iterable[Mapping[str, Any]]) -> MasterParseReport:
        if isinstance(payload, bytes):
            raw = payload
            import json
            rows = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
            import json
            rows = json.loads(payload)
        else:
            rows = tuple(payload)
            raw = repr(rows).encode("utf-8")
        if not isinstance(rows, (list, tuple)):
            raise ValueError("Angel One master must be a JSON array")
        return _parse_rows(rows, broker="ANGELONE", raw_bytes=raw, strike_scale=Decimal("100"), tick_scale=Decimal("100"))


class ShoonyaMasterParser:
    """Parse Shoonya pipe-delimited or CSV master with explicit strike scaling."""

    @staticmethod
    def parse(
        payload: bytes | str,
        *,
        delimiter: str = ",",
        strike_scale: Decimal = Decimal("1"),\n        tick_scale: Decimal = Decimal("1"),
    ) -> MasterParseReport:
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        text = raw.decode("utf-8-sig")
        if delimiter not in {",", "|", "\t"}:
            raise ValueError("delimiter must be comma, pipe or tab")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("Shoonya master is missing a header row")
        rows = tuple(reader)
        return _parse_rows(rows, broker="SHOONYA", raw_bytes=raw, strike_scale=Decimal(str(strike_scale)), tick_scale=Decimal(str(tick_scale)))

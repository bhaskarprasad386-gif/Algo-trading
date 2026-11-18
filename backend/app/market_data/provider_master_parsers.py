"""Strict offline parsers and guarded publisher for broker instrument masters.

No network calls, broker login, subscriptions or scanner wiring live here.
Provider strike/tick scales are explicit to avoid magnitude-based guessing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .provider_contracts import (
    BrokerContractToken, CanonicalContractKey, ContractMapping, ProviderContractRegistry,
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

    @property
    def rejection_ratio(self) -> float:
        return self.rejected_rows / self.total_rows if self.total_rows else 1.0


def _norm(name: object) -> str:
    return str(name).strip().lower().replace("_", "").replace(" ", "")


def _value(row: Mapping[str, Any], *names: str) -> Any:
    normalized = {_norm(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_norm(name))
        if value not in (None, ""):
            return value
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
            pass
    raise ValueError("invalid or missing expiry")


def _decimal(value: Any, name: str, scale: Decimal) -> Decimal:
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


def _instrument_type(raw: str) -> str:
    value = raw.strip().upper()
    if value in {"OPTIDX", "OPTSTK", "OPTION", "OPT"}:
        return "OPTION"
    if value in {"FUTIDX", "FUTSTK", "FUTURE", "FUT"}:
        return "FUTURE"
    if value in {"EQ", "EQUITY", "CASH"}:
        return "EQUITY"
    if value in {"INDEX", "INDICES"}:
        return "INDEX"
    if value in {"COM", "COMMODITY"}:
        return "COMMODITY"
    raise ValueError(f"unsupported instrument type: {raw}")


def _mapping(
    row: Mapping[str, Any], *, broker: str,
    strike_scale: Decimal, tick_scale: Decimal,
) -> ContractMapping:
    exchange = _required(row, "exch_seg", "exchange", "exch").upper()
    segment = str(_value(row, "segment", "exch_seg", "exchange", "exch") or "").strip().upper()
    if not segment:
        raise ValueError("missing segment")
    token = _required(row, "token", "tk")
    symbol = _required(row, "tradingsymbol", "trading_symbol", "tsym", "symbol")
    kind = _instrument_type(_required(row, "instrumenttype", "instrument", "instname", "instrument_type"))
    underlying_value = _value(row, "name", "underlying", "root", "dname")
    if underlying_value in (None, ""):
        underlying_value = _value(row, "symbol")
        # Shoonya's BFO master uses Symbol aliases such as SENSEXOPT and
        # BANKEXFUT, whereas NFO uses the bare underlying (for example NIFTY).
        # Strip only the instrument suffix for derivative rows; never infer
        # the root from TradingSymbol because that also contains expiry/strike.
        raw_underlying = str(underlying_value or "").strip().upper()
        if broker.upper() == "SHOONYA" and exchange == "BFO" and kind in {"OPTION", "FUTURE"}:
            # Shoonya BFO's Symbol column can be a short exchange alias (BKX,
            # SX50). The TradingSymbol carries the actual contract root and
            # expiry/strike suffix, so derive the root from that exact shape.
            raw_trading_symbol = symbol.strip().upper()
            option_match = re.match(
                r"^(.+?)\d{1,2}[A-Z]{3}\d{2,4}\d+(?:\.\d+)?(?:CE|PE)$",
                raw_trading_symbol,
            )
            future_match = re.match(
                r"^(.+?)\d{1,2}[A-Z]{3}\d{2,4}FUT$",
                raw_trading_symbol,
            )
            match = option_match or future_match
            if match:
                raw_underlying = match.group(1)
            else:
                for suffix in ("OPT", "FUT"):
                    if raw_underlying.endswith(suffix):
                        raw_underlying = raw_underlying[:-len(suffix)]
                        break
            underlying_value = raw_underlying
        elif broker.upper() == "ANGELONE" and exchange == "BFO" and kind in {"OPTION", "FUTURE"}:
            # Angel BFO rows may omit the separate root/name field and carry
            # the full contract in symbol. Recover only the well-formed
            # expiry/strike/right suffix; do not guess from arbitrary symbols.
            option_match = re.match(
                r"^(.+?)\d{1,2}[A-Z]{3}\d{2,4}\d+(?:\.\d+)?(?:CE|PE)$",
                raw_underlying,
            )
            future_match = re.match(
                r"^(.+?)\d{1,2}[A-Z]{3}\d{2,4}FUT$",
                raw_underlying,
            )
            match = option_match or future_match
            if match:
                raw_underlying = match.group(1)
            underlying_value = raw_underlying
    underlying = str(underlying_value or "").strip().upper()
    if not underlying:
        if kind in {"EQUITY", "INDEX"}:
            underlying = symbol.upper()
        else:
            raise ValueError("missing underlying/root metadata")

    expiry = _expiry(_value(row, "expiry", "expdate")) if kind in {"OPTION", "FUTURE"} else None
    strike = None
    option_type = None
    if kind == "OPTION":
        side = str(_value(row, "opttype", "optiontype", "option_type") or "").strip().upper()
        if not side:
            side = "CE" if symbol.upper().endswith("CE") else "PE" if symbol.upper().endswith("PE") else ""
        if side not in {"CE", "PE"}:
            raise ValueError("invalid option right")
        option_type = side
        strike = _decimal(_value(row, "strike", "strikeprice", "strike_price"), "strike", strike_scale)

    lot_size = _positive_int(_value(row, "lotsize", "lot_size", "ls"), "lot_size")
    tick_size = _decimal(_value(row, "tick_size", "ticksize", "ti"), "tick_size", tick_scale)
    key = CanonicalContractKey(
        exchange=exchange, segment=segment, underlying=underlying,
        instrument_type=kind, expiry=expiry, strike=strike, option_type=option_type,
    )
    provider = BrokerContractToken(broker, exchange, segment, token)
    status = str(_value(row, "status", "tradable", "is_tradable") or "Y").strip().upper()
    tradable = status not in {"N", "NO", "FALSE", "INACTIVE", "DISABLED", "0"}
    return ContractMapping(key, provider, symbol, lot_size, tick_size, tradable)


def _parse_rows(
    rows: Iterable[Mapping[str, Any]], *, broker: str, raw_bytes: bytes,
    strike_scale: Decimal, tick_scale: Decimal,
) -> MasterParseReport:
    accepted: list[ContractMapping] = []
    rejected: list[QuarantinedMasterRow] = []
    seen: set[BrokerContractToken] = set()
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("row is not a mapping")
            mapping = _mapping(row, broker=broker, strike_scale=strike_scale, tick_scale=tick_scale)
            if mapping.provider in seen:
                raise ValueError("duplicate broker token in source master")
            seen.add(mapping.provider)
            accepted.append(mapping)
        except (TypeError, ValueError) as exc:
            rejected.append(QuarantinedMasterRow(index, str(exc)))
    return MasterParseReport(
        broker, hashlib.sha256(raw_bytes).hexdigest(), tuple(accepted),
        tuple(rejected), len(accepted) + len(rejected),
    )


class AngelOneMasterParser:
    """Angel OpenAPIScripMaster: option strike and tick_size are /100."""

    @staticmethod
    def parse(payload: bytes | str | Iterable[Mapping[str, Any]]) -> MasterParseReport:
        if isinstance(payload, bytes):
            raw = payload
            rows = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
            rows = json.loads(payload)
        else:
            rows = tuple(payload)
            raw = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
        if not isinstance(rows, (list, tuple)):
            raise ValueError("Angel One master must be a JSON array")
        return _parse_rows(
            rows, broker="ANGELONE", raw_bytes=raw,
            strike_scale=Decimal("100"), tick_scale=Decimal("100"),
        )


class ShoonyaMasterParser:
    """Shoonya CSV/pipe master; scales are explicit and never inferred."""

    @staticmethod
    def parse(
        payload: bytes | str, *, delimiter: str = ",",
        strike_scale: Decimal = Decimal("1"),
        tick_scale: Decimal = Decimal("1"),
    ) -> MasterParseReport:
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        text = raw.decode("utf-8-sig")
        if delimiter not in {",", "|", "\t"}:
            raise ValueError("delimiter must be comma, pipe or tab")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("Shoonya master is missing a header row")
        return _parse_rows(
            tuple(reader), broker="SHOONYA", raw_bytes=raw,
            strike_scale=Decimal(str(strike_scale)),
            tick_scale=Decimal(str(tick_scale)),
        )


def publish_dual_master(
    registry: ProviderContractRegistry,
    reports: Iterable[MasterParseReport],
    *,
    source_version: str,
    fetched_at: datetime,
    max_rejection_ratio: float = 0.02,
):
    """Publish both broker masters together or leave the previous snapshot intact."""
    reports = tuple(reports)
    if not 0 <= max_rejection_ratio < 1:
        raise ValueError("max_rejection_ratio must be in [0, 1)")
    by_broker = {report.broker: report for report in reports}
    if set(by_broker) != {"ANGELONE", "SHOONYA"} or len(reports) != 2:
        raise ValueError("exactly one Angel One and one Shoonya report are required")
    for broker, report in by_broker.items():
        if report.total_rows <= 0 or not report.mappings:
            raise ValueError(f"{broker} master is empty or has no accepted contracts")
        if report.rejection_ratio > max_rejection_ratio:
            raise ValueError(f"{broker} rejection ratio exceeds configured safety threshold")
    mappings = tuple(item for report in reports for item in report.mappings)
    digest_input = "|".join(
        f"{broker}:{by_broker[broker].source_sha256}"
        for broker in sorted(by_broker)
    ).encode()
    source_digest = hashlib.sha256(digest_input).hexdigest()
    return registry.replace_snapshot(
        mappings, source_version=source_version,
        fetched_at=fetched_at, source_sha256=source_digest,
    )

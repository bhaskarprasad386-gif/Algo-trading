"""Provider-neutral contract identity and atomic broker-token snapshots.

This registry is deliberately separate from live subscription ownership. It
maps the same exchange contract to independent Angel One and Shoonya tokens;
it does not change either broker's subscriptions or scanner selection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Iterable, Mapping


def _text(value: object, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _positive_decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be a positive finite number") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result.normalize()


@dataclass(frozen=True, order=True)
class CanonicalContractKey:
    """Contract identity independent of a broker's token and display symbol."""
    exchange: str
    segment: str
    underlying: str
    instrument_type: str
    expiry: date | None = None
    strike: Decimal | None = None
    option_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("exchange", "segment", "underlying", "instrument_type"):
            object.__setattr__(self, name, _text(getattr(self, name), name).upper())
        if self.expiry is not None and type(self.expiry) is not date:
            raise TypeError("expiry must be a date or None")
        if self.strike is not None:
            object.__setattr__(self, "strike", _positive_decimal(self.strike, "strike"))
        side = self.option_type.upper() if self.option_type else None
        if side not in (None, "CE", "PE"):
            raise ValueError("option_type must be CE, PE or None")
        object.__setattr__(self, "option_type", side)
        if self.instrument_type in {"OPTION", "OPTIDX", "OPTSTK"}:
            if self.expiry is None or self.strike is None or side is None:
                raise ValueError("options require expiry, strike and option_type")
        elif self.strike is not None or side is not None:
            raise ValueError("strike and option_type are only valid for options")


@dataclass(frozen=True)
class BrokerContractToken:
    broker: str
    exchange: str
    segment: str
    token: str

    def __post_init__(self) -> None:
        for name in ("broker", "exchange", "segment", "token"):
            object.__setattr__(self, name, _text(getattr(self, name), name).upper() if name in {"broker", "exchange", "segment"} else _text(self.token, name))


@dataclass(frozen=True)
class ContractMapping:
    key: CanonicalContractKey
    provider: BrokerContractToken
    trading_symbol: str
    lot_size: int
    tick_size: Decimal
    tradable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.key, CanonicalContractKey) or not isinstance(self.provider, BrokerContractToken):
            raise TypeError("key and provider must use canonical contract types")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")
        object.__setattr__(self, "tick_size", _positive_decimal(self.tick_size, "tick_size"))
        object.__setattr__(self, "trading_symbol", _text(self.trading_symbol, "trading_symbol"))
        if type(self.tradable) is not bool:
            raise TypeError("tradable must be bool")
        if self.provider.exchange != self.key.exchange or self.provider.segment != self.key.segment:
            raise ValueError("provider exchange/segment does not match canonical contract")


@dataclass(frozen=True)
class ContractMasterSnapshot:
    source_version: str
    source_sha256: str
    fetched_at: datetime
    mappings: tuple[ContractMapping, ...]


class ProviderContractRegistry:
    """Atomic last-known-good snapshot of canonical contracts and broker tokens."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot: ContractMasterSnapshot | None = None

    @staticmethod
    def _fingerprint(mappings: tuple[ContractMapping, ...]) -> str:
        rows = []
        for item in sorted(mappings, key=lambda m: (m.key, m.provider.broker, m.provider.token)):
            key = item.key
            rows.append({
                "exchange": key.exchange, "segment": key.segment,
                "underlying": key.underlying, "instrument_type": key.instrument_type,
                "expiry": key.expiry.isoformat() if key.expiry else None,
                "strike": str(key.strike) if key.strike is not None else None,
                "option_type": key.option_type, "broker": item.provider.broker,
                "token": item.provider.token, "symbol": item.trading_symbol,
                "lot_size": item.lot_size, "tick_size": str(item.tick_size),
                "tradable": item.tradable,
            })
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def replace_snapshot(
        self,
        mappings: Iterable[ContractMapping],
        *,
        source_version: str,
        fetched_at: datetime,
        source_sha256: str | None = None,
    ) -> ContractMasterSnapshot:
        """Validate a complete candidate before a single atomic pointer swap."""
        candidate = tuple(mappings)
        version = _text(source_version, "source_version")
        if not candidate:
            raise ValueError("contract master snapshot cannot be empty")
        if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        now = fetched_at.astimezone(timezone.utc)
        if now > datetime.now(timezone.utc):
            raise ValueError("fetched_at cannot be in the future")
        canonical_hash = self._fingerprint(candidate)
        digest = _text(source_sha256, "source_sha256").lower() if source_sha256 else canonical_hash
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("source_sha256 must be a 64-character hex digest")

        by_provider: dict[BrokerContractToken, CanonicalContractKey] = {}
        by_contract: dict[CanonicalContractKey, list[ContractMapping]] = {}
        for item in candidate:
            if not isinstance(item, ContractMapping):
                raise TypeError("all mappings must be ContractMapping instances")
            previous = by_provider.get(item.provider)
            if previous is not None and previous != item.key:
                raise ValueError("one broker token maps to multiple canonical contracts")
            by_provider[item.provider] = item.key
            by_contract.setdefault(item.key, []).append(item)

        for key, entries in by_contract.items():
            lot_sizes = {entry.lot_size for entry in entries}
            tick_sizes = {entry.tick_size for entry in entries}
            if len(lot_sizes) != 1 or len(tick_sizes) != 1:
                raise ValueError(f"broker lot/tick metadata mismatch for {key}")
            brokers = [entry.provider.broker for entry in entries]
            if len(brokers) != len(set(brokers)):
                raise ValueError(f"duplicate broker mapping for {key}")

        staged = ContractMasterSnapshot(version, digest, now, tuple(sorted(
            candidate, key=lambda m: (m.key, m.provider.broker, m.provider.token)
        )))
        with self._lock:
            current = self._snapshot
            if current is not None and now < current.fetched_at:
                raise ValueError("stale contract master cannot replace the active snapshot")
            self._snapshot = staged
            return staged

    def snapshot(self) -> ContractMasterSnapshot | None:
        with self._lock:
            return self._snapshot

    def resolve(self, key: CanonicalContractKey, broker: str, *, tradable_only: bool = True) -> ContractMapping:
        broker_name = _text(broker, "broker").upper()
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            raise LookupError("no validated contract master snapshot is active")
        matches = [
            item for item in snapshot.mappings
            if item.key == key and item.provider.broker == broker_name
            and (item.tradable or not tradable_only)
        ]
        if len(matches) != 1:
            raise LookupError(f"no unique {broker_name} mapping for canonical contract")
        return matches[0]

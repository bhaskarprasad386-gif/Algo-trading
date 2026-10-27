"""Isolated Box Spread paper-trading intent engine.

Phase 2 deliberately creates validated paper intents only. It has no broker
client, no WebSocket lifecycle, no database writes, and no live-scanner hooks.
Execution, fills, persistence, and recovery are added in later gated phases.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from app.scanner.box_spread_contract_resolver import ResolvedBoxSpreadContracts


PaperAction = Literal["BUY", "SELL"]


class BoxSpreadPaperEngineError(ValueError):
    """A paper intent cannot safely be created."""


@dataclass(frozen=True)
class BoxSpreadPaperLegIntent:
    role: str
    exchange_type: int
    token: str
    symbol: str
    action: PaperAction
    quantity: int


@dataclass(frozen=True)
class BoxSpreadPaperIntent:
    intent_id: str
    idempotency_key: str
    underlying: str
    instrument_class: str
    expiry: str
    low_strike: float
    high_strike: float
    direction: str
    lots: int
    lot_size: int
    quantity: int
    status: Literal["CREATED"] = "CREATED"
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    legs: tuple[BoxSpreadPaperLegIntent, ...] = ()


def _positive_integer(value: object, *, name: str) -> int:
    if isinstance(value, bool):
        raise BoxSpreadPaperEngineError(f"{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise BoxSpreadPaperEngineError(f"{name} must be a positive integer") from None
    if number <= 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise BoxSpreadPaperEngineError(f"{name} must be a positive integer")
    return number


def _leg_actions(direction: str) -> tuple[PaperAction, ...]:
    # Stable contract order: LOW_CE, HIGH_CE, HIGH_PE, LOW_PE.
    if direction == "LONG":
        return ("BUY", "SELL", "BUY", "SELL")
    if direction == "SHORT":
        return ("SELL", "BUY", "SELL", "BUY")
    raise BoxSpreadPaperEngineError("direction must be LONG or SHORT")


class BoxSpreadPaperEngine:
    """Thread-safe-per-event-loop in-memory intent registry for paper trades.

    Repeated callbacks with the same key and same request return the original
    intent. Reusing a key for a different contract set/size is rejected.
    The registry is intentionally process-local until Phase 5 persistence and
    Phase 6 restart recovery are implemented.
    """

    def __init__(self) -> None:
        self._intents: dict[str, BoxSpreadPaperIntent] = {}
        self._request_fingerprints: dict[str, tuple[object, ...]] = {}
        self._lock = asyncio.Lock()

    async def create_intent(
        self,
        *,
        contracts: ResolvedBoxSpreadContracts,
        lots: int,
        idempotency_key: str,
    ) -> BoxSpreadPaperIntent:
        key = str(idempotency_key or "").strip()
        if not key:
            raise BoxSpreadPaperEngineError("idempotency_key is required")
        if len(key) > 200:
            raise BoxSpreadPaperEngineError("idempotency_key must be at most 200 characters")

        lot_count = _positive_integer(lots, name="lots")
        quantity = contracts.lot_size * lot_count
        if quantity <= 0:
            raise BoxSpreadPaperEngineError("computed quantity must be positive")
        actions = _leg_actions(contracts.direction)
        fingerprint = (
            contracts.underlying, contracts.instrument_class, contracts.expiry,
            contracts.low_strike, contracts.high_strike, contracts.direction,
            contracts.lot_size,
            tuple((leg.role, leg.exchange_type, leg.token, leg.symbol) for leg in contracts.legs),
            lot_count,
        )

        async with self._lock:
            existing = self._intents.get(key)
            if existing is not None:
                if self._request_fingerprints[key] != fingerprint:
                    raise BoxSpreadPaperEngineError(
                        "idempotency_key was already used for a different paper request"
                    )
                return existing

            legs = tuple(
                BoxSpreadPaperLegIntent(
                    role=contract.role,
                    exchange_type=contract.exchange_type,
                    token=contract.token,
                    symbol=contract.symbol,
                    action=action,
                    quantity=quantity,
                )
                for contract, action in zip(contracts.legs, actions, strict=True)
            )
            intent = BoxSpreadPaperIntent(
                intent_id=key,
                idempotency_key=key,
                underlying=contracts.underlying,
                instrument_class=contracts.instrument_class,
                expiry=contracts.expiry,
                low_strike=contracts.low_strike,
                high_strike=contracts.high_strike,
                direction=contracts.direction,
                lots=lot_count,
                lot_size=contracts.lot_size,
                quantity=quantity,
                created_at=datetime.now(timezone.utc),
                legs=legs,
            )
            self._intents[key] = intent
            self._request_fingerprints[key] = fingerprint
            return intent

    async def get_intent(self, idempotency_key: str) -> BoxSpreadPaperIntent | None:
        async with self._lock:
            return self._intents.get(str(idempotency_key or "").strip())

    async def list_intents(self) -> tuple[BoxSpreadPaperIntent, ...]:
        async with self._lock:
            return tuple(sorted(self._intents.values(), key=lambda item: item.created_at))


__all__ = [
    "BoxSpreadPaperEngine",
    "BoxSpreadPaperEngineError",
    "BoxSpreadPaperIntent",
    "BoxSpreadPaperLegIntent",
]

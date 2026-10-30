"""Isolated Box Spread paper-trading intent engine.

Phase 2 deliberately creates validated paper intents only. It has no broker
client, no WebSocket lifecycle, no database writes, and no live-scanner hooks.
Execution, fills, persistence, and recovery are added in later gated phases.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.scanner.box_spread_contract_resolver import ResolvedBoxSpreadContracts


PaperAction = Literal["BUY", "SELL"]
PAPER_EXECUTION_LATENCY_SECONDS = 0.2
ADVERSE_SLIPPAGE_RATE = Decimal("0.0005")
_REQUIRED_LEG_ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")


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
class BoxSpreadPaperLegFill:
    role: str
    action: PaperAction
    quantity: int
    reference_price: Decimal
    slippage: Decimal
    fill_price: Decimal
    notional: Decimal


@dataclass(frozen=True)
class BoxSpreadPaperExecution:
    idempotency_key: str
    fills: tuple[BoxSpreadPaperLegFill, ...]
    latency_seconds: float
    slippage_rate: Decimal
    executed_at: datetime



@dataclass(frozen=True)
class BoxSpreadPaperFillEvent:
    idempotency_key: str
    fill_id: str
    role: str
    action: PaperAction
    quantity: int
    fill_price: Decimal
    notional: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class BoxSpreadPaperLegProgress:
    role: str
    action: PaperAction
    quantity: int
    filled_quantity: int
    remaining_quantity: int
    average_fill_price: Decimal | None
    status: str
    retry_count: int
    cancelled: bool


@dataclass(frozen=True)
class BoxSpreadPaperResidualExposure:
    role: str
    action: PaperAction
    quantity: int
    signed_quantity: int


@dataclass(frozen=True)
class BoxSpreadPaperFillProgress:
    idempotency_key: str
    status: str
    legs: tuple[BoxSpreadPaperLegProgress, ...]
    matched_quantity: int
    residual_exposure: tuple[BoxSpreadPaperResidualExposure, ...]


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

    execution_latency_seconds = PAPER_EXECUTION_LATENCY_SECONDS
    adverse_slippage_rate = ADVERSE_SLIPPAGE_RATE

    def __init__(self) -> None:
        self._intents: dict[str, BoxSpreadPaperIntent] = {}
        self._request_fingerprints: dict[str, tuple[object, ...]] = {}
        self._executions: dict[str, BoxSpreadPaperExecution] = {}
        self._execution_fingerprints: dict[str, tuple[tuple[str, Decimal], ...]] = {}
        self._leg_progress: dict[str, dict[str, dict[str, object]]] = {}
        self._fill_events: dict[tuple[str, str], BoxSpreadPaperFillEvent] = {}
        self._fill_identities: dict[tuple[str, str], tuple[str, int, Decimal]] = {}
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
            self._leg_progress[key] = {
                leg.role: {
                    "action": leg.action, "quantity": leg.quantity, "filled_quantity": 0,
                    "fill_notional": Decimal("0"), "status": "OPEN",
                    "retry_count": 0, "cancelled": False,
                }
                for leg in legs
            }
            return intent

    async def execute_intent(
        self,
        *,
        idempotency_key: str,
        reference_prices: dict[str, object],
        limit_prices: dict[str, object] | None = None,
    ) -> BoxSpreadPaperExecution:
        """Simulate a full four-leg fill with async latency and adverse slippage.

        This is a paper-only price simulation. It has no broker/order-client
        dependency and must be called by an async worker, never a feed callback.
        Prices are supplied as the observed reference price for each leg.
        """
        key = str(idempotency_key or "").strip()
        if not key:
            raise BoxSpreadPaperEngineError("idempotency_key is required")

        async with self._lock:
            intent = self._intents.get(key)
            if intent is None:
                raise BoxSpreadPaperEngineError("paper intent not found")
            existing = self._executions.get(key)
            if existing is None and any(fill_key == key for fill_key, _ in self._fill_events):
                raise BoxSpreadPaperEngineError("intent already has partial fill records")

        supplied_roles = set(reference_prices)
        if supplied_roles != set(_REQUIRED_LEG_ROLES):
            raise BoxSpreadPaperEngineError(
                "reference_prices must contain exactly the four required leg roles"
            )
        prices: dict[str, Decimal] = {}
        for role in _REQUIRED_LEG_ROLES:
            try:
                price = Decimal(str(reference_prices[role]))
            except (InvalidOperation, TypeError, ValueError):
                raise BoxSpreadPaperEngineError(f"{role} reference price must be positive") from None
            if not price.is_finite() or price <= 0:
                raise BoxSpreadPaperEngineError(f"{role} reference price must be positive")
            prices[role] = price
        limits: dict[str, Decimal] | None = None
        if limit_prices is not None:
            if set(limit_prices) != set(_REQUIRED_LEG_ROLES):
                raise BoxSpreadPaperEngineError("limit_prices must contain exactly the four required leg roles")
            limits = {}
            intent_actions = {leg.role: leg.action for leg in intent.legs}
            for role in _REQUIRED_LEG_ROLES:
                try:
                    limit = Decimal(str(limit_prices[role]))
                except (InvalidOperation, TypeError, ValueError):
                    raise BoxSpreadPaperEngineError(f"{role} limit price must be positive") from None
                if not limit.is_finite() or limit <= 0:
                    raise BoxSpreadPaperEngineError(f"{role} limit price must be positive")
                if intent_actions[role] == "BUY" and limit < prices[role]:
                    raise BoxSpreadPaperEngineError(f"{role} BUY limit is below the current ask")
                if intent_actions[role] == "SELL" and limit > prices[role]:
                    raise BoxSpreadPaperEngineError(f"{role} SELL limit is above the current bid")
                limits[role] = limit
        price_fingerprint = tuple(
            (role, prices[role], limits[role] if limits is not None else Decimal("0"))
            for role in _REQUIRED_LEG_ROLES
        )

        if existing is not None:
            if self._execution_fingerprints[key] != price_fingerprint:
                raise BoxSpreadPaperEngineError(
                    "paper intent already executed with different reference prices"
                )
            return existing

        # Async wait intentionally yields control; it never blocks the event loop.
        await asyncio.sleep(self.execution_latency_seconds)

        async with self._lock:
            existing = self._executions.get(key)
            if existing is not None:
                if self._execution_fingerprints[key] != price_fingerprint:
                    raise BoxSpreadPaperEngineError(
                        "paper intent already executed with different reference prices"
                    )
                return existing

            fills: list[BoxSpreadPaperLegFill] = []
            for leg in intent.legs:
                reference = prices[leg.role]
                if leg.action == "BUY":
                    simulated_price = reference * (Decimal("1") + self.adverse_slippage_rate)
                    fill_price = min(simulated_price, limits[leg.role]) if limits is not None else simulated_price
                else:
                    simulated_price = reference * (Decimal("1") - self.adverse_slippage_rate)
                    fill_price = max(simulated_price, limits[leg.role]) if limits is not None else simulated_price
                slippage = abs(fill_price - reference)
                fills.append(
                    BoxSpreadPaperLegFill(
                        role=leg.role,
                        action=leg.action,
                        quantity=leg.quantity,
                        reference_price=reference,
                        slippage=slippage,
                        fill_price=fill_price,
                        notional=fill_price * leg.quantity,
                    )
                )
            execution = BoxSpreadPaperExecution(
                idempotency_key=key,
                fills=tuple(fills),
                latency_seconds=self.execution_latency_seconds,
                slippage_rate=self.adverse_slippage_rate,
                executed_at=datetime.now(timezone.utc),
            )
            self._executions[key] = execution
            self._execution_fingerprints[key] = price_fingerprint
            return execution

    async def record_leg_fill(
        self,
        *,
        idempotency_key: str,
        role: str,
        fill_id: str,
        quantity: int,
        fill_price: object,
    ) -> "BoxSpreadPaperFillEvent":
        """Record a paper partial fill idempotently without touching a broker."""
        key = str(idempotency_key or "").strip()
        role = str(role or "").strip()
        event_id = str(fill_id or "").strip()
        if not key or not event_id:
            raise BoxSpreadPaperEngineError("intent key and fill_id are required")
        qty = _positive_integer(quantity, name="fill quantity")
        try:
            price = Decimal(str(fill_price))
        except (InvalidOperation, TypeError, ValueError):
            raise BoxSpreadPaperEngineError("fill price must be positive") from None
        if not price.is_finite() or price <= 0:
            raise BoxSpreadPaperEngineError("fill price must be positive")

        async with self._lock:
            intent = self._intents.get(key)
            if intent is None:
                raise BoxSpreadPaperEngineError("paper intent not found")
            if key in self._executions:
                raise BoxSpreadPaperEngineError("intent already has a full execution")
            leg = next((item for item in intent.legs if item.role == role), None)
            if leg is None:
                raise BoxSpreadPaperEngineError("unknown paper leg role")
            identity = (key, event_id)
            payload = (role, qty, price)
            previous = self._fill_identities.get(identity)
            if previous is not None:
                if previous != payload:
                    raise BoxSpreadPaperEngineError("fill_id reused with different fill data")
                return self._fill_events[identity]

            progress = self._leg_progress[key][role]
            if progress["cancelled"]:
                raise BoxSpreadPaperEngineError("cannot fill a cancelled leg")
            remaining = leg.quantity - progress["filled_quantity"]
            if qty > remaining:
                raise BoxSpreadPaperEngineError("fill quantity exceeds leg remaining quantity")

            event = BoxSpreadPaperFillEvent(
                idempotency_key=key, fill_id=event_id, role=role,
                action=leg.action, quantity=qty, fill_price=price,
                notional=price * qty, filled_at=datetime.now(timezone.utc),
            )
            progress["filled_quantity"] += qty
            progress["fill_notional"] += event.notional
            progress["status"] = "FILLED" if progress["filled_quantity"] == leg.quantity else "PARTIALLY_FILLED"
            self._fill_identities[identity] = payload
            self._fill_events[identity] = event
            return event

    async def retry_leg(self, *, idempotency_key: str, role: str) -> "BoxSpreadPaperLegProgress":
        """Mark one still-open paper leg for a retry; never submits an order."""
        key, role = str(idempotency_key or "").strip(), str(role or "").strip()
        async with self._lock:
            if key not in self._intents:
                raise BoxSpreadPaperEngineError("paper intent not found")
            if key in self._executions:
                raise BoxSpreadPaperEngineError("intent already has a full execution")
            progress = self._leg_progress[key].get(role)
            if progress is None:
                raise BoxSpreadPaperEngineError("unknown paper leg role")
            if progress["cancelled"]:
                raise BoxSpreadPaperEngineError("cannot retry a cancelled leg")
            if progress["filled_quantity"] >= progress["quantity"]:
                raise BoxSpreadPaperEngineError("cannot retry a fully filled leg")
            progress["retry_count"] += 1
            return self._progress_snapshot(key, role)

    async def cancel_leg(self, *, idempotency_key: str, role: str) -> "BoxSpreadPaperLegProgress":
        """Cancel the unfilled remainder of one paper leg and preserve its exposure."""
        key, role = str(idempotency_key or "").strip(), str(role or "").strip()
        async with self._lock:
            if key not in self._intents:
                raise BoxSpreadPaperEngineError("paper intent not found")
            if key in self._executions:
                raise BoxSpreadPaperEngineError("intent already has a full execution")
            progress = self._leg_progress[key].get(role)
            if progress is None:
                raise BoxSpreadPaperEngineError("unknown paper leg role")
            if progress["filled_quantity"] >= progress["quantity"]:
                raise BoxSpreadPaperEngineError("cannot cancel a fully filled leg")
            progress["cancelled"] = True
            progress["status"] = "CANCELLED" if progress["filled_quantity"] == 0 else "CANCELLED_WITH_RESIDUAL"
            return self._progress_snapshot(key, role)

    def _progress_snapshot(self, key: str, role: str) -> "BoxSpreadPaperLegProgress":
        item = self._leg_progress[key][role]
        return BoxSpreadPaperLegProgress(
            role=role, action=item["action"], quantity=item["quantity"],
            filled_quantity=item["filled_quantity"],
            remaining_quantity=item["quantity"] - item["filled_quantity"],
            average_fill_price=(item["fill_notional"] / item["filled_quantity"] if item["filled_quantity"] else None),
            status=item["status"], retry_count=item["retry_count"], cancelled=item["cancelled"],
        )

    async def get_fill_progress(self, idempotency_key: str) -> "BoxSpreadPaperFillProgress | None":
        key = str(idempotency_key or "").strip()
        async with self._lock:
            if key not in self._intents:
                return None
            legs = tuple(self._progress_snapshot(key, role) for role in _REQUIRED_LEG_ROLES)
            matched = min((leg.filled_quantity for leg in legs), default=0)
            residual = tuple(
                BoxSpreadPaperResidualExposure(
                    role=leg.role, action=leg.action,
                    quantity=leg.filled_quantity - matched,
                    signed_quantity=(leg.filled_quantity - matched) * (1 if leg.action == "BUY" else -1),
                )
                for leg in legs if leg.filled_quantity > matched
            )
            all_filled = all(leg.filled_quantity == leg.quantity for leg in legs)
            any_filled = any(leg.filled_quantity > 0 for leg in legs)
            any_cancelled = any(leg.cancelled for leg in legs)
            status = "FILLED" if all_filled else (
                "CANCELLED_WITH_RESIDUAL" if any_cancelled and any_filled else
                "CANCELLED" if any_cancelled else
                "PARTIALLY_FILLED" if any_filled else "OPEN"
            )
            return BoxSpreadPaperFillProgress(
                idempotency_key=key, status=status, legs=legs,
                matched_quantity=matched, residual_exposure=residual,
            )

    async def get_execution(self, idempotency_key: str) -> BoxSpreadPaperExecution | None:
        async with self._lock:
            return self._executions.get(str(idempotency_key or "").strip())

    async def get_intent(self, idempotency_key: str) -> BoxSpreadPaperIntent | None:
        async with self._lock:
            return self._intents.get(str(idempotency_key or "").strip())

    async def list_intents(self) -> tuple[BoxSpreadPaperIntent, ...]:
        async with self._lock:
            return tuple(sorted(self._intents.values(), key=lambda item: item.created_at))


__all__ = [
    "ADVERSE_SLIPPAGE_RATE",
    "PAPER_EXECUTION_LATENCY_SECONDS",
    "BoxSpreadPaperEngine",
    "BoxSpreadPaperEngineError",
    "BoxSpreadPaperExecution",
    "BoxSpreadPaperFillEvent",
    "BoxSpreadPaperFillProgress",
    "BoxSpreadPaperIntent",
    "BoxSpreadPaperLegProgress",
    "BoxSpreadPaperResidualExposure",
    "BoxSpreadPaperLegFill",
    "BoxSpreadPaperLegIntent",
]

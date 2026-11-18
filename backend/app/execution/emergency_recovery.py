"""Fail-closed recovery coordinator for residual multi-leg option exposure.

Broker adapters must explicitly implement RecoveryBroker before this module is
wired to them. Importing this module never enables or submits live orders.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Protocol


class RecoveryState(str, Enum):
    RECOVERING = "EMERGENCY_UNWIND"
    FLAT = "FLAT_AFTER_UNWIND"
    MANUAL_REVIEW = "MANUAL_REVIEW_RESIDUAL_EXPOSURE"


@dataclass(frozen=True)
class RecoveryOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    role: str
    status: str
    remaining_quantity: int | None = None

    @property
    def terminal(self) -> bool:
        status = self.status.strip().upper()
        return status in {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"} or (
            status == "PARTIALLY_FILLED" and self.remaining_quantity == 0
        )


@dataclass(frozen=True)
class RecoveryPosition:
    symbol: str
    quantity: int  # positive = long, negative = short

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("position symbol cannot be empty")


@dataclass(frozen=True)
class RecoverySnapshot:
    account_id: str
    broker: str
    authoritative: bool
    positions_reconciled: bool
    position_attribution_complete: bool
    orders: tuple[RecoveryOrder, ...]
    positions: tuple[RecoveryPosition, ...]


@dataclass(frozen=True)
class RecoveryQuote:
    bid: Decimal
    ask: Decimal
    bid_depth: int
    ask_depth: int
    tick_size: Decimal
    lot_size: int
    observed_at_ns: int


@dataclass(frozen=True)
class EmergencyClose:
    symbol: str
    side: str
    quantity: int
    limit_price: Decimal
    time_in_force: str
    client_order_id: str


@dataclass(frozen=True)
class RecoveryPolicy:
    max_quote_age_ns: int = 1_000_000_000
    max_slippage_bps: Decimal = Decimal("50")
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_quote_age_ns <= 0 or self.max_attempts < 1:
            raise ValueError("recovery age and attempt limits must be positive")
        if not self.max_slippage_bps.is_finite() or not Decimal("0") <= self.max_slippage_bps <= Decimal("500"):
            raise ValueError("max_slippage_bps must be between 0 and 500")


class RecoveryBroker(Protocol):
    name: str

    def recovery_snapshot(self, *, account_id: str, intent_id: str) -> RecoverySnapshot:
        """Return authoritative orders and positions scoped to account/intent."""
        ...

    def cancel_order(self, *, account_id: str, broker_order_id: str) -> None:
        """Request cancellation; a request is not proof of cancellation."""
        ...

    def recovery_quote(self, *, account_id: str, symbol: str) -> RecoveryQuote:
        """Return a fresh executable quote and displayed depth."""
        ...

    def place_ioc_limit(self, *, account_id: str, order: EmergencyClose) -> RecoveryOrder:
        """Place one bounded LIMIT/IOC close order."""
        ...


class RecoveryJournal(Protocol):
    def latch_entry_kill_switch(self, *, account_id: str, strategy: str, reason: str) -> None: ...
    def freeze_intent(self, *, account_id: str, intent_id: str, reason: str) -> None: ...
    def assert_exclusive_instrument_lease(self, *, account_id: str, intent_id: str, symbols: tuple[str, ...]) -> bool: ...
    def append_event(self, *, account_id: str, intent_id: str, event: str, details: dict) -> None: ...
    def set_state(self, *, account_id: str, intent_id: str, state: RecoveryState, reason: str) -> None: ...
    def release_risk_reservation(self, *, account_id: str, intent_id: str) -> None: ...
    def claim_recovery_attempt(self, *, account_id: str, intent_id: str, symbol: str) -> int: ...
    def alert_operator(self, *, account_id: str, intent_id: str, reason: str) -> None: ...


@dataclass(frozen=True)
class RecoveryResult:
    state: RecoveryState
    reason: str
    residual_positions: tuple[RecoveryPosition, ...]
    attempts: int


def _close_price(quote: RecoveryQuote, *, side: str, slippage_bps: Decimal) -> Decimal:
    if (
        not quote.bid.is_finite() or not quote.ask.is_finite()
        or quote.bid <= 0 or quote.ask <= 0 or quote.bid > quote.ask
        or quote.tick_size <= 0 or quote.lot_size <= 0
    ):
        raise ValueError("missing, crossed or invalid recovery quote")
    factor = slippage_bps / Decimal("10000")
    raw = quote.bid * (Decimal("1") - factor) if side == "SELL" else quote.ask * (Decimal("1") + factor)
    rounding = ROUND_FLOOR if side == "SELL" else ROUND_CEILING
    return (raw / quote.tick_size).to_integral_value(rounding=rounding) * quote.tick_size


class EmergencyRecoveryCoordinator:
    """Cancel -> authoritative reconcile -> bounded IOC close -> verify flat.

    The default is to close broker-confirmed residual option positions. It does
    not invent an underlying hedge: an improvised hedge can add basis, gamma,
    volatility and margin risk. Strategy-specific hedges need a separately
    approved contract and are intentionally outside this generic fallback.
    """

    def __init__(self, broker: RecoveryBroker, journal: RecoveryJournal, policy: RecoveryPolicy | None = None):
        self._broker = broker
        self._journal = journal
        self._policy = policy or RecoveryPolicy()

    def _snapshot(self, account_id: str, intent_id: str) -> RecoverySnapshot:
        snapshot = self._broker.recovery_snapshot(account_id=account_id, intent_id=intent_id)
        if not snapshot.authoritative:
            raise RuntimeError("broker order snapshot is not authoritative")
        if snapshot.account_id != account_id or snapshot.broker.strip().lower() != self._broker.name.strip().lower():
            raise RuntimeError("broker/account identity drift during recovery")
        return snapshot

    @staticmethod
    def _positions_are_safe(snapshot: RecoverySnapshot) -> bool:
        return snapshot.positions_reconciled and snapshot.position_attribution_complete

    @staticmethod
    def _residuals(snapshot: RecoverySnapshot, symbols: set[str], baseline: dict[str, int]) -> tuple[RecoveryPosition, ...]:
        # Broker positions are account-wide. Subtract the durable pre-entry
        # baseline so pre-existing holdings in the same option are never closed.
        current = {symbol: 0 for symbol in symbols}
        for position in snapshot.positions:
            if position.symbol in current:
                current[position.symbol] += position.quantity
        totals = {symbol: current[symbol] - baseline[symbol] for symbol in symbols}
        return tuple(RecoveryPosition(s, q) for s, q in sorted(totals.items()) if q)

    @staticmethod
    def _unresolved(snapshot: RecoverySnapshot) -> tuple[RecoveryOrder, ...]:
        return tuple(order for order in snapshot.orders if not order.terminal)

    def _manual(self, *, account_id: str, intent_id: str, reason: str,
                residuals: tuple[RecoveryPosition, ...], attempts: int) -> RecoveryResult:
        self._journal.set_state(
            account_id=account_id, intent_id=intent_id,
            state=RecoveryState.MANUAL_REVIEW, reason=reason,
        )
        self._journal.alert_operator(account_id=account_id, intent_id=intent_id, reason=reason)
        self._journal.append_event(
            account_id=account_id, intent_id=intent_id, event="RECOVERY_MANUAL_REVIEW",
            details={"reason": reason, "residuals": [(p.symbol, p.quantity) for p in residuals]},
        )
        return RecoveryResult(RecoveryState.MANUAL_REVIEW, reason, residuals, attempts)

    def _mark_flat(self, *, account_id: str, intent_id: str, attempts: int) -> RecoveryResult:
        self._journal.set_state(
            account_id=account_id, intent_id=intent_id,
            state=RecoveryState.FLAT, reason="authoritative broker positions confirm flat",
        )
        self._journal.append_event(
            account_id=account_id, intent_id=intent_id, event="RECOVERY_FLAT_VERIFIED",
            details={"attempts": attempts},
        )
        self._journal.release_risk_reservation(account_id=account_id, intent_id=intent_id)
        return RecoveryResult(RecoveryState.FLAT, "broker positions confirm flat", (), attempts)

    def recover(self, *, account_id: str, intent_id: str, strategy: str,
                symbols: tuple[str, ...], baseline_positions: tuple[RecoveryPosition, ...],
                now_ns: int | None = None) -> RecoveryResult:
        """Recover only named basket instruments; unrelated positions are untouched."""
        symbol_set = {s.strip() for s in symbols if s.strip()}
        baseline = {p.symbol: p.quantity for p in baseline_positions}
        if len(baseline) != len(baseline_positions):
            raise ValueError("pre-entry baseline must contain one row per instrument")
        if not account_id.strip() or not intent_id.strip() or not strategy.strip() or not symbol_set:
            raise ValueError("account, intent, strategy and basket symbols are required")
        if set(baseline) != symbol_set:
            raise ValueError("a durable pre-entry baseline is required for every basket instrument")
        self._journal.latch_entry_kill_switch(
            account_id=account_id, strategy=strategy, reason="multi-leg entry failed; recovery active",
        )
        self._journal.freeze_intent(
            account_id=account_id, intent_id=intent_id, reason="stop all unsent basket legs and retries",
        )
        self._journal.set_state(
            account_id=account_id, intent_id=intent_id,
            state=RecoveryState.RECOVERING, reason="cancel/reconcile/close workflow started",
        )
        self._journal.append_event(
            account_id=account_id, intent_id=intent_id, event="RECOVERY_STARTED",
            details={"strategy": strategy, "symbols": sorted(symbol_set)},
        )
        attempts = 0
        residuals: tuple[RecoveryPosition, ...] = ()
        try:
            snapshot = self._snapshot(account_id, intent_id)
            for order in snapshot.orders:
                if order.role.strip().upper() == "ENTRY" and not order.terminal:
                    self._broker.cancel_order(account_id=account_id, broker_order_id=order.broker_order_id)
                    self._journal.append_event(
                        account_id=account_id, intent_id=intent_id, event="ENTRY_CANCEL_REQUESTED",
                        details={"broker_order_id": order.broker_order_id},
                    )

            # A second authoritative read is mandatory after cancel requests.
            snapshot = self._snapshot(account_id, intent_id)
            if self._positions_are_safe(snapshot):
                residuals = self._residuals(snapshot, symbol_set, baseline)
            if self._unresolved(snapshot):
                return self._manual(
                    account_id=account_id, intent_id=intent_id,
                    reason="broker still reports non-terminal orders; closing could race a late fill",
                    residuals=residuals, attempts=attempts,
                )
            if not self._positions_are_safe(snapshot):
                return self._manual(
                    account_id=account_id, intent_id=intent_id,
                    reason="broker position snapshot is not fully reconciled and attributable to this intent",
                    residuals=(), attempts=attempts,
                )
            if not self._journal.assert_exclusive_instrument_lease(
                account_id=account_id, intent_id=intent_id, symbols=tuple(sorted(symbol_set)),
            ):
                return self._manual(
                    account_id=account_id, intent_id=intent_id,
                    reason="exclusive basket instrument lease is missing; residual attribution is unsafe",
                    residuals=residuals, attempts=attempts,
                )

            for attempt in range(1, self._policy.max_attempts + 1):
                attempts = attempt
                snapshot = self._snapshot(account_id, intent_id)
                if self._positions_are_safe(snapshot):
                    residuals = self._residuals(snapshot, symbol_set, baseline)
                if self._unresolved(snapshot):
                    return self._manual(
                        account_id=account_id, intent_id=intent_id,
                        reason="an order became non-terminal during recovery",
                        residuals=residuals, attempts=attempts,
                    )
                if not self._positions_are_safe(snapshot):
                    return self._manual(
                        account_id=account_id, intent_id=intent_id,
                        reason="broker position snapshot lost reconciliation or attribution during recovery",
                        residuals=(), attempts=attempts,
                    )
                residuals = self._residuals(snapshot, symbol_set, baseline)
                if not residuals:
                    return self._mark_flat(account_id=account_id, intent_id=intent_id, attempts=attempts)

                for position in residuals:
                    quote = self._broker.recovery_quote(account_id=account_id, symbol=position.symbol)
                    current_time = now_ns if now_ns is not None else time.time_ns()
                    age = current_time - quote.observed_at_ns
                    if age < 0 or age > self._policy.max_quote_age_ns:
                        raise RuntimeError("recovery quote is stale or timestamp is in the future")
                    side = "SELL" if position.quantity > 0 else "BUY"
                    depth = quote.bid_depth if side == "SELL" else quote.ask_depth
                    quantity = min(abs(position.quantity), depth)
                    quantity = (quantity // quote.lot_size) * quote.lot_size
                    if quantity <= 0:
                        raise RuntimeError("insufficient executable depth for one valid lot")
                    limit_price = _close_price(
                        quote, side=side, slippage_bps=self._policy.max_slippage_bps,
                    )
                    recovery_attempt = self._journal.claim_recovery_attempt(
                        account_id=account_id, intent_id=intent_id, symbol=position.symbol,
                    )
                    token = hashlib.sha256(
                        f"{account_id}|{intent_id}|{position.symbol}|{recovery_attempt}".encode("utf-8")
                    ).hexdigest()[:24]
                    close = EmergencyClose(
                        symbol=position.symbol, side=side, quantity=quantity,
                        limit_price=limit_price, time_in_force="IOC", client_order_id=f"REC-{token}",
                    )
                    placed = self._broker.place_ioc_limit(account_id=account_id, order=close)
                    if placed.client_order_id != close.client_order_id:
                        raise RuntimeError("broker returned a mismatched recovery client order ID")
                    self._journal.append_event(
                        account_id=account_id, intent_id=intent_id, event="EMERGENCY_CLOSE_SUBMITTED",
                        details={
                            "symbol": close.symbol, "side": close.side, "quantity": close.quantity,
                            "limit_price": str(close.limit_price), "client_order_id": close.client_order_id,
                            "broker_order_id": placed.broker_order_id, "status": placed.status,
                        },
                    )
                    if not placed.terminal:
                        return self._manual(
                            account_id=account_id, intent_id=intent_id,
                            reason="emergency close order is not terminal; reconcile before retry",
                            residuals=residuals, attempts=attempts,
                        )

            final = self._snapshot(account_id, intent_id)
            if not self._positions_are_safe(final):
                return self._manual(
                    account_id=account_id, intent_id=intent_id,
                    reason="final broker positions are not reconciled and attributable",
                    residuals=(), attempts=attempts,
                )
            residuals = self._residuals(final, symbol_set, baseline)
            if not residuals and not self._unresolved(final):
                return self._mark_flat(account_id=account_id, intent_id=intent_id, attempts=attempts)
            return self._manual(
                account_id=account_id, intent_id=intent_id,
                reason="residual exposure remains after bounded emergency close attempts",
                residuals=residuals, attempts=attempts,
            )
        except Exception as exc:
            try:
                snapshot = self._snapshot(account_id, intent_id)
                if self._positions_are_safe(snapshot):
                    residuals = self._residuals(snapshot, symbol_set, baseline)
            except Exception:
                residuals = ()  # unknown is not flat; kill switch and reservation remain latched
            return self._manual(
                account_id=account_id, intent_id=intent_id,
                reason=f"recovery stopped fail-closed: {type(exc).__name__}: {exc}",
                residuals=residuals, attempts=attempts,
            )

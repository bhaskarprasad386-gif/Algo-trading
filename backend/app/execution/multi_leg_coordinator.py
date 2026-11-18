"""All-legs-or-recover coordinator for option baskets.

This is an adapter-neutral orchestration contract. It deliberately does not
register broker adapters or enable Live routes. A broker must provide durable
client-order lookup, authoritative order/trade/position reconciliation and
cancel-by-order-ID semantics before integration.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Callable, Mapping, Protocol


class BasketStrategy(str, Enum):
    BOX_SPREAD = "BOX_SPREAD"
    CREDIT_BUTTERFLY = "CREDIT_BUTTERFLY"
    CONDOR = "CONDOR"


class BasketResultState(str, Enum):
    PREFLIGHT_REJECTED = "PREFLIGHT_REJECTED"
    DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED"
    FILLED = "FILLED"
    RECOVERED_FLAT = "RECOVERED_FLAT"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class BasketLeg:
    leg_id: str
    role: str
    symbol: str
    side: str
    ratio: int
    expiry: str
    option_type: str
    limit_price: Decimal

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.leg_id, self.role, self.symbol, self.expiry)):
            raise ValueError("leg identity, role, symbol and expiry are required")
        if self.side not in {"BUY", "SELL"} or self.option_type not in {"CE", "PE"}:
            raise ValueError("leg side or option type is invalid")
        if isinstance(self.ratio, bool) or not isinstance(self.ratio, int) or self.ratio <= 0:
            raise ValueError("leg ratio must be a positive integer")
        if not self.limit_price.is_finite() or self.limit_price <= 0:
            raise ValueError("leg ratio and limit price must be positive")


@dataclass(frozen=True)
class BasketIntent:
    account_id: str
    intent_id: str
    strategy: BasketStrategy
    lots: int
    lot_size: int
    legs: tuple[BasketLeg, ...]
    baseline_positions: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if not self.account_id.strip() or not self.intent_id.strip():
            raise ValueError("account and intent IDs are required")
        if (
            isinstance(self.lots, bool) or not isinstance(self.lots, int) or self.lots <= 0
            or isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size <= 0
            or not self.legs
        ):
            raise ValueError("lots, lot size and legs are required as positive integers")
        if len({x.leg_id for x in self.legs}) != len(self.legs):
            raise ValueError("leg IDs must be unique")
        if len({x.role for x in self.legs}) != len(self.legs):
            raise ValueError("leg roles must be unique")
        if len({x.symbol for x in self.legs}) != len(self.legs):
            raise ValueError("each basket leg must reference a distinct instrument")
        if {symbol for symbol, _ in self.baseline_positions} != {x.symbol for x in self.legs}:
            raise ValueError("pre-entry baseline must cover every basket instrument exactly once")
        if len({x.expiry for x in self.legs}) != 1:
            raise ValueError("all basket legs must share one expiry")
        if self.strategy is BasketStrategy.CREDIT_BUTTERFLY and len({x.option_type for x in self.legs}) != 1:
            raise ValueError("Butterfly legs must share one option type")
        if len({symbol for symbol, _ in self.baseline_positions}) != len(self.baseline_positions):
            raise ValueError("baseline must contain one row per symbol")
        if any(not symbol.strip() or isinstance(quantity, bool) or not isinstance(quantity, int)
               for symbol, quantity in self.baseline_positions):
            raise ValueError("baseline symbols and integer quantities are required")

    def validate_shape(self) -> None:
        ratios = sorted(x.ratio for x in self.legs)
        expected = {
            BasketStrategy.BOX_SPREAD: (4, [1, 1, 1, 1]),
            BasketStrategy.CREDIT_BUTTERFLY: (3, [1, 1, 2]),
            BasketStrategy.CONDOR: (4, [1, 1, 1, 1]),
        }[self.strategy]
        if len(self.legs) != expected[0] or ratios != expected[1]:
            raise ValueError(f"{self.strategy.value} has invalid leg count or ratio shape")
        option_counts = {kind: sum(x.option_type == kind for x in self.legs) for kind in {"CE", "PE"}}
        if self.strategy is BasketStrategy.BOX_SPREAD and option_counts != {"CE": 2, "PE": 2}:
            raise ValueError("Box Spread must contain two CE and two PE legs")
        if self.strategy is BasketStrategy.CONDOR and len({x.option_type for x in self.legs}) == 2 and option_counts != {"CE": 2, "PE": 2}:
            raise ValueError("Iron Condor must contain two CE and two PE legs")

    def quantity_for(self, leg: BasketLeg) -> int:
        return self.lots * self.lot_size * leg.ratio


@dataclass(frozen=True)
class ExecutableQuote:
    bid: Decimal
    ask: Decimal
    bid_depth: int
    ask_depth: int
    tick_size: Decimal
    lot_size: int
    observed_at_ns: int


@dataclass(frozen=True)
class EdgeCheck:
    net_edge: Decimal
    minimum_net_edge: Decimal
    snapshot_id: str

    def __post_init__(self) -> None:
        if not self.net_edge.is_finite() or not self.minimum_net_edge.is_finite():
            raise ValueError("net edge values must be finite")
        if not self.snapshot_id.strip():
            raise ValueError("edge snapshot identity is required")


@dataclass(frozen=True)
class PreflightEvidence:
    quotes: Mapping[str, ExecutableQuote]
    edge: EdgeCheck
    risk_approved: bool
    margin_approved: bool
    snapshot_observed_at_ns: int


@dataclass(frozen=True)
class BrokerOrderAck:
    client_order_id: str
    broker_order_id: str | None
    status: str
    accepted: bool


@dataclass(frozen=True)
class ReconciledLeg:
    client_order_id: str
    broker_order_id: str | None
    filled_quantity: int
    order_terminal: bool


@dataclass(frozen=True)
class BasketReconciliation:
    authoritative: bool
    orders_complete: bool
    trades_complete: bool
    positions_reconciled: bool
    legs: tuple[ReconciledLeg, ...]


@dataclass(frozen=True)
class BasketResult:
    state: BasketResultState
    reason: str
    client_order_ids: tuple[str, ...]
    recovery_state: str | None = None


class BasketBroker(Protocol):
    async def place_limit(self, *, account_id: str, client_order_id: str,
                          symbol: str, side: str, quantity: int,
                          limit_price: Decimal) -> BrokerOrderAck: ...
    async def reconcile_intent(self, *, account_id: str, intent_id: str,
                               client_order_ids: tuple[str, ...]) -> BasketReconciliation: ...


class BasketJournal(Protocol):
    async def claim_submission(self, *, account_id: str, intent_id: str,
                               strategy: str, snapshot_id: str) -> bool: ...
    async def record_submission_started(self, *, account_id: str, intent_id: str,
                                        client_order_ids: tuple[str, ...]) -> None: ...
    async def record_order_ack(self, *, account_id: str, intent_id: str,
                               ack: BrokerOrderAck) -> None: ...
    async def freeze_intent(self, *, account_id: str, intent_id: str, reason: str) -> None: ...
    async def complete_filled_basket(self, *, account_id: str, intent_id: str,
                                     reconciliation: BasketReconciliation) -> None: ...
    async def reject_preflight(self, *, account_id: str, intent_id: str, reason: str) -> None: ...
    async def require_manual_review(self, *, account_id: str, intent_id: str,
                                    reason: str) -> None: ...


RecoveryCall = Callable[..., object]
EdgeEvaluator = Callable[[BasketIntent, Mapping[str, ExecutableQuote]], EdgeCheck]


def _round_to_tick(price: Decimal, tick: Decimal, *, side: str) -> Decimal:
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    result = (price / tick).to_integral_value(rounding=rounding) * tick
    if result <= 0:
        raise ValueError("limit price is not positive after tick rounding")
    return result


class MultiLegBasketCoordinator:
    """Submit a pre-approved basket concurrently; success requires full fills."""

    def __init__(self, broker: BasketBroker, journal: BasketJournal,
                 recovery: RecoveryCall, *,
                 max_quote_age_ns: int = 1_000_000_000,
                 max_cross_leg_skew_ns: int = 250_000_000,
                 max_slippage_bps: Decimal = Decimal("50"),
                 submit_timeout_seconds: float = 3.0,
                 edge_evaluator: EdgeEvaluator | None = None):
        if max_quote_age_ns <= 0 or max_cross_leg_skew_ns < 0 or submit_timeout_seconds <= 0:
            raise ValueError("coordinator timing policy is invalid")
        if not max_slippage_bps.is_finite() or not Decimal("0") <= max_slippage_bps <= Decimal("500"):
            raise ValueError("slippage collar must be between 0 and 500 bps")
        self._broker = broker
        self._journal = journal
        self._recovery = recovery
        self._max_age = max_quote_age_ns
        self._max_skew = max_cross_leg_skew_ns
        self._slippage = max_slippage_bps
        self._timeout = submit_timeout_seconds
        self._edge_evaluator = edge_evaluator

    def _validate_preflight(self, intent: BasketIntent, evidence: PreflightEvidence,
                             *, now_ns: int) -> None:
        intent.validate_shape()
        if not evidence.risk_approved or not evidence.margin_approved:
            raise ValueError("risk or margin approval is missing")
        if self._edge_evaluator is None:
            raise ValueError("strategy-specific live-quote edge evaluator is not configured")
        timestamps = []
        for leg in intent.legs:
            quote = evidence.quotes.get(leg.symbol)
            if quote is None:
                raise ValueError(f"missing executable quote for {leg.symbol}")
            if (
                not quote.bid.is_finite() or not quote.ask.is_finite()
                or not quote.tick_size.is_finite() or quote.bid <= 0 or quote.ask <= 0
                or quote.bid > quote.ask or quote.tick_size <= 0
                or quote.lot_size != intent.lot_size
                or isinstance(quote.bid_depth, bool) or not isinstance(quote.bid_depth, int) or quote.bid_depth < 0
                or isinstance(quote.ask_depth, bool) or not isinstance(quote.ask_depth, int) or quote.ask_depth < 0
                or quote.observed_at_ns <= 0
            ):
                raise ValueError(f"invalid or crossed quote for {leg.symbol}")
            age = now_ns - quote.observed_at_ns
            if age < 0 or age > self._max_age:
                raise ValueError(f"stale quote for {leg.symbol}")
            timestamps.append(quote.observed_at_ns)
            quantity = intent.quantity_for(leg)
            depth = quote.ask_depth if leg.side == "BUY" else quote.bid_depth
            if depth < quantity:
                raise ValueError(f"insufficient executable depth for {leg.symbol}")
            executable = quote.ask if leg.side == "BUY" else quote.bid
            collar = self._slippage / Decimal("10000")
            if leg.side == "BUY":
                max_price = executable * (Decimal("1") + collar)
                if leg.limit_price < executable or leg.limit_price > max_price:
                    raise ValueError(f"BUY limit outside executable price collar for {leg.symbol}")
            else:
                min_price = executable * (Decimal("1") - collar)
                if leg.limit_price > executable or leg.limit_price < min_price:
                    raise ValueError(f"SELL limit outside executable price collar for {leg.symbol}")
            if leg.limit_price != _round_to_tick(leg.limit_price, quote.tick_size, side=leg.side):
                raise ValueError(f"limit price violates tick size for {leg.symbol}")
        if max(timestamps) - min(timestamps) > self._max_skew:
            raise ValueError("cross-leg quote timestamp skew exceeds policy")
        # Recompute edge from this exact executable quote set. Scanner-alert
        # edge is never sufficient authorization for a broker submission.
        current_edge = self._edge_evaluator(intent, evidence.quotes)
        if current_edge.snapshot_id != evidence.edge.snapshot_id:
            raise ValueError("recomputed edge does not belong to the approved quote snapshot")
        if current_edge.net_edge < current_edge.minimum_net_edge:
            raise ValueError("recomputed net edge after fees is below the required threshold")
        snapshot_age = now_ns - evidence.snapshot_observed_at_ns
        if evidence.snapshot_observed_at_ns <= 0 or snapshot_age < 0 or snapshot_age > self._max_age:
            raise ValueError("basket snapshot is stale or timestamped in the future")

    @staticmethod
    def _client_order_id(intent: BasketIntent, leg: BasketLeg) -> str:
        # Deterministic bounded ID supports broker lookup after an unknown ack.
        import hashlib
        import re
        token = hashlib.sha256(
            f"{intent.account_id}|{intent.intent_id}|{leg.leg_id}".encode("utf-8")
        ).hexdigest()[:24]
        prefix = re.sub(r"[^A-Za-z0-9]", "", intent.intent_id)[:12] or "BASKET"
        return f"B0{prefix}{token}"

    async def execute(self, intent: BasketIntent, evidence: PreflightEvidence,
                      *, now_ns: int) -> BasketResult:
        try:
            self._validate_preflight(intent, evidence, now_ns=now_ns)
        except Exception as exc:
            reason = f"preflight rejected: {type(exc).__name__}: {exc}"
            await self._journal.reject_preflight(
                account_id=intent.account_id, intent_id=intent.intent_id, reason=reason,
            )
            return BasketResult(BasketResultState.PREFLIGHT_REJECTED, reason, ())

        client_ids = tuple(self._client_order_id(intent, leg) for leg in intent.legs)
        try:
            claimed = await self._journal.claim_submission(
                account_id=intent.account_id, intent_id=intent.intent_id,
                strategy=intent.strategy.value, snapshot_id=evidence.edge.snapshot_id,
            )
        except Exception as exc:
            await self._journal.require_manual_review(
                account_id=intent.account_id, intent_id=intent.intent_id,
                reason=f"durable submission claim failed: {type(exc).__name__}",
            )
            return BasketResult(BasketResultState.MANUAL_REVIEW, "durable submission claim failed", client_ids)
        if not claimed:
            return BasketResult(
                BasketResultState.DUPLICATE_BLOCKED,
                "intent submission was already claimed; duplicate submission is blocked",
                client_ids,
            )

        try:
            await self._journal.record_submission_started(
                account_id=intent.account_id, intent_id=intent.intent_id,
                client_order_ids=client_ids,
            )
        except Exception as exc:
            await self._journal.freeze_intent(
                account_id=intent.account_id, intent_id=intent.intent_id,
                reason="durable submission-start record failed before broker calls",
            )
            await self._journal.require_manual_review(
                account_id=intent.account_id, intent_id=intent.intent_id,
                reason=f"submission not sent because durable journal failed: {type(exc).__name__}",
            )
            return BasketResult(BasketResultState.MANUAL_REVIEW, "submission journal unavailable; no broker call sent", client_ids)
        acks: list[BrokerOrderAck | Exception] = []
        async def submit(leg: BasketLeg, client_id: str) -> BrokerOrderAck:
            return await asyncio.wait_for(
                self._broker.place_limit(
                    account_id=intent.account_id, client_order_id=client_id,
                    symbol=leg.symbol, side=leg.side,
                    quantity=intent.quantity_for(leg), limit_price=leg.limit_price,
                ),
                timeout=self._timeout,
            )
        acks = list(await asyncio.gather(
            *(submit(leg, client_id) for leg, client_id in zip(intent.legs, client_ids, strict=True)),
            return_exceptions=True,
        ))
        all_accepted = True
        for expected_id, ack in zip(client_ids, acks, strict=True):
            if isinstance(ack, Exception):
                all_accepted = False
                continue
            try:
                await self._journal.record_order_ack(
                    account_id=intent.account_id, intent_id=intent.intent_id, ack=ack,
                )
            except Exception:
                # The deterministic client ID lets recovery find an accepted
                # broker order even if the acknowledgement write was lost.
                all_accepted = False
                continue
            if ack.client_order_id != expected_id or not ack.accepted or ack.status.upper() in {
                "REJECTED", "UNKNOWN", "ERROR",
            }:
                all_accepted = False

        if all_accepted:
            try:
                reconciliation = await asyncio.wait_for(
                    self._broker.reconcile_intent(
                        account_id=intent.account_id, intent_id=intent.intent_id,
                        client_order_ids=client_ids,
                    ),
                    timeout=self._timeout,
                )
                if (
                    reconciliation.authoritative and reconciliation.orders_complete
                    and reconciliation.trades_complete and reconciliation.positions_reconciled
                    and len(reconciliation.legs) == len(intent.legs)
                ):
                    by_id = {x.client_order_id: x for x in reconciliation.legs}
                    if len(by_id) == len(intent.legs) and all(
                        by_id.get(client_id) is not None
                        and by_id[client_id].filled_quantity == intent.quantity_for(leg)
                        and by_id[client_id].order_terminal
                        for leg, client_id in zip(intent.legs, client_ids, strict=True)
                    ):
                        await self._journal.complete_filled_basket(
                            account_id=intent.account_id, intent_id=intent.intent_id,
                            reconciliation=reconciliation,
                        )
                        return BasketResult(BasketResultState.FILLED, "all legs fully filled and reconciled", client_ids)
            except Exception:
                # Timeout or malformed evidence is an UNKNOWN outcome, never a retry signal.
                pass

        reason = "one or more legs rejected, timed out, partially filled, unknown, or failed reconciliation"
        freeze_error = None
        try:
            await self._journal.freeze_intent(
                account_id=intent.account_id, intent_id=intent.intent_id, reason=reason,
            )
        except Exception as exc:
            # Still invoke the independent recovery journal/kill-switch path.
            freeze_error = type(exc).__name__
        try:
            recovery_result = await asyncio.to_thread(
                self._recovery,
                account_id=intent.account_id, intent_id=intent.intent_id,
                strategy=intent.strategy.value,
                symbols=tuple(sorted({x.symbol for x in intent.legs})),
                baseline_positions=intent.baseline_positions,
            )
            recovery_state = getattr(getattr(recovery_result, "state", None), "value",
                                      getattr(recovery_result, "state", None))
            if recovery_state == "FLAT_AFTER_UNWIND" and freeze_error is None:
                return BasketResult(BasketResultState.RECOVERED_FLAT, reason, client_ids, str(recovery_state))
            await self._journal.require_manual_review(
                account_id=intent.account_id, intent_id=intent.intent_id,
                reason=f"{reason}; recovery outcome={recovery_state}; freeze_error={freeze_error}",
            )
            return BasketResult(BasketResultState.MANUAL_REVIEW, reason, client_ids, str(recovery_state))
        except Exception as exc:
            await self._journal.require_manual_review(
                account_id=intent.account_id, intent_id=intent.intent_id,
                reason=f"{reason}; recovery invocation failed: {type(exc).__name__}",
            )
            return BasketResult(BasketResultState.MANUAL_REVIEW, reason, client_ids, "UNKNOWN")

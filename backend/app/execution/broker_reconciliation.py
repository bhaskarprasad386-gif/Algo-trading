"""Pure read-only comparison of persisted execution identities with broker snapshots.

This module classifies evidence only. It never calls a broker, writes the
database, changes an intent, retries an order, or enables Live execution.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from datetime import date, datetime, time as datetime_time
import math
from zoneinfo import ZoneInfo


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    INCOMPLETE_SNAPSHOT = "INCOMPLETE_SNAPSHOT"
    MISSING_BROKER_ORDER = "MISSING_BROKER_ORDER"
    MISSING_PERSISTED_BROKER_ORDER_ID = "MISSING_PERSISTED_BROKER_ORDER_ID"
    UNRECORDED_BROKER_ORDER = "UNRECORDED_BROKER_ORDER"
    BROKER_ORDER_MISMATCH = "BROKER_ORDER_MISMATCH"
    UNRECORDED_BROKER_TRADE = "UNRECORDED_BROKER_TRADE"
    LOCAL_FILL_MISSING_AT_BROKER = "LOCAL_FILL_MISSING_AT_BROKER"
    FILL_QUANTITY_MISMATCH = "FILL_QUANTITY_MISMATCH"
    DUPLICATE_BROKER_ORDER_ID = "DUPLICATE_BROKER_ORDER_ID"
    DUPLICATE_BROKER_TRADE_ID = "DUPLICATE_BROKER_TRADE_ID"
    TRADE_ORDER_MISSING = "TRADE_ORDER_MISSING"
    ORDER_TRADE_FILL_TOTAL_MISMATCH = "ORDER_TRADE_FILL_TOTAL_MISMATCH"
    ORDER_STATUS_REQUIRES_RECONCILIATION = "ORDER_STATUS_REQUIRES_RECONCILIATION"
    ORDER_STATUS_CONFLICT = "ORDER_STATUS_CONFLICT"


@dataclass(frozen=True)
class ExpectedOrder:
    leg_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: int

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.leg_id, self.broker_order_id, self.symbol)):
            raise ValueError("leg_id, broker_order_id and symbol are required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    raw_status: str | None = None

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip() or not self.symbol.strip():
            raise ValueError("broker order identity and symbol are required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0 or not 0 <= self.filled_quantity <= self.quantity:
            raise ValueError("invalid broker order quantities")


@dataclass(frozen=True)
class BrokerTradeSnapshot:
    broker_trade_id: str
    broker_order_id: str
    quantity: int
    fill_price: float | None = None
    occurred_at_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.broker_trade_id.strip() or not self.broker_order_id.strip():
            raise ValueError("broker trade and order identities are required")
        if self.quantity <= 0:
            raise ValueError("trade quantity must be positive")
        if self.fill_price is not None and (
            not math.isfinite(self.fill_price) or self.fill_price <= 0
        ):
            raise ValueError("trade fill price must be finite and positive")
        if self.occurred_at_ns is not None and self.occurred_at_ns <= 0:
            raise ValueError("trade timestamp must be positive when supplied")


def parse_trade_timestamp_ns(value: object, *, trading_date: str) -> int | None:
    """Parse a provider trade timestamp into IST epoch ns; reject placeholders.

    A time-only provider field is anchored to the adapter's current trading date.
    Known placeholder dates (notably Shoonya's 1980 example value) are not
    represented as real execution times.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%H:%M:%S",
    )
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%H:%M:%S":
                parsed = datetime.combine(date.fromisoformat(trading_date), parsed.time())
            break
        except ValueError:
            continue
    if parsed is None or parsed.date().isoformat() == "1980-01-01":
        return None
    return int(parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class BrokerSnapshot:
    """Order/trade evidence with explicit source account and broker identity."""

    account_id: str
    broker: str
    orders: tuple[BrokerOrderSnapshot, ...]
    trades: tuple[BrokerTradeSnapshot, ...]
    complete: bool

    def __post_init__(self) -> None:
        if not self.account_id.strip() or not self.broker.strip():
            raise ValueError("snapshot account_id and broker are required")
        if not isinstance(self.complete, bool):
            raise ValueError("snapshot completeness must be a boolean")


@dataclass(frozen=True)
class ReconciliationIssue:
    status: ReconciliationStatus
    identity: str
    detail: str


@dataclass(frozen=True)
class ReconciliationReport:
    account_id: str
    broker: str
    snapshot_complete: bool
    matched_order_count: int
    issues: tuple[ReconciliationIssue, ...]

    @property
    def is_consistent(self) -> bool:
        # Consistency is not an authorization to resume or trade.
        return self.snapshot_complete and not self.issues


def reconcile_snapshot(
    *,
    account_id: str,
    broker: str,
    expected_orders: tuple[ExpectedOrder, ...],
    local_fill_quantities: dict[str, int],
    snapshot: BrokerSnapshot,
) -> ReconciliationReport:
    """Compare a caller-supplied, account-scoped snapshot without side effects.

    local_fill_quantities maps persisted broker_trade_id -> persisted quantity.
    A caller must obtain a complete broker order AND trade history window before
    treating an empty result as authoritative. Any discrepancy blocks resume.
    """
    if not account_id.strip() or not broker.strip():
        raise ValueError("account_id and broker are required")
    if snapshot.account_id != account_id or snapshot.broker.strip().lower() != broker.strip().lower():
        raise ValueError("broker snapshot account/broker scope does not match reconciliation request")

    issues: list[ReconciliationIssue] = []
    if not snapshot.complete:
        issues.append(ReconciliationIssue(
            ReconciliationStatus.INCOMPLETE_SNAPSHOT, account_id,
            "Broker order/trade history is not confirmed complete; absence is not evidence.",
        ))

    expected_ids = [x.broker_order_id for x in expected_orders]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("expected broker_order_id values must be unique")
    broker_order_counts = Counter(x.broker_order_id for x in snapshot.orders)
    broker_order_ids = list(broker_order_counts)
    for identity, count in sorted(broker_order_counts.items()):
        if count <= 1:
            continue
        issues.append(ReconciliationIssue(
            ReconciliationStatus.DUPLICATE_BROKER_ORDER_ID, identity,
            "Broker snapshot contains the same order identity more than once.",
        ))
    broker_by_id = {x.broker_order_id: x for x in snapshot.orders}
    expected_id_set = set(expected_ids)
    for identity in sorted(set(broker_order_ids) - expected_id_set):
        issues.append(ReconciliationIssue(
            ReconciliationStatus.UNRECORDED_BROKER_ORDER, identity,
            "Broker snapshot contains an order absent from the selected persisted intent scope.",
        ))

    matched = 0
    for expected in expected_orders:
        actual = broker_by_id.get(expected.broker_order_id)
        if actual is None:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.MISSING_BROKER_ORDER, expected.broker_order_id,
                f"Persisted leg {expected.leg_id} has no matching broker order in the supplied snapshot.",
            ))
            continue
        if (
            actual.symbol != expected.symbol
            or actual.side != expected.side
            or actual.quantity != expected.quantity
        ):
            issues.append(ReconciliationIssue(
                ReconciliationStatus.BROKER_ORDER_MISMATCH, expected.broker_order_id,
                f"Persisted leg {expected.leg_id} differs from broker symbol/side/requested quantity.",
            ))
        else:
            matched += 1

    broker_trade_counts = Counter(x.broker_trade_id for x in snapshot.trades)
    broker_trade_ids = list(broker_trade_counts)
    for identity, count in sorted(broker_trade_counts.items()):
        if count <= 1:
            continue
        issues.append(ReconciliationIssue(
            ReconciliationStatus.DUPLICATE_BROKER_TRADE_ID, identity,
            "Broker snapshot contains the same trade identity more than once.",
        ))

    broker_trade_map = {x.broker_trade_id: x for x in snapshot.trades}
    broker_order_map = {x.broker_order_id: x for x in snapshot.orders}
    trade_totals_by_order: dict[str, int] = {}
    for trade in snapshot.trades:
        if trade.broker_order_id not in broker_order_map:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.TRADE_ORDER_MISSING, trade.broker_trade_id,
                "Broker trade references an order absent from the supplied broker order snapshot.",
            ))
        trade_totals_by_order[trade.broker_order_id] = (
            trade_totals_by_order.get(trade.broker_order_id, 0) + trade.quantity
        )
    for order_id, broker_order in broker_order_map.items():
        trade_total = trade_totals_by_order.get(order_id, 0)
        if trade_total != broker_order.filled_quantity:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.ORDER_TRADE_FILL_TOTAL_MISMATCH, order_id,
                f"Broker order reports filled quantity {broker_order.filled_quantity}, but broker trade rows total {trade_total}.",
            ))

    for trade_id, local_qty in local_fill_quantities.items():
        if not trade_id.strip() or local_qty <= 0:
            raise ValueError("local fill identities and quantities must be positive")
        broker_trade = broker_trade_map.get(trade_id)
        if broker_trade is None:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.LOCAL_FILL_MISSING_AT_BROKER, trade_id,
                "Persisted fill is absent from the supplied broker trade snapshot.",
            ))
        elif broker_trade.quantity != local_qty:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.FILL_QUANTITY_MISMATCH, trade_id,
                f"Persisted fill quantity {local_qty} differs from broker quantity {broker_trade.quantity}.",
            ))

    for trade in snapshot.trades:
        if trade.broker_trade_id not in local_fill_quantities:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.UNRECORDED_BROKER_TRADE, trade.broker_trade_id,
                "Broker reports a trade that is not present in the local immutable fill ledger.",
            ))

    return ReconciliationReport(
        account_id=account_id,
        broker=broker.strip().lower(),
        snapshot_complete=snapshot.complete,
        matched_order_count=matched,
        issues=tuple(issues),
    )

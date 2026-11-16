"""Provider-neutral, read-only broker order/trade history contract.

The contract makes account scope, requested time window, pagination and proven
completeness explicit. It does not authorize order placement or execution resume.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.execution.broker_reconciliation import BrokerSnapshot


class BrokerHistoryUnavailable(RuntimeError):
    """Raised when a provider cannot prove the requested history window."""


@dataclass(frozen=True)
class BrokerHistoryWindow:
    """A provider response for one explicitly requested, timezone-aware window."""

    snapshot: BrokerSnapshot
    window_start: datetime
    window_end: datetime
    pages_fetched: int
    has_more: bool
    next_cursor: str | None = None
    provider_window: str = "REQUESTED_RANGE"

    def __post_init__(self) -> None:
        if self.window_start.tzinfo is None or self.window_start.utcoffset() is None:
            raise ValueError("window_start must be timezone-aware")
        if self.window_end.tzinfo is None or self.window_end.utcoffset() is None:
            raise ValueError("window_end must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be later than window_start")
        if self.pages_fetched < 1:
            raise ValueError("pages_fetched must be at least one")
        if not self.provider_window.strip():
            raise ValueError("provider_window is required")
        if self.has_more and not (self.next_cursor and self.next_cursor.strip()):
            raise ValueError("a provider continuation cursor is required when has_more is true")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor must be absent when history pagination is complete")
        if self.snapshot.complete and self.has_more:
            raise ValueError("a paginated response cannot be complete while has_more is true")

    @property
    def complete(self) -> bool:
        """True only when provider snapshot and pagination both prove completeness."""
        return self.snapshot.complete and not self.has_more


class BrokerHistorySnapshotProvider(Protocol):
    """Read-only provider capability, deliberately separate from order routing."""

    name: str

    def fetch_order_trade_snapshot(
        self,
        *,
        account_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> BrokerHistoryWindow:
        """Fetch order/trade evidence for the requested account and time window.

        Implementations must reject unsupported windows, incomplete pagination,
        account mismatches, malformed rows and provider errors. They must never
        silently convert an unavailable history response into an empty complete
        snapshot.
        """
        ...


def validated_daily_history_window(
    *,
    provider_name: str,
    account_id: str,
    window_start: datetime,
    window_end: datetime,
    daily_history: object,
    provider_window: str,
) -> BrokerHistoryWindow:
    """Validate adapter output again at the provider boundary.

    Session adapters are trusted to enforce their own account scope, but this
    boundary deliberately verifies the returned evidence as well. A malformed
    or cross-account/cross-broker snapshot must never be relabelled as complete
    history merely because the adapter call returned successfully.
    """
    snapshot = getattr(daily_history, "snapshot", None)
    if not isinstance(snapshot, BrokerSnapshot):
        raise BrokerHistoryUnavailable(
            f"{provider_name} history adapter returned no BrokerSnapshot"
        )
    expected_account = str(account_id).strip()
    expected_broker = str(provider_name).strip().lower()
    if not expected_account:
        raise BrokerHistoryUnavailable("history account scope is required")
    if str(snapshot.account_id).strip() != expected_account:
        raise BrokerHistoryUnavailable(
            f"{expected_broker} history snapshot account scope does not match the request"
        )
    if str(snapshot.broker).strip().lower() != expected_broker:
        raise BrokerHistoryUnavailable(
            f"{expected_broker} history snapshot broker identity does not match the provider"
        )
    if not snapshot.complete:
        raise BrokerHistoryUnavailable(
            f"{expected_broker} history adapter returned an incomplete snapshot"
        )
    return BrokerHistoryWindow(
        snapshot=snapshot,
        window_start=window_start,
        window_end=window_end,
        pages_fetched=1,
        has_more=False,
        provider_window=provider_window,
    )

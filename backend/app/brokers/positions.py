"""Read-only broker position snapshot capability contract.

This protocol is deliberately separate from order routing and order/trade
history. Providers must prove account scope, trading date, freshness and full
pagination; unsupported or partial responses must raise, never masquerade as
an empty flat account.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from app.execution.broker_positions import BrokerPositionSnapshot


class BrokerPositionsUnavailable(RuntimeError):
    """Raised when authoritative, complete positions cannot be obtained."""


class BrokerPositionSnapshotProvider(Protocol):
    """Optional read-only capability; not every broker adapter implements it."""

    name: str

    def fetch_position_snapshot(
        self,
        *,
        account_id: str,
        trading_date: date,
        as_of: datetime,
    ) -> BrokerPositionSnapshot:
        """Return one complete account-scoped net-position snapshot.

        Implementations must:
        - use the already-authenticated account and reject account mismatch;
        - normalize exchange, instrument, product, currency and signed net qty;
        - exhaust provider pagination before setting complete=True;
        - attach a timezone-aware provider fetch timestamp and trading date;
        - reject unsupported product/segment coverage and malformed rows;
        - raise BrokerPositionsUnavailable on errors or unprovable completeness.

        An empty positions tuple is valid only when the provider explicitly
        confirms that the entire requested account position book is complete.
        This capability is read-only and never authorizes execution recovery.
        """
        ...

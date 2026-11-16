"""Cross-provider structural conformance for the read-only history capability."""

import inspect

import pytest

from app.brokers.angel_one_history import AngelOneHistoryProvider
from app.brokers.fyers_history import FyersHistoryProvider
from app.brokers.history import BrokerHistorySnapshotProvider
from app.brokers.mstock_history import MStockHistoryProvider
from app.brokers.shoonya_history import ShoonyaHistoryProvider
from app.brokers.zerodha_history import ZerodhaHistoryProvider


PROVIDERS = (
    (AngelOneHistoryProvider, "angel_one"),
    (ZerodhaHistoryProvider, "zerodha"),
    (FyersHistoryProvider, "fyers"),
    (ShoonyaHistoryProvider, "shoonya"),
    (MStockHistoryProvider, "mstock"),
)


@pytest.mark.parametrize(("provider_type", "expected_name"), PROVIDERS)
def test_all_five_history_providers_implement_the_same_read_only_contract(
    provider_type, expected_name
):
    method = getattr(provider_type, "fetch_order_trade_snapshot", None)
    assert callable(method), provider_type.__name__

    signature = inspect.signature(method)
    assert tuple(signature.parameters) == (
        "self", "account_id", "window_start", "window_end",
    )
    assert provider_type.name == expected_name

    # The protocol is intentionally a read-only history capability, not an
    # order execution interface. Adapter mutation remains separately gated.
    assert not hasattr(provider_type, "place_order")
    assert not hasattr(provider_type, "cancel_order")
    assert not hasattr(provider_type, "modify_order")


def test_provider_protocol_exposes_only_history_snapshot_operation():
    protocol_methods = {
        name for name, value in vars(BrokerHistorySnapshotProvider).items()
        if callable(value) and not name.startswith("_")
    }
    assert protocol_methods == {"fetch_order_trade_snapshot"}


from datetime import datetime, timedelta, timezone

from app.brokers.history import (
    BrokerHistoryUnavailable,
    BrokerHistoryWindow,
    validated_daily_history_window,
)
from app.execution.broker_reconciliation import BrokerSnapshot


def _history_snapshot(*, account_id="acct-1", broker="fyers", complete=True):
    return BrokerSnapshot(
        account_id=account_id,
        broker=broker,
        orders=(),
        trades=(),
        complete=complete,
    )


def _history_window():
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (_history_snapshot(account_id="other-account"), "account scope"),
        (_history_snapshot(broker="zerodha"), "broker identity"),
        (_history_snapshot(complete=False), "incomplete snapshot"),
    ],
)
def test_provider_boundary_rejects_wrong_or_incomplete_adapter_evidence(snapshot, message):
    start, end = _history_window()

    class Daily:
        pass

    daily = Daily()
    daily.snapshot = snapshot
    with pytest.raises(BrokerHistoryUnavailable, match=message):
        validated_daily_history_window(
            provider_name="fyers",
            account_id="acct-1",
            window_start=start,
            window_end=end,
            daily_history=daily,
            provider_window="TEST",
        )


def test_provider_boundary_accepts_only_matching_complete_snapshot():
    start, end = _history_window()

    class Daily:
        snapshot = _history_snapshot()

    result = validated_daily_history_window(
        provider_name="FYERS",
        account_id="acct-1",
        window_start=start,
        window_end=end,
        daily_history=Daily(),
        provider_window="TEST",
    )
    assert result.complete is True
    assert result.snapshot.account_id == "acct-1"
    assert result.snapshot.broker == "fyers"
    assert result.pages_fetched == 1


def test_history_window_rejects_cursor_or_pagination_claimed_as_complete():
    start, end = _history_window()
    with pytest.raises(ValueError, match="paginated response"):
        BrokerHistoryWindow(
            snapshot=_history_snapshot(),
            window_start=start,
            window_end=end,
            pages_fetched=1,
            has_more=True,
            next_cursor="page-2",
        )

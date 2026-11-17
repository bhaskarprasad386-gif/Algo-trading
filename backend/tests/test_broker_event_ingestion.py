from types import SimpleNamespace

import pytest

from app.execution import broker_event_ingestion as ingestion
from app.execution.broker_events import BrokerOrderEvent, BrokerOrderEventStatus


def _event(**changes):
    values = {
        "broker": "angel_one",
        "account_id": "acct-123",
        "event_id": "evt-1",
        "broker_order_id": "order-1",
        "status": BrokerOrderEventStatus.OPEN,
        "occurred_at_ns": 100,
    }
    values.update(changes)
    return BrokerOrderEvent(**values)


@pytest.mark.parametrize(
    "broker",
    ["angel_one", "zerodha", "fyers", "shoonya", "mstock"],
)
def test_authenticated_adapter_identity_is_accepted_for_each_supported_broker(
    monkeypatch, broker
):
    adapter = SimpleNamespace(name=broker, account_id="acct-123", connected=True)
    calls = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, event: calls.append((db, event)) or {"created": True},
    )
    result = ingestion.ingest_broker_order_event(
        object(), adapter=adapter, event=_event(broker=broker),
    )
    assert result == {"created": True}
    assert len(calls) == 1


def test_broker_name_comparison_is_case_normalized_but_persisted_event_is_unchanged(
    monkeypatch,
):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    event = _event(broker="ANGEL_ONE")
    seen = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, value: seen.append(value) or {"created": True},
    )
    ingestion.ingest_broker_order_event(object(), adapter=adapter, event=event)
    assert seen == [event]


@pytest.mark.parametrize(
    "adapter,event,match",
    [
        (SimpleNamespace(name="zerodha", account_id="acct-123", connected=True),
         _event(broker="angel_one"), "broker"),
        (SimpleNamespace(name="angel_one", account_id="acct-999", connected=True),
         _event(), "account"),
        (SimpleNamespace(name="angel_one", account_id="acct-123", connected=False),
         _event(), "not connected"),
        (SimpleNamespace(name="angel_one", account_id=None, connected=True),
         _event(), "identity is unavailable"),
    ],
)
def test_mismatched_or_unavailable_session_identity_fails_closed(
    monkeypatch, adapter, event, match
):
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda *args, **kwargs: pytest.fail("persistence must not be called"),
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda *args, **kwargs: pytest.fail("persistence must not be called"),
    )
    with pytest.raises(ingestion.BrokerEventIngestionBlocked, match=match):
        ingestion.ingest_broker_order_event(object(), adapter=adapter, event=event)


@pytest.mark.parametrize(
    "status,metadata",
    [
        (BrokerOrderEventStatus.PARTIALLY_FILLED, {
            "cumulative_filled_quantity": 2, "fill_id": "trade-1",
            "fill_quantity": 2, "average_fill_price": 101.5,
        }),
        (BrokerOrderEventStatus.FILLED, {
            "cumulative_filled_quantity": 10, "fill_id": "trade-2",
            "fill_quantity": 10, "average_fill_price": 102.0,
        }),
    ],
)
def test_fill_events_use_atomic_event_and_fill_persistence(monkeypatch, status, metadata):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    calls = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda db, event: calls.append(event) or {"event_created": True, "fill_created": True},
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda *args, **kwargs: pytest.fail("fill must use atomic persistence"),
    )
    result = ingestion.ingest_broker_order_event(
        object(), adapter=adapter, event=_event(status=status, **metadata),
    )
    assert result["fill_created"] is True
    assert len(calls) == 1


def test_status_only_event_never_calls_fill_persistence(monkeypatch):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    calls = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, event: calls.append(event) or {"created": True},
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda *args, **kwargs: pytest.fail("status-only event must not enter fill path"),
    )
    result = ingestion.ingest_broker_order_event(object(), adapter=adapter, event=_event())
    assert result["created"] is True
    assert len(calls) == 1


def test_partial_fill_without_trade_identity_is_status_evidence_only(monkeypatch):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    seen = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, event: seen.append(event) or {"created": True},
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda *args, **kwargs: pytest.fail("must not mutate fill ledger without trade identity"),
    )
    event = _event(
        status=BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=1,
    )
    result = ingestion.ingest_broker_order_event(object(), adapter=adapter, event=event)
    assert result["created"] is True
    assert seen == [event]



def test_partial_fill_status_without_trade_identity_is_evidence_only(monkeypatch):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    calls = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, event: calls.append(("event", event)) or {"created": True},
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda *args, **kwargs: pytest.fail("no trade identity means no fill mutation"),
    )
    event = _event(
        status=BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=3,
    )
    result = ingestion.ingest_broker_order_event(
        object(), adapter=adapter, event=event,
    )
    assert result["created"] is True
    assert calls == [("event", event)]


@pytest.mark.parametrize(
    "metadata",
    [
        {"fill_id": "trade-price-pending", "fill_quantity": 2},
        {"average_fill_price": 101.5},
    ],
)
def test_incomplete_fill_metadata_is_persisted_as_evidence_only(monkeypatch, metadata):
    adapter = SimpleNamespace(name="angel_one", account_id="acct-123", connected=True)
    event = _event(
        status=BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=2,
        **metadata,
    )
    calls = []
    monkeypatch.setattr(
        ingestion, "record_broker_order_event",
        lambda db, value: calls.append(value) or {"created": True},
    )
    monkeypatch.setattr(
        ingestion, "record_broker_order_event_with_fill",
        lambda *args, **kwargs: pytest.fail("incomplete evidence must not mutate fills"),
    )

    result = ingestion.ingest_broker_order_event(object(), adapter=adapter, event=event)

    assert result == {"created": True}
    assert calls == [event]

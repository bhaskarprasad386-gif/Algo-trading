from queue import Empty

import pytest

from app.market_data.provider_ingress import (
    BoundedProviderIngress,
    ProviderIngressEnvelope,
    ProviderIngressWorker,
)


def envelope(*, provider="angelone", generation=1, token="101", payload=None):
    return ProviderIngressEnvelope.capture(
        provider=provider,
        generation=generation,
        exchange="NFO",
        token=token,
        event_type="tick",
        payload=payload or {"ltp": 100},
        provider_timestamp_ns=1_800_000_000_000_000_000,
    )


def test_ingress_requires_activation_and_accepts_current_generation():
    ingress = BoundedProviderIngress(provider="angelone", capacity=2)
    ingress.activate_generation(1)
    item = envelope()
    assert ingress.publish(item) is True
    assert ingress.get_nowait if False else True
    assert ingress.get(timeout=0.01) is item
    ingress.task_done()
    assert ingress.snapshot()["accepted"] == 1


def test_old_generation_is_rejected_after_reconnect():
    ingress = BoundedProviderIngress(provider="shoonya", capacity=4)
    ingress.activate_generation(1)
    old = envelope(provider="shoonya", generation=1)
    ingress.activate_generation(2)
    assert ingress.publish(old) is False
    assert ingress.publish(envelope(provider="shoonya", generation=2)) is True
    assert ingress.snapshot()["stale_generation"] == 1


def test_queue_overflow_is_explicit_and_latches_integrity_fault():
    ingress = BoundedProviderIngress(provider="angelone", capacity=1)
    ingress.activate_generation(1)
    assert ingress.publish(envelope()) is True
    assert ingress.publish(envelope(token="102")) is False
    state = ingress.snapshot()
    assert state["overflow"] == 1
    assert state["integrity_fault"] is True
    assert state["queue_depth"] == 1


def test_wrong_provider_and_closed_ingress_are_rejected():
    ingress = BoundedProviderIngress(provider="angelone", capacity=2)
    ingress.activate_generation(1)
    assert ingress.publish(envelope(provider="shoonya")) is False
    ingress.close()
    assert ingress.publish(envelope()) is False
    assert ingress.snapshot()["wrong_provider"] == 1
    assert ingress.snapshot()["closed"] is True


def test_envelope_copies_payload_mapping():
    source = {"ltp": 100}
    item = envelope(payload=source)
    source["ltp"] = 999
    assert item.payload["ltp"] == 100
    with pytest.raises(TypeError):
        item.payload["ltp"] = 1


def test_market_and_order_lanes_have_independent_capacity():
    market = BoundedProviderIngress(provider="shoonya", capacity=1, event_class="market")
    orders = BoundedProviderIngress(provider="shoonya", capacity=2, event_class="order")
    market.activate_generation(1)
    orders.activate_generation(1)
    assert market.publish(envelope(provider="shoonya")) is True
    assert market.publish(envelope(provider="shoonya", token="102")) is False
    assert orders.publish(envelope(provider="shoonya", token="order-1")) is True
    assert orders.snapshot()["overflow"] == 0
    assert market.snapshot()["integrity_fault"] is True


def test_generation_must_move_forward():
    ingress = BoundedProviderIngress(provider="angelone", capacity=1)
    ingress.activate_generation(2)
    with pytest.raises(ValueError):
        ingress.activate_generation(2)
    with pytest.raises(ValueError):
        ingress.activate_generation(1)



def test_queued_old_generation_is_discarded_after_reconnect():
    ingress = BoundedProviderIngress(provider="angelone", capacity=3)
    ingress.activate_generation(1)
    assert ingress.publish(envelope(generation=1, token="old")) is True
    ingress.activate_generation(2)
    assert ingress.publish(envelope(generation=2, token="new")) is True
    current = ingress.get_current(timeout=0.01)
    assert current.token == "new"
    ingress.task_done()
    assert ingress.snapshot()["stale_dequeued"] == 1


def test_nested_payload_is_detached_and_immutable():
    source = {"depth": {"bids": [{"price": 100}]}}
    item = envelope(payload=source)
    source["depth"]["bids"][0]["price"] = 1
    assert item.payload["depth"]["bids"][0]["price"] == 100
    with pytest.raises(TypeError):
        item.payload["depth"]["bids"][0]["price"] = 2


def test_worker_drains_accepted_current_generation_frames_on_shutdown():
    ingress = BoundedProviderIngress(provider="angelone", capacity=4)
    ingress.activate_generation(1)
    seen = []
    for token in ("a", "b", "c"):
        assert ingress.publish(envelope(token=token)) is True
    worker = ProviderIngressWorker(ingress, lambda item: seen.append(item.token))
    worker.start()
    worker.stop(timeout=2.0, drain=True)
    assert seen == ["a", "b", "c"]
    assert worker.snapshot()["processed"] == 3
    assert worker.snapshot()["running"] is False


def test_worker_counts_handler_errors_and_continues_processing():
    ingress = BoundedProviderIngress(provider="shoonya", capacity=3)
    ingress.activate_generation(1)
    for token in ("bad", "good"):
        assert ingress.publish(envelope(provider="shoonya", token=token)) is True
    seen = []

    def handler(item):
        if item.token == "bad":
            raise RuntimeError("test failure")
        seen.append(item.token)

    worker = ProviderIngressWorker(ingress, handler)
    worker.start()
    worker.stop(timeout=2.0, drain=True)
    assert seen == ["good"]
    assert worker.snapshot()["handler_errors"] == 1
    assert worker.snapshot()["processed"] == 1


def test_non_draining_shutdown_reports_discarded_pending_frames():
    ingress = BoundedProviderIngress(provider="angelone", capacity=3)
    ingress.activate_generation(1)
    assert ingress.publish(envelope(token="pending")) is True
    worker = ProviderIngressWorker(ingress, lambda item: None)
    worker.stop(timeout=1.0, drain=False)
    assert ingress.snapshot()["shutdown_discarded"] == 1
    assert ingress.snapshot()["queue_depth"] == 0

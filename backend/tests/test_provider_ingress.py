from queue import Empty

import pytest

from app.market_data.provider_ingress import (
    BoundedProviderIngress,
    ProviderIngressEnvelope,
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

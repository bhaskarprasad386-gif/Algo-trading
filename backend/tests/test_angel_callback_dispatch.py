import pytest

from app.market_data.angel_callback_dispatch import (
    AngelShardCallbackAdapter,
    CallbackInvocation,
)


def test_per_shard_adapter_preserves_batch_and_callback_fifo_order():
    adapter = AngelShardCallbackAdapter(capacity=4)
    adapter.activate_generation(1)
    seen = []
    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(lambda value: seen.append(("raw", value)), "a", "raw"),
            CallbackInvocation(lambda value: seen.append(("record", value)), "a", "record"),
        ),
    )
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda value: seen.append(("raw", value)), "b", "raw"),),
    )
    adapter.stop(timeout=2.0, drain=True)
    assert seen == [("raw", "a"), ("record", "a"), ("raw", "b")]
    assert adapter.snapshot()["processed_batches"] == 2


def test_per_shard_adapter_rejects_retired_generation():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    adapter.activate_generation(2)
    assert not adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "raw"),),
    )
    assert adapter.snapshot()["stale_batches"] == 1


def test_per_shard_adapter_overflow_latches_integrity_fault():
    adapter = AngelShardCallbackAdapter(capacity=1)
    adapter.activate_generation(1)
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "raw"),),
    )
    assert not adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "raw"),),
    )
    state = adapter.snapshot()
    assert state["overflow_batches"] == 1
    assert state["integrity_fault"] is True


def test_per_shard_adapter_isolates_callback_exception():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    seen = []

    def fail(_):
        raise RuntimeError("consumer failed")

    adapter.start()
    adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(fail, None, "raw"),
            CallbackInvocation(lambda value: seen.append(value), "ok", "record"),
        ),
    )
    adapter.stop(timeout=2.0, drain=True)
    assert seen == ["ok"]
    assert adapter.snapshot()["callback_errors"] == 1


def test_per_shard_adapter_stop_rejects_new_publish_and_drains():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    seen = []
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, 1, "raw"),),
    )
    adapter.start()
    adapter.stop(timeout=2.0, drain=True)
    assert seen == [1]
    assert not adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, 2, "raw"),),
    )
    assert adapter.snapshot()["closed"] is True


def test_adapter_detaches_mutable_payload_before_worker_delivery():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    payload = {"depth": {"bids": [{"price": 100}]}}
    seen = []
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, payload, "raw"),),
    )
    payload["depth"]["bids"][0]["price"] = 1
    adapter.start()
    adapter.stop(timeout=2.0, drain=True)
    assert seen[0]["depth"]["bids"][0]["price"] == 100

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
    assert state["queue_high_watermark"] == 1
    assert state["integrity_fault"] is True
    assert state["closed"] is True
    assert state["queue_depth"] == 0
    assert state["discarded_batches"] == 1
    assert not adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "raw"),),
    )


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



def test_non_draining_stop_counts_discarded_batches():
    adapter = AngelShardCallbackAdapter(capacity=3)
    adapter.activate_generation(1)
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, 1, "raw"),),
    )
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, 2, "raw"),),
    )
    adapter.stop(timeout=2.0, drain=False)
    assert adapter.snapshot()["discarded_batches"] == 2


def test_payload_copy_failure_is_reported_as_integrity_fault():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise TypeError("cannot detach payload")

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    accepted = adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, Uncopyable(), "raw"),),
    )
    state = adapter.snapshot()
    assert accepted is False
    assert state["payload_copy_errors"] == 1
    assert state["integrity_fault"] is True
    assert state["closed"] is True
    assert state["queue_depth"] == 0
    assert "payload_copy: TypeError" in state["last_error"]



def test_delivery_counters_distinguish_attempts_successes_and_errors():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)

    def fail(_):
        raise RuntimeError("consumer unavailable")

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(lambda _: None, "ok", "raw"),
            CallbackInvocation(fail, "bad", "record"),
        ),
    )
    adapter.stop(timeout=2.0, drain=True)
    state = adapter.snapshot()
    assert state["callback_attempts"] == 2
    assert state["callback_successes"] == 1
    assert state["callback_errors"] == 1
    assert state["delivery_status"] == "DEGRADED"


def test_overflow_marks_delivery_status_degraded():
    adapter = AngelShardCallbackAdapter(capacity=1)
    adapter.activate_generation(1)
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, 1, "raw"),),
    )
    accepted = adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, 2, "raw"),),
    )
    assert accepted is False
    state = adapter.snapshot()
    assert state["delivery_status"] == "DEGRADED"
    assert state["integrity_fault"] is True


def test_graceful_stop_without_worker_counts_unprocessed_batches():
    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, 1, "raw"),),
    )
    adapter.stop(timeout=2.0, drain=True)
    state = adapter.snapshot()
    assert state["discarded_batches"] == 1
    assert state["queue_depth"] == 0



def test_overflow_retires_generation_and_discards_queued_work_while_inflight_finishes():
    from threading import Event

    adapter = AngelShardCallbackAdapter(capacity=1)
    adapter.activate_generation(1)
    entered = Event()
    release = Event()
    seen = []

    def blocked(value):
        entered.set()
        release.wait(timeout=2.0)
        seen.append(value)

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(blocked, "inflight", "raw"),),
    )
    assert entered.wait(timeout=1.0)
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, "queued", "raw"),),
    )
    assert not adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, "overflow", "raw"),),
    )
    state = adapter.snapshot()
    assert state["closed"] is True
    assert state["queue_depth"] == 0
    assert state["discarded_batches"] == 1
    assert state["delivery_status"] == "DEGRADED"

    release.set()
    adapter.stop(timeout=2.0, drain=True)
    assert seen == ["inflight"]



def test_retirement_fences_remaining_callbacks_in_already_dequeued_batch():
    from threading import Event, Thread

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    entered = Event()
    release = Event()
    seen = []

    def blocked(value):
        entered.set()
        release.wait(timeout=2.0)
        seen.append(value)

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(blocked, "inflight", "raw"),
            CallbackInvocation(seen.append, "must-not-run-after-retire", "record"),
        ),
    )
    assert entered.wait(timeout=1.0)

    # stop invalidates the generation even though one consumer callback is
    # already executing and cannot be cancelled safely.
    with pytest.raises(TimeoutError):
        adapter.stop(timeout=0.05, drain=False)
    release.set()
    worker = adapter._thread
    assert worker is not None
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert seen == ["inflight"]
    assert adapter.snapshot()["callback_attempts"] == 1
    assert adapter.snapshot()["closed"] is True



def test_graceful_stop_timeout_fences_remaining_invocations_in_active_batch():
    from threading import Event

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    entered = Event()
    release = Event()
    seen = []

    def blocked(value):
        entered.set()
        release.wait(timeout=2.0)
        seen.append(value)

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(blocked, "inflight", "raw"),
            CallbackInvocation(seen.append, "must-not-run-after-timeout", "record"),
        ),
    )
    assert entered.wait(timeout=1.0)

    with pytest.raises(TimeoutError, match="shutdown budget"):
        adapter.stop(timeout=0.05, drain=True)

    state = adapter.snapshot()
    assert state["closed"] is True
    assert state["generation"] == 2
    assert state["callback_attempts"] == 1

    release.set()
    worker = adapter._thread
    assert worker is not None
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert seen == ["inflight"]
    assert adapter.snapshot()["callback_attempts"] == 1



def test_publish_revalidates_generation_after_payload_copy_races_retirement():
    from threading import Event, Thread

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    copy_entered = Event()
    release_copy = Event()
    seen = []

    class BlockingCopy:
        def __deepcopy__(self, memo):
            copy_entered.set()
            assert release_copy.wait(timeout=1.0)
            return {"detached": True}

    result = []
    publisher = Thread(target=lambda: result.append(adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(seen.append, BlockingCopy(), "raw"),),
    )))
    publisher.start()
    assert copy_entered.wait(timeout=1.0)

    # Retirement while payload detachment is outside the adapter lock must
    # prevent that old generation from publishing after the copy completes.
    adapter.stop(timeout=1.0, drain=False)
    release_copy.set()
    publisher.join(timeout=1.0)

    assert not publisher.is_alive()
    assert result == [False]
    state = adapter.snapshot()
    assert state["queue_depth"] == 0
    assert state["accepted_batches"] == 0
    assert state["stale_batches"] == 1
    assert seen == []


def test_worker_can_retire_itself_without_joining_its_own_thread():
    from threading import Event

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    entered = Event()
    seen = []

    def shutdown_from_callback(_value):
        entered.set()
        adapter.stop(timeout=1.0, drain=False)
        seen.append("current-callback-finished")

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(shutdown_from_callback, None, "shutdown"),
            CallbackInvocation(lambda _: seen.append("must-not-run"), None, "later"),
        ),
    )
    assert entered.wait(timeout=1.0)
    worker = adapter._thread
    assert worker is not None
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert seen == ["current-callback-finished"]
    state = adapter.snapshot()
    assert state["closed"] is True
    assert state["generation"] == 2
    assert state["callback_attempts"] == 1
    assert state["queue_depth"] == 0

def test_repeated_worker_self_stop_is_idempotent():
    from threading import Event

    adapter = AngelShardCallbackAdapter(capacity=2)
    adapter.activate_generation(1)
    finished = Event()

    def repeated_shutdown(_value):
        adapter.stop(timeout=1.0, drain=False)
        adapter.stop(timeout=1.0, drain=False)
        finished.set()

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(repeated_shutdown, None, "shutdown"),),
    )
    assert finished.wait(timeout=1.0)
    worker = adapter._thread
    assert worker is not None
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    state = adapter.snapshot()
    assert state["generation"] == 2
    assert state["callback_attempts"] == 1
    assert state["queue_depth"] == 0
    assert state["closed"] is True



def test_callback_dispatch_records_latency_and_attributes_slow_consumer_kind():
    from time import sleep

    adapter = AngelShardCallbackAdapter(capacity=2, slow_callback_seconds=0.01)
    adapter.activate_generation(1)
    seen = []
    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(
            CallbackInvocation(lambda value: (sleep(0.02), seen.append(value)), "slow", "normalized_record"),
            CallbackInvocation(seen.append, "fast", "raw_tick"),
        ),
    )
    adapter.stop(timeout=2.0, drain=True)

    state = adapter.snapshot()
    assert seen == ["slow", "fast"]
    assert state["callback_attempts"] == 2
    assert state["callback_successes"] == 2
    assert state["slow_callback_count"] == 1
    assert state["callback_duration_max_ns"] >= 10_000_000
    assert state["queue_wait_max_ns"] >= 0
    assert state["callback_kind_metrics"]["normalized_record"]["slow"] == 1
    assert state["callback_kind_metrics"]["raw_tick"]["slow"] == 0
    assert state["callback_kind_metrics"]["normalized_record"]["queue_wait_total_ns"] >= 0
    assert state["callback_kind_metrics"]["normalized_record"]["queue_wait_max_ns"] >= 0
    assert state["callback_kind_metrics"]["raw_tick"]["queue_wait_total_ns"] >= 0
    assert state["callback_kind_metrics"]["raw_tick"]["queue_wait_max_ns"] >= 0


def test_callback_dispatch_metrics_are_monotonic_and_queue_high_water_is_bounded():
    from threading import Event

    adapter = AngelShardCallbackAdapter(capacity=2, slow_callback_seconds=0.01)
    adapter.activate_generation(1)
    entered = Event()
    release = Event()

    def blocked(_value):
        entered.set()
        release.wait(timeout=1.0)

    adapter.start()
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(blocked, None, "normalized_record"),),
    )
    assert entered.wait(timeout=1.0)
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "normalized_record"),),
    )
    assert adapter.publish(
        generation=1,
        invocations=(CallbackInvocation(lambda _: None, None, "normalized_record"),),
    )
    state = adapter.snapshot()
    assert state["queue_depth"] == 2
    assert state["queue_high_watermark"] == 2
    assert state["queue_high_watermark"] <= state["queue_capacity"]

    release.set()
    adapter.stop(timeout=2.0, drain=True)
    final = adapter.snapshot()
    assert final["callback_attempts"] == 3
    assert final["callback_duration_total_ns"] >= final["callback_duration_max_ns"]
    assert final["queue_wait_total_ns"] >= final["queue_wait_max_ns"]
    # The second batch waited behind the deliberately blocked first callback;
    # its consumer kind must receive that queue-wait attribution as well.
    assert final["callback_kind_metrics"]["normalized_record"]["queue_wait_max_ns"] > 0

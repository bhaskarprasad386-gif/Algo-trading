from threading import Event

from app.market_data.shared_index_control_worker import SharedIndexControlWorker


class Record:
    def __init__(self, timestamp_ns):
        self.timestamp_ns = timestamp_ns


def test_control_worker_coalesces_to_latest_record_per_locked_underlying():
    from threading import Event

    delivered = []
    entered, release, second_batch = Event(), Event(), Event()

    def consume(batch, generation):
        delivered.append((batch, generation))
        if len(delivered) == 1:
            entered.set()
            assert release.wait(2)
        else:
            second_batch.set()

    worker = SharedIndexControlWorker(consume)
    worker.start(7)
    try:
        assert worker.submit("NIFTY", Record(10), 7)
        assert entered.wait(2)
        assert worker.submit("NIFTY", Record(11), 7)
        assert worker.submit("BANKNIFTY", Record(12), 7)
        assert worker.submit("NIFTY", Record(13), 7)
        release.set()
        assert second_batch.wait(2)
        batch, generation = delivered[1]
        assert generation == 7
        assert set(batch) == {"NIFTY", "BANKNIFTY"}
        assert batch["NIFTY"].timestamp_ns == 13
        assert batch["BANKNIFTY"].timestamp_ns == 12
        snapshot = worker.snapshot()
        assert snapshot["mailbox_capacity"] == 2
        assert snapshot["pending_records"] == 0
        assert snapshot["coalesced_updates"] >= 1
    finally:
        release.set()
        assert worker.stop()


def test_control_worker_rejects_old_generation_and_unknown_underlyings():
    delivered = []
    ready = Event()
    worker = SharedIndexControlWorker(lambda batch, generation: (delivered.append((batch, generation)), ready.set()))
    worker.start(3)
    try:
        assert worker.submit("NIFTY", Record(10), 2) is False
        assert worker.submit("SENSEX", Record(10), 3) is False
        assert ready.wait(0.05) is False
        assert delivered == []
    finally:
        assert worker.stop()

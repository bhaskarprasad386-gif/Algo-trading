import sqlite3

from app.backtesting.atomic_replay import AtomicReplayStore
from app.backtesting.resumable_high_resolution import ResumableHighResolutionRunner
from app.backtesting.universal import MarketEvent


class FailOnFourth:
    def __init__(self):
        self.seen = 0

    def on_event(self, event):
        self.seen += 1
        if self.seen == 4:
            raise RuntimeError("batch failure")
        return None

    def snapshot_state(self):
        return {"seen": self.seen}

    def restore_state(self, state):
        self.seen = int(state["seen"])


def test_batch_commit_and_failure_rolls_back_only_open_batch(tmp_path):
    db = tmp_path / "batch.db"
    conn = sqlite3.connect(db)
    events = [MarketEvent(i, "NIFTY", i) for i in range(1, 7)]

    strategy = FailOnFourth()
    runner = ResumableHighResolutionRunner(conn, "batch", batch_size=3)
    try:
        runner.run(events, strategy)
    except RuntimeError as exc:
        assert str(exc) == "batch failure"
    else:
        raise AssertionError("expected batch failure")

    store = AtomicReplayStore(conn)
    checkpoint = store.load_checkpoint("batch")
    assert checkpoint is not None
    assert checkpoint.timestamp_ns == 3
    assert checkpoint.sequence == 3
    assert checkpoint.processed_events == 3
    assert store.count_events("batch") == 3
    assert strategy.seen == 3
    conn.close()


def test_batch_resume_does_not_reprocess_committed_events(tmp_path):
    db = tmp_path / "resume.db"
    conn = sqlite3.connect(db)

    class Counter:
        def __init__(self):
            self.seen = []

        def on_event(self, event):
            self.seen.append(event.timestamp_ns)
            return None

        def snapshot_state(self):
            return {"seen": self.seen}

        def restore_state(self, state):
            self.seen = list(state["seen"])

    first = Counter()
    ResumableHighResolutionRunner(conn, "resume", batch_size=2).run(
        [MarketEvent(1, "NIFTY", 1), MarketEvent(2, "NIFTY", 2), MarketEvent(3, "NIFTY", 3)],
        first,
    )
    assert first.seen == [1, 2, 3]

    second = Counter()
    result = ResumableHighResolutionRunner(conn, "resume", batch_size=2).run(
        [MarketEvent(1, "NIFTY", 1), MarketEvent(2, "NIFTY", 2), MarketEvent(3, "NIFTY", 3), MarketEvent(4, "NIFTY", 4)],
        second,
    )
    # Checkpoint state is restored before replay, so strategy-local state is preserved;
    # only the new event is delivered after the committed checkpoint.
    assert second.seen == [1, 2, 3, 4]
    assert result.events_processed == 4
    assert AtomicReplayStore(conn).count_events("resume") == 4
    conn.close()

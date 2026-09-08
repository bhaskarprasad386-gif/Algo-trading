"""Opt-in 1M-event scalability benchmark for the streaming replay path.

This benchmark deliberately generates events lazily and reports bounded-memory
and durable-store metrics without materializing the event stream or trade list.
Run from the backend directory with the project's Python environment.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
import tracemalloc
from dataclasses import dataclass

from app.backtesting.atomic_replay import AtomicReplayStore
from app.backtesting.resumable_high_resolution import ResumableHighResolutionRunner
from app.backtesting.universal import MarketEvent


@dataclass
class CounterStrategy:
    seen: int = 0

    def on_event(self, event: MarketEvent):
        self.seen += 1
        return None

    def snapshot_state(self):
        return {"seen": self.seen}

    def restore_state(self, state):
        self.seen = int(state.get("seen", 0))


def events(count: int):
    for i in range(count):
        yield MarketEvent(
            timestamp_ns=i + 1,
            sequence=i,
            instrument="BENCH",
            event_type="tick",
            payload={"price": 100.0 + (i % 10) * 0.01},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    args = parser.parse_args()
    if args.events <= 0 or args.batch_size <= 0:
        raise SystemExit("--events and --batch-size must be positive")

    with tempfile.TemporaryDirectory(prefix="algo-trading-bench-") as tmp:
        db_path = os.path.join(tmp, "benchmark.sqlite3")
        strategy = CounterStrategy()
        store = AtomicReplayStore(db_path)
        runner = ResumableHighResolutionRunner(
            store=store,
            run_id="benchmark-1m",
            batch_size=args.batch_size,
        )

        tracemalloc.start()
        started = time.perf_counter()
        result = runner.run(strategy, events(args.events))
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        db_bytes = os.path.getsize(db_path)
        persisted_events = store.count_events("benchmark-1m")
        persisted_trades = store.count_trades("benchmark-1m")
        rate = result.processed_events / elapsed if elapsed else 0.0

        print(f"events={result.processed_events}")
        print(f"strategy_seen={strategy.seen}")
        print(f"persisted_events={persisted_events}")
        print(f"persisted_trades={persisted_trades}")
        print(f"elapsed_seconds={elapsed:.3f}")
        print(f"events_per_second={rate:.1f}")
        print(f"peak_python_mb={peak / (1024 * 1024):.2f}")
        print(f"sqlite_mb={db_bytes / (1024 * 1024):.2f}")

        if result.processed_events != args.events or strategy.seen != args.events:
            raise SystemExit("processed event count mismatch")
        if persisted_events != args.events:
            raise SystemExit("durable event count mismatch")
        if persisted_trades != 0:
            raise SystemExit("unexpected trades in counter benchmark")


if __name__ == "__main__":
    main()

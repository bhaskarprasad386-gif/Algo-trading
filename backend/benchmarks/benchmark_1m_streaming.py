"""Opt-in 1M-event scalability benchmark for the streaming replay path.

The benchmark generates events lazily and reports throughput, Python peak
memory, and durable SQLite size without materializing the event stream or
loading the complete trade history.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
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
            context={"price": 100.0 + (i % 10) * 0.01},
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
        connection = sqlite3.connect(db_path)
        store = AtomicReplayStore(connection)
        runner = ResumableHighResolutionRunner(
            connection=connection,
            run_id="benchmark-1m",
            batch_size=args.batch_size,
        )

        tracemalloc.start()
        started = time.perf_counter()
        result = runner.run(events(args.events), strategy)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Query durable metrics before closing the connection owned by the store.
        persisted_events = store.count_events("benchmark-1m")
        persisted_trades = store.count_trades("benchmark-1m")
        db_bytes = os.path.getsize(db_path)
        rate = result.events_processed / elapsed if elapsed else 0.0
        connection.close()

        print(f"events={result.events_processed}")
        print(f"strategy_seen={strategy.seen}")
        print(f"persisted_events={persisted_events}")
        print(f"persisted_trades={persisted_trades}")
        print(f"elapsed_seconds={elapsed:.3f}")
        print(f"events_per_second={rate:.1f}")
        print(f"peak_python_mb={peak / (1024 * 1024):.2f}")
        print(f"sqlite_mb={db_bytes / (1024 * 1024):.2f}")

        if result.events_processed != args.events or strategy.seen != args.events:
            raise SystemExit("processed event count mismatch")
        if persisted_events != args.events:
            raise SystemExit("durable event count mismatch")
        if persisted_trades != 0:
            raise SystemExit("unexpected trades in counter benchmark")


if __name__ == "__main__":
    main()

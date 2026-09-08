"""1M-event streaming replay benchmark."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

# The workflow executes this file from backend/benchmarks, so make the
# backend package importable without requiring PYTHONPATH configuration.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.backtesting.resumable_high_resolution import ResumableHighResolutionRunner
from app.backtesting.universal import MarketEvent


class CounterStrategy:
    def __init__(self) -> None:
        self.seen = 0

    def on_event(self, event: MarketEvent):
        self.seen += 1
        return None


def event_stream(count: int):
    for i in range(count):
        yield MarketEvent(
            timestamp_ns=i + 1,
            sequence=i,
            instrument="BENCH",
            data=(("price", 100.0), ("i", i)),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    args = parser.parse_args()
    if args.events < 1 or args.batch_size < 1:
        raise SystemExit("events and batch-size must be positive")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "benchmark.sqlite3"
        conn = sqlite3.connect(db_path)
        strategy = CounterStrategy()
        runner = ResumableHighResolutionRunner(conn, "benchmark-1m", batch_size=args.batch_size)
        tracemalloc.start()
        started = time.perf_counter()
        result = runner.run(event_stream(args.events), strategy)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        persisted_events = runner.store.count_events("benchmark-1m")
        persisted_trades = runner.store.count_trades("benchmark-1m")
        conn.commit()
        sqlite_bytes = db_path.stat().st_size
        conn.close()

    rate = args.events / elapsed if elapsed else float("inf")
    print(f"events={result.events_processed}")
    print(f"strategy_seen={strategy.seen}")
    print(f"persisted_events={persisted_events}")
    print(f"persisted_trades={persisted_trades}")
    print(f"elapsed_seconds={elapsed:.6f}")
    print(f"events_per_second={rate:.2f}")
    print(f"peak_python_mb={peak / (1024 * 1024):.2f}")
    print(f"sqlite_mb={sqlite_bytes / (1024 * 1024):.2f}")
    if result.events_processed != args.events or strategy.seen != args.events:
        raise SystemExit("benchmark event count mismatch")
    if persisted_events != args.events or persisted_trades != 0:
        raise SystemExit("benchmark durable count mismatch")


if __name__ == "__main__":
    main()

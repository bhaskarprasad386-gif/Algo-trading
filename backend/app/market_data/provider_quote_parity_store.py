"""Durable, read-only SQLite store for shadow quote-parity diagnostics.

Only derived parity metrics and quality metadata are persisted. No raw ticks,
credentials, selected quotes, or execution instructions are stored.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from threading import RLock

from .provider_quote_parity import QuoteParityMetrics


_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_quote_parity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc_ns INTEGER NOT NULL CHECK(observed_at_utc_ns > 0),
    contract_key TEXT NOT NULL,
    left_provider TEXT NOT NULL,
    right_provider TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    identity_match INTEGER NOT NULL CHECK(identity_match IN (0, 1)),
    left_valid INTEGER NOT NULL CHECK(left_valid IN (0, 1)),
    left_fresh INTEGER NOT NULL CHECK(left_fresh IN (0, 1)),
    right_valid INTEGER NOT NULL CHECK(right_valid IN (0, 1)),
    right_fresh INTEGER NOT NULL CHECK(right_fresh IN (0, 1)),
    metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_provider_quote_parity_contract_time
    ON provider_quote_parity(contract_key, observed_at_utc_ns DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_provider_quote_parity_time
    ON provider_quote_parity(observed_at_utc_ns);
"""


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class SQLiteQuoteParityStore:
    """Thread-safe SQLite persistence for derived parity diagnostics."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if not self.database_path:
            raise ValueError("database_path is required")
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path, check_same_thread=False, timeout=10.0
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout=10000")
            if self.database_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    @staticmethod
    def _contract_key(metrics: QuoteParityMetrics) -> str:
        key = metrics.contract
        return json.dumps({
            "exchange": key.exchange,
            "segment": key.segment,
            "underlying": key.underlying,
            "instrument_type": key.instrument_type,
            "expiry": key.expiry.isoformat() if key.expiry else None,
            "strike": str(key.strike) if key.strike is not None else None,
            "option_type": key.option_type,
        }, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _payload(metrics: QuoteParityMetrics) -> str:
        return json.dumps({
            "bid_abs_diff": _decimal(metrics.bid_abs_diff),
            "ask_abs_diff": _decimal(metrics.ask_abs_diff),
            "bid_relative_diff_bps": _decimal(metrics.bid_relative_diff_bps),
            "ask_relative_diff_bps": _decimal(metrics.ask_relative_diff_bps),
            "bid_size_abs_diff": _decimal(metrics.bid_size_abs_diff),
            "ask_size_abs_diff": _decimal(metrics.ask_size_abs_diff),
            "mid_abs_diff": _decimal(metrics.mid_abs_diff),
            "mid_relative_diff_bps": _decimal(metrics.mid_relative_diff_bps),
            "exchange_timestamp_skew_ns": metrics.exchange_timestamp_skew_ns,
            "left_reasons": list(metrics.quality.left.reasons),
            "right_reasons": list(metrics.quality.right.reasons),
            "left_age_ns": metrics.quality.left.age_ns,
            "right_age_ns": metrics.quality.right.age_ns,
            "left_exchange_age_ns": metrics.quality.left.exchange_age_ns,
            "right_exchange_age_ns": metrics.quality.right.exchange_age_ns,
        }, sort_keys=True, separators=(",", ":"))

    def record(self, metrics: QuoteParityMetrics, *, observed_at_utc_ns: int) -> int:
        if not isinstance(metrics, QuoteParityMetrics):
            raise TypeError("metrics must be QuoteParityMetrics")
        if isinstance(observed_at_utc_ns, bool) or not isinstance(observed_at_utc_ns, int) or observed_at_utc_ns <= 0:
            raise ValueError("observed_at_utc_ns must be a positive integer")
        quality = metrics.quality
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO provider_quote_parity (
                    observed_at_utc_ns, contract_key, left_provider, right_provider,
                    quality_state, identity_match, left_valid, left_fresh,
                    right_valid, right_fresh, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observed_at_utc_ns, self._contract_key(metrics),
                    quality.left.provider, quality.right.provider, quality.state.value,
                    int(quality.identity_match), int(quality.left.valid),
                    int(quality.left.fresh), int(quality.right.valid),
                    int(quality.right.fresh), self._payload(metrics),
                ),
            )
            return int(cursor.lastrowid)

    def latest(self, *, contract_key: str, limit: int = 100) -> tuple[dict, ...]:
        if not isinstance(contract_key, str) or not contract_key:
            raise ValueError("contract_key is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, observed_at_utc_ns, contract_key, left_provider,
                          right_provider, quality_state, identity_match, left_valid,
                          left_fresh, right_valid, right_fresh, metrics_json
                   FROM provider_quote_parity
                   WHERE contract_key = ?
                   ORDER BY observed_at_utc_ns DESC, id DESC LIMIT ?""",
                (contract_key, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["identity_match"] = bool(item["identity_match"])
            for field in ("left_valid", "left_fresh", "right_valid", "right_fresh"):
                item[field] = bool(item[field])
            item["metrics"] = json.loads(item.pop("metrics_json"))
            result.append(item)
        return tuple(result)

    def prune_before(self, *, cutoff_utc_ns: int) -> int:
        if isinstance(cutoff_utc_ns, bool) or not isinstance(cutoff_utc_ns, int) or cutoff_utc_ns <= 0:
            raise ValueError("cutoff_utc_ns must be a positive integer")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM provider_quote_parity WHERE observed_at_utc_ns < ?",
                (cutoff_utc_ns,),
            )
            return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

"""Live Box Spread scanner -> durable snapshots, alerts and notifications."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import LiveBoxSpreadSnapshot, LiveBoxSpreadSnapshotKey
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.notifications.box_spread_alerts import BoxSpreadAlertService

logger = logging.getLogger(__name__)


def _spool_path():
    """Persistent local fallback for qualifying opportunities awaiting DB writes."""
    return Path(os.getenv("LIVE_BOX_SPREAD_SPOOL_PATH", "./box_spread_pending.jsonl"))


def _append_spool(rows):
    """Append and fsync pending snapshots before attempting database persistence."""
    if not rows:
        return
    path = _spool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_spool():
    path = _spool_path()
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _clear_spool():
    """Atomically replace the queue after a successful database commit."""
    path = _spool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)



def _snapshot_payload(result, observed_at):
    low, high = result.low, result.high
    return {
        "symbol": low.underlying,
        "instrument_class": low.instrument_class,
        "expiry": low.expiry,
        "expiry_date": str(low.expiry),
        "expiry_type": "UNKNOWN",
        "low_strike": low.strike,
        "high_strike": high.strike,
        "direction": result.direction,
        "observed_at": observed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp_ns": int(low.timestamp_ns),
        "high_timestamp_ns": int(high.timestamp_ns),
        "low_call_timestamp_ns": int(getattr(low, "call_timestamp_ns", None) or low.timestamp_ns),
        "low_put_timestamp_ns": int(getattr(low, "put_timestamp_ns", None) or low.timestamp_ns),
        "high_call_timestamp_ns": int(getattr(high, "call_timestamp_ns", None) or high.timestamp_ns),
        "high_put_timestamp_ns": int(getattr(high, "put_timestamp_ns", None) or high.timestamp_ns),
        "last_update_timestamp_ns": max(
            int(getattr(low, "call_timestamp_ns", None) or low.timestamp_ns),
            int(getattr(low, "put_timestamp_ns", None) or low.timestamp_ns),
            int(getattr(high, "call_timestamp_ns", None) or high.timestamp_ns),
            int(getattr(high, "put_timestamp_ns", None) or high.timestamp_ns),
        ),
        "timestamp_skew_ms": round((
            max(int(getattr(low, "call_timestamp_ns", None) or low.timestamp_ns),
                int(getattr(low, "put_timestamp_ns", None) or low.timestamp_ns),
                int(getattr(high, "call_timestamp_ns", None) or high.timestamp_ns),
                int(getattr(high, "put_timestamp_ns", None) or high.timestamp_ns))
            - min(int(getattr(low, "call_timestamp_ns", None) or low.timestamp_ns),
                  int(getattr(low, "put_timestamp_ns", None) or low.timestamp_ns),
                  int(getattr(high, "call_timestamp_ns", None) or high.timestamp_ns),
                  int(getattr(high, "put_timestamp_ns", None) or high.timestamp_ns))
        ) / 1_000_000, 3),
        "low_call_ltp": getattr(low, "call_ltp", None),
        "low_call_bid": low.call_bid,
        "low_call_ask": low.call_ask,
        "low_put_ltp": getattr(low, "put_ltp", None),
        "low_put_bid": low.put_bid,
        "low_put_ask": low.put_ask,
        "high_call_ltp": getattr(high, "call_ltp", None),
        "high_call_bid": high.call_bid,
        "high_call_ask": high.call_ask,
        "high_put_ltp": getattr(high, "put_ltp", None),
        "high_put_bid": high.put_bid,
        "high_put_ask": high.put_ask,
        "executable_edge": result.executable_edge,
        "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl,
        "lot_size": low.lot_size,
        "strike_distance": result.strike_distance,
        "freshness": "LIVE",
    }


def _snapshot_key(row):
    """Stable identity for one underlying/expiry/strike pair at a quote timestamp."""
    return (
        str(row["symbol"]).strip().upper(),
        int(row["expiry"]),
        float(row["low_strike"]),
        float(row["high_strike"]),
        str(row["direction"]).strip().upper(),
        int(row["last_update_timestamp_ns"]),
    )


class LiveBoxSpreadPipeline:
    def __init__(self, scanner, session_factory=None, alerts=None, on_results=None):
        self.scanner = scanner
        self.session_factory = session_factory
        self.alerts = alerts or BoxSpreadAlertService()
        self.on_results = on_results

    def observe(self, payload):
        results = self.scanner.observe(payload)
        if results and self.on_results:
            try:
                self.on_results(results)
            except Exception:
                logger.exception("Box Spread live-result publisher failed")

        if not results:
            return results

        # Write every qualifying observation to a disk-backed spool before DB I/O.
        # If the DB is down, pending rows survive process restarts and are replayed
        # on the next observation. A successful commit is the only point that clears it.
        try:
            fresh_rows = []
            for result in results:
                observed_at = datetime.now(timezone.utc)
                row = _snapshot_payload(result, observed_at)
                key = _snapshot_key(row)
                row["_snapshot_identity"] = list(key)
                fresh_rows.append(row)
            _append_spool(fresh_rows)
            pending_rows = _read_spool()
        except Exception:
            logger.exception("Failed to enqueue Box Spread snapshots to durable local spool")
            pending_rows = []

        if pending_rows and self.session_factory:
            for attempt in range(3):
                try:
                    with self.session_factory() as db:
                        now = datetime.now(timezone.utc)
                        retention_days = max(1, int(getattr(settings, "LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS", 7)))
                        cutoff = now - timedelta(days=retention_days)
                        db.query(LiveBoxSpreadSnapshot).filter(LiveBoxSpreadSnapshot.observed_at < cutoff).delete(synchronize_session=False)
                        db.query(LiveBoxSpreadSnapshotKey).filter(LiveBoxSpreadSnapshotKey.created_at < cutoff).delete(synchronize_session=False)
                        batch_keys = set()
                        for row in pending_rows:
                            key = tuple(row.get("_snapshot_identity") or _snapshot_key(row))
                            if key in batch_keys:
                                continue
                            batch_keys.add(key)
                            digest = hashlib.sha256(json.dumps(key, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                            try:
                                with db.begin_nested():
                                    db.add(LiveBoxSpreadSnapshotKey(snapshot_key=digest, created_at=now))
                                    db.flush()
                                    exists = db.query(LiveBoxSpreadSnapshot.id).filter(
                                        LiveBoxSpreadSnapshot.symbol == str(row["symbol"]).strip().upper(),
                                        LiveBoxSpreadSnapshot.expiry == int(row["expiry"]),
                                        LiveBoxSpreadSnapshot.low_strike == float(row["low_strike"]),
                                        LiveBoxSpreadSnapshot.high_strike == float(row["high_strike"]),
                                        LiveBoxSpreadSnapshot.direction == str(row["direction"]).strip().upper(),
                                        LiveBoxSpreadSnapshot.timestamp_ns == int(row["last_update_timestamp_ns"]),
                                    ).first()
                                    if not exists:
                                        db.add(LiveBoxSpreadSnapshot(
                                            observed_at=datetime.strptime(row["observed_at"], "%Y-%m-%dT%H:%M:%SZ"),
                                            timestamp_ns=int(row["last_update_timestamp_ns"]),
                                            symbol=str(row["symbol"]).strip().upper(),
                                            expiry=int(row["expiry"]),
                                            low_strike=float(row["low_strike"]),
                                            high_strike=float(row["high_strike"]),
                                            direction=str(row["direction"]).strip().upper(),
                                            payload_json=json.dumps({k: v for k, v in row.items() if k != "_snapshot_identity"}, separators=(",", ":"), allow_nan=False),
                                        ))
                                        db.flush()
                            except IntegrityError:
                                continue
                        db.commit()
                    _clear_spool()
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("Failed to persist Box Spread snapshots after 3 attempts; disk spool retained")
                    else:
                        logger.warning("Box Spread snapshot persistence failed (attempt %s/3); retrying; disk spool retained", attempt + 1, exc_info=True)
                        time.sleep(0.1 * (attempt + 1))

        try:
            with self.session_factory() as db:
                self.alerts.persist(db, results)
                self.alerts.notify_users(db, results)
        except Exception:
            logger.exception("Failed to persist/send Box Spread alerts")

        return results


__all__ = ["LiveBoxSpreadPipeline"]

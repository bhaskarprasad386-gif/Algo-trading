"""Live Box Spread scanner -> durable snapshots, alerts and notifications."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import LiveBoxSpreadSnapshot, LiveBoxSpreadSnapshotKey
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.notifications.box_spread_alerts import BoxSpreadAlertService

logger = logging.getLogger(__name__)


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

        if not results or not self.session_factory:
            return results

        # Snapshot history, alert history, and notifications are independent:
        # a snapshot DB error must not suppress alert processing or the live feed.
        # Retry transient database failures so brief DB hiccups do not drop
        # opportunities from Post-Market history. Each retry uses a fresh session.
        import time
        for attempt in range(3):
            try:
                with self.session_factory() as db:
                    now = datetime.now(timezone.utc)
                    retention_days = max(1, int(getattr(settings, "LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS", 7)))
                    cutoff = now - timedelta(days=retention_days)
                    db.query(LiveBoxSpreadSnapshot).filter(LiveBoxSpreadSnapshot.observed_at < cutoff).delete(synchronize_session=False)
                    db.query(LiveBoxSpreadSnapshotKey).filter(LiveBoxSpreadSnapshotKey.created_at < cutoff).delete(synchronize_session=False)
                    batch_keys = set()
                    for result in results:
                        observed_at = datetime.now(timezone.utc)
                        row = _snapshot_payload(result, observed_at)
                        key = _snapshot_key(row)
                        if key in batch_keys:
                            continue
                        batch_keys.add(key)
                        digest = hashlib.sha256(json.dumps(key, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                        try:
                            with db.begin_nested():
                                db.add(LiveBoxSpreadSnapshotKey(snapshot_key=digest, created_at=observed_at))
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
                                        observed_at=observed_at,
                                        timestamp_ns=int(row["last_update_timestamp_ns"]),
                                        symbol=str(row["symbol"]).strip().upper(),
                                        expiry=int(row["expiry"]),
                                        low_strike=float(row["low_strike"]),
                                        high_strike=float(row["high_strike"]),
                                        direction=str(row["direction"]).strip().upper(),
                                        payload_json=json.dumps(row, separators=(",", ":"), allow_nan=False),
                                    ))
                                    db.flush()
                        except IntegrityError:
                            continue
                    db.commit()
                break
            except Exception:
                if attempt == 2:
                    logger.exception("Failed to persist Box Spread snapshots after 3 attempts")
                else:
                    logger.warning("Box Spread snapshot persistence failed (attempt %s/3); retrying", attempt + 1, exc_info=True)
                    time.sleep(0.1 * (attempt + 1))

        try:
            with self.session_factory() as db:
                self.alerts.persist(db, results)
                self.alerts.notify_users(db, results)
        except Exception:
            logger.exception("Failed to persist/send Box Spread alerts")

        return results


__all__ = ["LiveBoxSpreadPipeline"]

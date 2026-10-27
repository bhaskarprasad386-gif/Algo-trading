"""Live Box Spread scanner -> durable snapshots, alerts and notifications."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.core.config import settings
from app.models import LiveBoxSpreadSnapshot
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.notifications.box_spread_alerts import BoxSpreadAlertService


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
        "observed_at": observed_at.isoformat(timespec="seconds") + "Z",
        "timestamp_ns": int(low.timestamp_ns),
        "high_timestamp_ns": int(high.timestamp_ns),
        "last_update_timestamp_ns": max(int(low.timestamp_ns), int(high.timestamp_ns)),
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
                pass
        if results and self.session_factory:
            try:
                with self.session_factory() as db:
                    now = datetime.utcnow()
                    retention_days = max(1, int(getattr(settings, "LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS", 7)))
                    db.query(LiveBoxSpreadSnapshot).filter(
                        LiveBoxSpreadSnapshot.observed_at < now - timedelta(days=retention_days)
                    ).delete(synchronize_session=False)
                    for result in results:
                        observed_at = datetime.utcnow()
                        row = _snapshot_payload(result, observed_at)
                        db.add(LiveBoxSpreadSnapshot(
                            observed_at=observed_at,
                            timestamp_ns=int(row["last_update_timestamp_ns"]),
                            symbol=str(row["symbol"]),
                            expiry=int(row["expiry"]),
                            low_strike=float(row["low_strike"]),
                            high_strike=float(row["high_strike"]),
                            direction=str(row["direction"]),
                            payload_json=json.dumps(row, separators=(",", ":"), allow_nan=False),
                        ))
                    db.commit()
                # Alert history/notifications remain separate; snapshot saving must
                # not depend on whether a notification is eligible or delivered.
                with self.session_factory() as db:
                    self.alerts.persist(db, results)
                    self.alerts.notify_users(db, results)
            except Exception:
                # Do not interrupt the live feed because a history write failed.
                pass
        return results


__all__ = ["LiveBoxSpreadPipeline"]

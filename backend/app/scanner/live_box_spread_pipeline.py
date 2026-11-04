"""Live Box Spread scanner -> durable snapshots, alerts and notifications."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import LiveBoxSpreadSnapshot, LiveBoxSpreadSnapshotKey
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.notifications.box_spread_alerts import BoxSpreadAlertService
from app.scanner.live_box_spread_routes import classify_expiry_type

logger = logging.getLogger(__name__)

# Serialize spool append/read/DB-commit/clear across scanner threads in this process.
_SPOOL_LOCK = threading.RLock()


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
            handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _rewrite_spool_lines(path, lines):
    """Atomically replace the spool with the supplied original JSONL lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _valid_spool_record(row):
    """Reject structurally valid JSON records that cannot form a DB snapshot."""
    from math import isfinite
    required = {
        "symbol", "expiry", "low_strike", "high_strike",
        "direction", "observed_at", "last_update_timestamp_ns",
    }
    if not isinstance(row, dict) or not required.issubset(row):
        return False
    try:
        if not str(row["symbol"]).strip():
            return False
        expiry = int(row["expiry"])
        if expiry <= 0 or str(expiry) != str(row["expiry"]).strip():
            return False
        low = float(row["low_strike"])
        high = float(row["high_strike"])
        if not isfinite(low) or not isfinite(high) or low <= 0 or high <= low:
            return False
        if str(row["direction"]).strip().upper() not in {"LONG", "SHORT"}:
            return False
        datetime.strptime(str(row["observed_at"]), "%Y-%m-%dT%H:%M:%SZ")
        _positive_timestamp_ns(row["last_update_timestamp_ns"], "spool.last_update_timestamp_ns")
        for field, value in row.items():
            if isinstance(value, float) and not isfinite(value):
                return False
        for field in (
            "spot_price", "spot_age_seconds", "low_call_ltp", "low_call_bid",
            "low_call_ask", "low_put_ltp", "low_put_bid", "low_put_ask",
            "high_call_ltp", "high_call_bid", "high_call_ask", "high_put_ltp",
            "high_put_bid", "high_put_ask", "executable_edge", "edge_per_lot",
            "gross_pnl", "lot_size", "strike_distance", "timestamp_skew_ms", "max_executable_lots",
        ):
            if field in row and row[field] is not None and not isfinite(float(row[field])):
                return False
        json.dumps(row, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _read_spool():
    path = _spool_path()
    if not path.exists():
        return []
    rows = []
    valid_lines = []
    corrupt_lines = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("spool record must be a JSON object")
                if not _valid_spool_record(row):
                    raise ValueError("spool record is not a valid Box Spread snapshot")
                rows.append(row)
                valid_lines.append(line if line.endswith("\n") else line + "\n")
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(
                    "Quarantining corrupt Box Spread spool record at line %s: %s",
                    line_number, exc,
                )
                corrupt_lines.append(line if line.endswith("\n") else line + "\n")

    if corrupt_lines:
        # Preserve the original malformed records for diagnosis before atomically
        # rewriting the active queue. If quarantine cannot be fsynced, fail closed
        # and leave the original spool untouched.
        quarantine = path.with_name(path.name + ".corrupt")
        with quarantine.open("a", encoding="utf-8") as handle:
            handle.writelines(corrupt_lines)
            handle.flush()
            os.fsync(handle.fileno())
        _rewrite_spool_lines(path, valid_lines)
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



def _positive_timestamp_ns(value, field_name):
    """Validate an exact positive integer nanosecond timestamp without truncation."""
    from decimal import Decimal, InvalidOperation
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError
        return int(number)
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid Box Spread quote timestamp: {field_name}") from exc


def _qualified_opportunity(result):
    """Single hard eligibility gate shared by live publishing, snapshots and alerts."""
    from app.market_data.live_box_spread_runner import INDEX_EDGE_THRESHOLDS
    try:
        symbol = str(result.low.underlying).strip().upper()
        minimum_edge = float(INDEX_EDGE_THRESHOLDS.get(symbol, 10.0))
        lots = int(getattr(result, "max_executable_lots", 0) or 0)
        edge = float(getattr(result, "depth_edge_per_unit", 0) or 0)
        return (
            str(getattr(result, "tradability_status", "") or "").strip().upper() == "TRADABLE"
            and lots >= max(2, int(settings.LIVE_BOX_SPREAD_MIN_EXECUTABLE_LOTS))
            and edge >= minimum_edge
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _qualified_snapshot_row(row):
    """Fail closed when replaying old spool entries."""
    from app.market_data.live_box_spread_runner import INDEX_EDGE_THRESHOLDS
    try:
        symbol = str(row.get("symbol") or "").strip().upper()
        lots = int(row.get("max_executable_lots") or 0)
        edge = float(row.get("depth_edge_per_unit") or 0)
        return (
            str(row.get("tradability_status") or "").strip().upper() == "TRADABLE"
            and lots >= max(2, int(settings.LIVE_BOX_SPREAD_MIN_EXECUTABLE_LOTS))
            and edge >= float(INDEX_EDGE_THRESHOLDS.get(symbol, 10.0))
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _snapshot_payload(result, observed_at, spot_provider=None):
    low, high = result.low, result.high
    low_ts = _positive_timestamp_ns(low.timestamp_ns, "low.timestamp_ns")
    high_ts = _positive_timestamp_ns(high.timestamp_ns, "high.timestamp_ns")
    # Explicit zero/fractional leg timestamps are malformed, not "missing".
    raw_leg_timestamps = (
        getattr(low, "call_timestamp_ns", None),
        getattr(low, "put_timestamp_ns", None),
        getattr(high, "call_timestamp_ns", None),
        getattr(high, "put_timestamp_ns", None),
    )
    leg_timestamps = tuple(
        _positive_timestamp_ns(value if value is not None else fallback, field)
        for value, fallback, field in zip(
            raw_leg_timestamps,
            (low_ts, low_ts, high_ts, high_ts),
            ("low.call_timestamp_ns", "low.put_timestamp_ns",
             "high.call_timestamp_ns", "high.put_timestamp_ns"),
        )
    )
    newest_timestamp = max(leg_timestamps)
    oldest_timestamp = min(leg_timestamps)
    # Snapshot persistence is driven by the four option quotes. Spot is
    # optional context and a temporary spot-provider error must not lose it.
    try:
        spot = spot_provider(low.underlying) if callable(spot_provider) else None
    except Exception:
        spot = None
    if not isinstance(spot, dict):
        spot = {}
    return {
        "symbol": low.underlying,
        "spot_price": spot.get("spot_price"),
        "spot_timestamp_ns": spot.get("spot_timestamp_ns"),
        "spot_age_seconds": spot.get("spot_age_seconds"),
        "instrument_class": low.instrument_class,
        "expiry": low.expiry,
        "expiry_date": str(low.expiry),
        "expiry_type": classify_expiry_type(low.underlying, low.expiry),
        "low_strike": low.strike,
        "high_strike": high.strike,
        "direction": result.direction,
        "observed_at": observed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp_ns": low_ts,
        "high_timestamp_ns": high_ts,
        "low_call_timestamp_ns": leg_timestamps[0],
        "low_put_timestamp_ns": leg_timestamps[1],
        "high_call_timestamp_ns": leg_timestamps[2],
        "high_put_timestamp_ns": leg_timestamps[3],
        "last_update_timestamp_ns": newest_timestamp,
        "timestamp_skew_ms": round((newest_timestamp - oldest_timestamp) / 1_000_000, 3),
        "low_call_ltp": getattr(low, "call_ltp", None),
        "low_call_bid": low.call_bid,
        "low_call_bid_qty": getattr(low, "call_bid_qty", None),
        "low_call_ask": low.call_ask,
        "low_call_ask_qty": getattr(low, "call_ask_qty", None),
        "low_put_ltp": getattr(low, "put_ltp", None),
        "low_put_bid": low.put_bid,
        "low_put_bid_qty": getattr(low, "put_bid_qty", None),
        "low_put_ask": low.put_ask,
        "low_put_ask_qty": getattr(low, "put_ask_qty", None),
        "high_call_ltp": getattr(high, "call_ltp", None),
        "high_call_bid": high.call_bid,
        "high_call_bid_qty": getattr(high, "call_bid_qty", None),
        "high_call_ask": high.call_ask,
        "high_call_ask_qty": getattr(high, "call_ask_qty", None),
        "high_put_ltp": getattr(high, "put_ltp", None),
        "high_put_bid": high.put_bid,
        "high_put_bid_qty": getattr(high, "put_bid_qty", None),
        "high_put_ask": high.put_ask,
        "high_put_ask_qty": getattr(high, "put_ask_qty", None),
        "executable_edge": result.executable_edge,
        "depth_edge_per_unit": getattr(result, "depth_edge_per_unit", result.executable_edge),
        "max_executable_lots": getattr(result, "max_executable_lots", 0),
        "tradability_status": getattr(result, "tradability_status", "UNVERIFIED"),
        "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl,
        "lot_size": low.lot_size,
        "max_executable_lots": getattr(result, "max_executable_lots", 0),
        "tradability_status": getattr(result, "tradability_status", "UNVERIFIED"),
        "tradability_reason": getattr(result, "tradability_reason", None),
        "l1_reference_edge": getattr(result, "l1_reference_edge", None),
        "depth_edge_per_unit": getattr(result, "depth_edge_per_unit", None),
        "depth_edge_per_lot": getattr(result, "depth_edge_per_lot", None),
        "depth_executable_lots": getattr(result, "depth_executable_lots", 0),
        "depth_quantity": getattr(result, "depth_quantity", 0),
        "depth_legs": list(getattr(result, "depth_legs", ()) or ()),
            "depth_entry_prices": dict(getattr(result, "depth_entry_prices", {}) or {}),
            "depth_entry_type": getattr(result, "depth_entry_type", None),
            "depth_net_entry_price": getattr(result, "depth_net_entry_price", None),
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
    def __init__(self, scanner, session_factory=None, alerts=None, on_results=None, spot_provider=None, result_transform=None, publish_enabled=True, publisher_gate=None, publisher_owner=None):
        self.scanner = scanner
        self.session_factory = session_factory
        self.alerts = alerts or BoxSpreadAlertService()
        self.on_results = on_results
        self.spot_provider = spot_provider
        self.result_transform = result_transform
        self.publisher_gate = publisher_gate
        self.publisher_owner = publisher_owner
        self._publish_lock = threading.RLock()
        self._publish_enabled = bool(publish_enabled)

    def set_publish_enabled(self, enabled):
        """Atomically gate all external side effects while a feed session is staged."""
        with self._publish_lock:
            self._publish_enabled = bool(enabled)

    def observe(self, payload):
        # Serialize the complete observation/publish cycle with activation changes.
        # Deactivation therefore waits for any in-flight publisher to finish.
        with self._publish_lock:
            return self._observe(payload)

    def _observe(self, payload):
        results = self.scanner.observe(payload)
        if results and callable(self.result_transform):
            # Apply depth pricing/gating before any downstream consumer.
            results = tuple(self.result_transform(results))
        # Hard rule: unqualified opportunities never reach live UI, snapshot or alert paths.
        results = tuple(result for result in (results or ()) if _qualified_opportunity(result))
        with self._publish_lock:
            publish_enabled = self._publish_enabled
        # A staged session may warm its scanner and depth cache, but must not
        # publish, append to the shared durable spool, persist snapshots, or alert.
        if not publish_enabled:
            return results

        if self.publisher_gate is None:
            self._publish_results(results)
        else:
            # The shared gate holds its lock through every external side effect.
            # A new owner therefore cannot activate until an in-flight old publish
            # completes, and an old owner cannot publish after ownership transfers.
            self.publisher_gate.publish(
                self.publisher_owner,
                lambda: self._publish_results(results),
            )
        return results

    def _publish_results(self, results):
        if results and self.on_results:
            try:
                self.on_results(results)
            except Exception:
                logger.exception("Box Spread live-result publisher failed")

        # Keep append, replay, commit and clear in one critical section. Otherwise
        # a concurrent observe() can append a new row after _read_spool() and have
        # that row erased by _clear_spool() after the earlier batch commits.
        with _SPOOL_LOCK:
            # Always inspect/replay the durable queue, even when this observation
            # produces no new opportunities. Otherwise transient DB failures can leave
            # snapshots stranded until another qualifying opportunity appears.
            try:
                if results:
                    fresh_rows = []
                    for result in results:
                        observed_at = datetime.now(timezone.utc)
                        row = _snapshot_payload(result, observed_at, self.spot_provider)
                        key = _snapshot_key(row)
                        row["_snapshot_identity"] = list(key)
                        fresh_rows.append(row)
                    _append_spool(fresh_rows)
                pending_rows = [row for row in _read_spool() if _qualified_snapshot_row(row)]
            except Exception as exc:
                # Never report a successful observation after failing to durably
                # enqueue it. Keep the scanner caller informed; any existing spool
                # contents remain untouched and can be replayed on the next pass.
                logger.exception("CRITICAL: failed to enqueue/read Box Spread durable local spool; qualifying observations may not be persisted")
                raise RuntimeError("Box Spread opportunity could not be durably queued") from exc
    
            if pending_rows and self.session_factory:
                for attempt in range(3):
                    try:
                        with self.session_factory() as db:
                            now = datetime.now(timezone.utc)
                            retention_days = max(1, int(getattr(settings, "LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS", 7)))
                            cutoff = now - timedelta(days=retention_days)
                            # Keep Box Spread observations indefinitely for full-history timing analysis.
                            # Only deduplication keys expire; deleting snapshots here silently erased older history.
                            db.query(LiveBoxSpreadSnapshotKey).filter(LiveBoxSpreadSnapshotKey.created_at < cutoff).delete(synchronize_session=False)
                            batch_keys = set()
                            for row in pending_rows:
                                # Older disk-spool entries may predate expiry typing.
                                # Enrich them before the durable database write when the
                                # current instrument-master mapping can classify them.
                                if str(row.get("expiry_type") or "UNKNOWN").strip().upper() == "UNKNOWN":
                                    row["expiry_type"] = classify_expiry_type(
                                        row.get("symbol"), row.get("expiry")
                                    )
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
                                                expiry_type=(str(row.get("expiry_type") or "").strip().upper() if str(row.get("expiry_type") or "").strip().upper() in {"WEEKLY", "MONTHLY"} else None),
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
    
        if results and self.session_factory:
            try:
                with self.session_factory() as db:
                    self.alerts.persist(db, results)
                    self.alerts.notify_users(db, results)
            except Exception:
                logger.exception("Failed to persist/send Box Spread alerts")



__all__ = ["LiveBoxSpreadPipeline"]

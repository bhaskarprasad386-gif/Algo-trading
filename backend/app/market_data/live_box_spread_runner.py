"""Production Box Spread live runner using Angel One option ticks."""
from __future__ import annotations
from threading import Thread, Event
from time import time_ns, sleep
from app.algo.auth import AngelOneAuth
from app.core.database import SessionLocal
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.market_data.instruments import InstrumentMaster
from app.market_data.box_spread_instrument_cache import download_box_spread_instruments
from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.scanner.live_box_spread_pipeline import LiveBoxSpreadPipeline
from app.scanner.box_spread import BoxSpreadScanConfig
from app.core.logger import app_logger

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
INDEX_EDGE_THRESHOLDS = {
    "NIFTY": 5.0, "FINNIFTY": 5.0, "MIDCPNIFTY": 5.0,
    "BANKNIFTY": 10.0, "SENSEX": 10.0, "BANKEX": 10.0,
}


def _atm_outside_subscribed_range(atm_strike, subscribed_strikes):
    if atm_strike is None or not subscribed_strikes:
        return False
    return float(atm_strike) < min(subscribed_strikes) or float(atm_strike) > max(subscribed_strikes)


def _safe_snapshot(component):
    if component is None:
        return {"state": "not_started"}
    try:
        snapshot = component.snapshot()
        return snapshot if isinstance(snapshot, dict) else {"state": "snapshot_unavailable"}
    except Exception as exc:
        return {"state": "snapshot_error", "reason": str(exc)[:240]}


class BoxSpreadLiveTarget:
    def __init__(self, underlying: str, instrument_class: str, expiry: str | None = None):
        self.underlying = underlying.strip().upper()
        self.instrument_class = instrument_class.strip().upper()
        self.expiry = expiry


class LiveBoxSpreadRunner:
    def __init__(self, data_db, targets, *, allowed_stock_symbols, future_master=None, auth=None, on_results=None, policy=None):
        self.data_db = data_db
        self.targets = tuple(targets)
        self.allowed_stock_symbols = frozenset(allowed_stock_symbols)
        self.master = future_master or InstrumentMaster()
        self.auth = auth or AngelOneAuth()
        self.on_results = on_results
        self.policy = policy or ScanPolicy()
        self.stop_event = Event()
        self.recorder = None
        self.feed = None
        self._feed_thread = None
        self.tracker = None
        self._readiness = {
            "state": "starting",
            "ready_symbols": [],
            "waiting_symbols": [],
            "unavailable_symbols": [],
            "underlying_websocket": {"state": "not_started"},
            "option_websocket": {"state": "not_started"},
        }
        self._last_feed_log_signature = None

    def run_forever(self):
        retry_delay = 5.0
        while not self.stop_event.is_set():
            try:
                self._run_session()
                retry_delay = 5.0
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                app_logger.warning("Box Spread live runner failed; retrying in {:.1f}s: {}", retry_delay, exc)
                self.stop_event.wait(retry_delay)
                retry_delay = min(60.0, retry_delay * 2.0)
        self.stop()

    def _log_feed_state_change(self):
        if self.feed is None:
            return
        snapshot = _safe_snapshot(self.feed)
        feed_detail = snapshot.get("feed") or {}
        signature = (
            str(snapshot.get("state") or "UNKNOWN"),
            str(snapshot.get("state_reason") or ""),
            str(feed_detail.get("connected")),
            str(feed_detail.get("disconnected_groups")),
            str(feed_detail.get("last_error")),
        )
        if signature == self._last_feed_log_signature:
            return
        self._last_feed_log_signature = signature
        app_logger.info(
            "Box Spread underlying WebSocket state={} reason={} connected={} disconnected_groups={} last_error={}",
            signature[0], signature[1], feed_detail.get("connected"),
            feed_detail.get("disconnected_groups"), feed_detail.get("last_error"),
        )

    def _run_session(self):
        # main.py normally prepares this master before constructing the runner.
        # Avoid a duplicate network refresh when a validated snapshot is present.
        if not getattr(self.master, "_loaded", False) or not getattr(self.master, "instruments", None):
            download_box_spread_instruments(self.master)
        symbols = tuple(dict.fromkeys(t.underlying for t in self.targets))
        from datetime import datetime
        today = datetime.now().date()
        strikes = {}
        for target in self.targets:
            wanted = target.expiry
            if wanted is None:
                future_type = "FUTSTK" if target.instrument_class == "STOCK" else "FUTIDX"
                expiries = []
                for item in self.master.instruments:
                    expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                    if str(item.get("exch_seg", "")).upper() != expected_segment or str(item.get("name", "")).strip().upper() != target.underlying:
                        continue
                    if str(item.get("instrumenttype", "")).upper() != future_type:
                        continue
                    text = str(item.get("expiry", "")).strip().upper()
                    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
                        try:
                            value = datetime.strptime(text, fmt).date()
                            if value >= today:
                                expiries.append((value, text))
                            break
                        except ValueError:
                            continue
                if expiries:
                    wanted = min(expiries)[1]
            if wanted:
                strikes.update(concrete_strikes_from_master(
                    self.master.instruments, symbols=(target.underlying,), expiry=wanted,
                    exchange_segment=("BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"),
                ))
        self.tracker = LiveSyntheticAtmTracker(strikes_by_symbol=strikes)
        index_symbols = frozenset(t.underlying for t in self.targets if t.instrument_class == "INDEX")
        self.feed = LiveSyntheticUnderlyingFeed(
            symbols, tracker=self.tracker, instrument_master=self.master, auth=self.auth,
            index_symbols=index_symbols, consumer="box-underlyings",
        )
        self._feed_thread = Thread(target=self.feed.run_forever, daemon=True, name="box-underlying-feed")
        self._feed_thread.start()
        try:
            while not self.stop_event.is_set():
                self._log_feed_state_change()
                atms = {symbol: self.tracker.atm(symbol, time_ns()) for symbol in symbols}
                ready_targets = tuple(target for target in self.targets if atms.get(target.underlying) is not None)
                ready_symbols = sorted({target.underlying for target in ready_targets})
                waiting_symbols = sorted(set(symbols) - set(ready_symbols))
                underlying_snapshot = _safe_snapshot(self.feed)
                self._readiness = {
                    "state": "ready" if ready_targets else "waiting_for_underlying_ticks",
                    "ready_symbols": ready_symbols, "waiting_symbols": waiting_symbols,
                    "unavailable_symbols": [], "underlying_websocket": underlying_snapshot,
                    "option_websocket": _safe_snapshot(self.recorder),
                }
                if not ready_targets:
                    self.stop_event.wait(0.25)
                    continue
                subs = []
                unavailable_symbols = []
                for target in ready_targets:
                    wanted = target.expiry
                    if wanted is None:
                        future_type = "FUTSTK" if target.instrument_class == "STOCK" else "FUTIDX"
                        expiries = []
                        for item in self.master.instruments:
                            expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                            if str(item.get("exch_seg", "")).upper() != expected_segment or str(item.get("name", "")).strip().upper() != target.underlying:
                                continue
                            if str(item.get("instrumenttype", "")).upper() != future_type:
                                continue
                            text = str(item.get("expiry", "")).strip().upper()
                            for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
                                try:
                                    value = datetime.strptime(text, fmt).date()
                                    if value >= datetime.now().date():
                                        expiries.append((value, text))
                                    break
                                except ValueError:
                                    continue
                        if expiries:
                            wanted = min(expiries)[1]
                    try:
                        sel = select_box_contracts(
                            self.master, underlying=target.underlying,
                            instrument_class=target.instrument_class, atm_strike=atms[target.underlying],
                            expiry=wanted, allowed_stock_symbols=self.allowed_stock_symbols, policy=self.policy,
                        )
                    except (LookupError, ValueError) as exc:
                        unavailable_symbols.append({"symbol": target.underlying, "reason": str(exc)})
                        continue
                    subs.extend(sel.subscriptions)
                self._readiness = {**self._readiness, "state": "ready" if subs else "waiting_for_contracts",
                                   "ready_symbols": ready_symbols, "waiting_symbols": waiting_symbols,
                                   "unavailable_symbols": unavailable_symbols}
                if not subs:
                    self.stop_event.wait(0.25)
                    continue
                scanner = LiveBoxSpreadScanner(
                    atm_provider=self.tracker.atm,
                    config_provider=lambda symbol: BoxSpreadScanConfig(
                        min_executable_edge=INDEX_EDGE_THRESHOLDS.get(str(symbol).strip().upper(), 10.0) + 1e-9,
                        allowed_stock_symbols=self.allowed_stock_symbols,
                    ),
                    policy=self.policy,
                )
                pipeline = LiveBoxSpreadPipeline(scanner, SessionLocal, on_results=self.on_results)
                subscribed_strikes = {}
                for sub in subs:
                    symbol = str(getattr(sub, "underlying", "") or "").strip().upper()
                    try:
                        strike = float(sub.strike)
                    except (AttributeError, TypeError, ValueError):
                        continue
                    if symbol:
                        subscribed_strikes.setdefault(symbol, set()).add(strike)

                def observe(payload):
                    pipeline.observe(payload)
                    symbol = str(payload.get("underlying") or "").upper()
                    current = self.tracker.atm(symbol, time_ns())
                    if _atm_outside_subscribed_range(current, subscribed_strikes.get(symbol, set())) and self.recorder is not None:
                        self.recorder.stop()

                self.recorder = LiveSyntheticOptionFutureRecorder(
                    self.data_db, subs, auth=self.auth, on_observation=observe, consumer="box-options",
                )
                self._readiness["option_websocket"] = {"state": "starting", "subscription_count": len(subs)}
                try:
                    self.recorder.run_forever()
                finally:
                    self.recorder = None
        finally:
            feed, self.feed = self.feed, None
            feed_thread, self._feed_thread = self._feed_thread, None
            recorder, self.recorder = self.recorder, None
            if recorder is not None:
                recorder.stop()
            if feed is not None:
                feed.stop()
            if feed_thread is not None and feed_thread.is_alive():
                feed_thread.join(timeout=2.0)

    def snapshot(self):
        return {
            "running": not self.stop_event.is_set(),
            "targets": len(self.targets),
            "underlying_feed_thread_alive": bool(self._feed_thread and self._feed_thread.is_alive()),
            "underlying_feed": _safe_snapshot(self.feed),
            "option_recorder": _safe_snapshot(self.recorder),
            "readiness": dict(self._readiness),
        }

    def stop(self):
        self.stop_event.set()
        if self.feed:
            self.feed.stop()
        if self.recorder:
            self.recorder.stop()


__all__ = ["BoxSpreadLiveTarget", "LiveBoxSpreadRunner"]

"""Production Box Spread live runner using Angel One option ticks."""
from __future__ import annotations
from threading import Thread, Event
from time import time_ns, sleep, monotonic
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
WEEKLY_EXPIRY_PRIORITY_SYMBOLS = frozenset({"NIFTY", "SENSEX"})
INDEX_EDGE_THRESHOLDS = {
    "NIFTY": 5.0,
    "FINNIFTY": 5.0,
    "MIDCPNIFTY": 5.0,
    "BANKNIFTY": 10.0,
    "SENSEX": 10.0,
    "BANKEX": 10.0,
}


def _atm_outside_subscribed_range(atm_strike, subscribed_strikes):
    """Refresh option subscriptions only after live ATM exits their strike coverage."""
    if atm_strike is None or not subscribed_strikes:
        return False
    return float(atm_strike) < min(subscribed_strikes) or float(atm_strike) > max(subscribed_strikes)


class BoxSpreadLiveTarget:
    def __init__(self, underlying: str, instrument_class: str, expiry: str|None=None):
        self.underlying=underlying.strip().upper(); self.instrument_class=instrument_class.strip().upper(); self.expiry=expiry


class LiveBoxSpreadRunner:
    def __init__(self,data_db,targets,*,allowed_stock_symbols,future_master=None,auth=None,on_results=None,policy=None):
        self.data_db=data_db; self.targets=tuple(sorted(targets, key=lambda target: (0 if target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS else 1, target.underlying.strip().upper()))); self.allowed_stock_symbols=frozenset(allowed_stock_symbols)
        self.master=future_master or InstrumentMaster(); self.auth=auth or AngelOneAuth(); self.on_results=on_results
        self.policy=policy or ScanPolicy(); self.stop_event=Event(); self.recorder=None; self.feed=None; self.tracker=None; self.scanner=None
        self._readiness = {"state": "starting", "ready_symbols": [], "waiting_symbols": [], "unavailable_symbols": []}

    @staticmethod
    def _feed_is_down(feed_snapshot) -> bool:
        """Detect a sustained no-socket state without fighting the manager's reconnect window."""
        snapshot = feed_snapshot or {}
        if not isinstance(snapshot, dict):
            return False
        if int(snapshot.get("active_instruments", 0) or 0) <= 0:
            return False
        connected = snapshot.get("connected_groups")
        pending = snapshot.get("connect_pending_groups")
        disconnected = snapshot.get("disconnected_groups")
        if connected is None or connected or pending:
            return False
        return bool(disconnected) or int(snapshot.get("socket_groups", 0) or 0) == 0

    def run_forever(self):
        retry_delay = 5.0
        max_retry_delay = 60.0
        while not self.stop_event.is_set():
            try:
                self._run_session()
                retry_delay = 5.0
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                app_logger.warning(
                    "Box Spread live runner failed; retrying in {:.1f}s: {}",
                    retry_delay,
                    exc,
                )
                self.stop_event.wait(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)
        self.stop()

    def _run_session(self):
        download_box_spread_instruments(self.master)
        symbols=tuple(dict.fromkeys(t.underlying for t in self.targets))
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
                    if str(item.get("exch_seg","")).upper() != expected_segment or str(item.get("name","")).strip().upper() != target.underlying:
                        continue
                    if str(item.get("instrumenttype","")).upper() != future_type:
                        continue
                    text = str(item.get("expiry","")).strip().upper()
                    for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d"):
                        try:
                            value = datetime.strptime(text, fmt).date()
                            if value >= today: expiries.append((value, text))
                            break
                        except ValueError:
                            continue
                if expiries: wanted = min(expiries)[1]
            if wanted:
                strikes.update(concrete_strikes_from_master(self.master.instruments,symbols=(target.underlying,),expiry=wanted, exchange_segment=("BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO")))
        self.tracker=LiveSyntheticAtmTracker(strikes_by_symbol=strikes)
        index_symbols=frozenset(t.underlying for t in self.targets if t.instrument_class=="INDEX")
        self.feed=LiveSyntheticUnderlyingFeed(symbols,tracker=self.tracker,instrument_master=self.master,auth=self.auth,index_symbols=index_symbols,consumer="box-underlyings")
        Thread(target=self.feed.run_forever,daemon=True,name="box-underlying-feed").start()
        try:
            while not self.stop_event.is_set():
                atms = {symbol: self.tracker.atm(symbol, time_ns()) for symbol in symbols}
                ready_targets = tuple(target for target in self.targets if atms.get(target.underlying) is not None)
                ready_symbols = sorted({target.underlying for target in ready_targets})
                waiting_symbols = sorted(set(symbols) - set(ready_symbols))
                self._readiness = {
                    "state": "ready" if ready_targets else "waiting_for_underlying_ticks",
                    "ready_symbols": ready_symbols,
                    "waiting_symbols": waiting_symbols,
                    "unavailable_symbols": [],
                }
                if not ready_targets:
                    sleep(.25)
                    continue
                subs=[]
                unavailable_symbols = []
                for target in ready_targets:
                    wanted = target.expiry
                    if wanted is None and target.underlying.strip().upper() not in WEEKLY_EXPIRY_PRIORITY_SYMBOLS:
                        future_type = "FUTSTK" if target.instrument_class == "STOCK" else "FUTIDX"
                        expiries = []
                        for item in self.master.instruments:
                            expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                            if str(item.get("exch_seg","")).upper() != expected_segment or str(item.get("name","")).strip().upper() != target.underlying:
                                continue
                            if str(item.get("instrumenttype","")).upper() != future_type:
                                continue
                            text = str(item.get("expiry","")).strip().upper()
                            for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d"):
                                try:
                                    value = datetime.strptime(text, fmt).date()
                                    if value >= datetime.now().date(): expiries.append((value, text))
                                    break
                                except ValueError:
                                    continue
                        if expiries:
                            wanted = min(expiries)[1]
                    try:
                        sel=select_box_contracts(self.master,underlying=target.underlying,instrument_class=target.instrument_class,
                            atm_strike=atms[target.underlying],expiry=wanted,
                            allowed_stock_symbols=self.allowed_stock_symbols,policy=self.policy)
                    except (LookupError, ValueError) as exc:
                        unavailable_symbols.append({"symbol": target.underlying, "reason": str(exc)})
                        continue
                    subs.extend(sel.subscriptions)
                self._readiness = {
                    "state": "ready" if subs else "waiting_for_contracts",
                    "ready_symbols": ready_symbols,
                    "waiting_symbols": waiting_symbols,
                    "unavailable_symbols": unavailable_symbols,
                }
                if not subs:
                    sleep(.25)
                    continue
                scanner=LiveBoxSpreadScanner(
                    atm_provider=self.tracker.atm,
                    config_provider=lambda symbol: BoxSpreadScanConfig(
                        min_executable_edge=INDEX_EDGE_THRESHOLDS.get(str(symbol).strip().upper(), 10.0) + 1e-9,
                        allowed_stock_symbols=self.allowed_stock_symbols,
                    ),
                    policy=self.policy,
                )
                self.scanner = scanner
                pipeline=LiveBoxSpreadPipeline(scanner,SessionLocal,on_results=self.on_results,spot_provider=lambda symbol: self.tracker.spot_snapshot(symbol) if self.tracker is not None else None)
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
                    symbol=str(payload.get("underlying") or "").upper()
                    current=self.tracker.atm(symbol,time_ns())
                    coverage = subscribed_strikes.get(symbol, set())
                    if _atm_outside_subscribed_range(current, coverage) and self.recorder is not None:
                        self.recorder.stop()
                self.recorder=LiveSyntheticOptionFutureRecorder(self.data_db,subs,auth=self.auth,on_observation=observe,consumer="box-options")
                recorder = self.recorder
                recorder_thread = Thread(target=recorder.run_forever, daemon=True, name="box-option-recorder")
                recorder_thread.start()
                down_since = None
                while not self.stop_event.is_set() and recorder_thread.is_alive() and self.recorder is recorder:
                    feed_snapshot = recorder.snapshot().get("feed")
                    if self._feed_is_down(feed_snapshot):
                        if down_since is None:
                            down_since = monotonic()
                        elif monotonic() - down_since >= 15.0:
                            app_logger.warning("Box Spread option feed disconnected for 15s; restarting session")
                            recorder.stop()
                            recorder_thread.join(timeout=3.0)
                            raise ConnectionError("Box Spread common WebSocket stayed disconnected for 15s")
                    else:
                        down_since = None
                    sleep(.5)
                recorder.stop()
                recorder_thread.join(timeout=3.0)
                self.recorder=None
        finally:
            feed, self.feed = self.feed, None
            recorder, self.recorder = self.recorder, None
            if recorder is not None:
                recorder.stop()
            if feed is not None:
                feed.stop()

    def snapshot(self):
        """Return lightweight runtime state for shared-feed diagnostics."""
        return {
            "running": not self.stop_event.is_set(),
            "targets": len(self.targets),
            "underlying_feed": None if self.feed is None else self.feed.snapshot(),
            "option_recorder": None if self.recorder is None else self.recorder.snapshot(),
            "readiness": dict(self._readiness),
            "scanner_diagnostics": None if self.scanner is None else self.scanner.diagnostics_snapshot(),
        }

    def stop(self):
        self.stop_event.set()
        if self.feed:self.feed.stop()
        if self.recorder:self.recorder.stop()

__all__=["BoxSpreadLiveTarget","LiveBoxSpreadRunner"]

"""Production Box Spread live runner using Angel One option ticks."""
from __future__ import annotations
from threading import Thread, Event, Lock
from dataclasses import replace
from time import time_ns, sleep, monotonic
from sqlalchemy import select
from app.algo.auth import AngelOneAuth
from app.core.database import SessionLocal
from app.core.config import settings
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.market_data.instruments import InstrumentMaster
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.box_spread_instrument_cache import download_box_spread_instruments
from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.scanner.box_spread_paper_ledger import BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.scanner.live_box_spread_pipeline import LiveBoxSpreadPipeline
from app.scanner.box_spread import BoxSpreadScanConfig
from app.scanner.box_spread_live_depth_shadow import BoxLiveDepthShadowFeed
from app.scanner.box_spread_depth_shadow_diagnostics import BoxDepthShadowDiagnostics
from app.core.logger import app_logger

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
# Temporarily excluded from the Box Spread scanner universe and all its feeds.
BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS = frozenset({"FINNIFTY", "BANKEX"})
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



def _box_underlying_health_snapshot(snapshot):
    """Expose aggregate health for Box Spread without changing the shared feed."""
    result = dict(snapshot or {})
    symbols = result.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        return result
    states = [
        str(item.get("state") or "UNKNOWN").upper()
        if isinstance(item, dict) else "UNKNOWN"
        for item in symbols.values()
    ]
    if not states:
        return result
    fresh_count = sum(state == "LIVE" for state in states)
    stale_count = sum(state == "STALE" for state in states)
    waiting_count = sum(state not in {"LIVE", "STALE"} for state in states)
    if fresh_count == len(states):
        result["state"] = "LIVE"
        result["state_reason"] = "all_configured_underlyings_have_fresh_ticks"
    elif fresh_count:
        result["state"] = "DEGRADED"
        result["state_reason"] = "one_or_more_configured_underlyings_are_not_fresh"
    elif str(result.get("state") or "").upper() in {"FAILED", "TOKEN_RESOLUTION_FAILED"}:
        pass
    elif stale_count:
        result["state"] = "STALE"
        result["state_reason"] = "all_observed_underlying_ticks_exceeded_freshness_threshold"
    elif waiting_count:
        result["state"] = "WAITING_FOR_TICKS"
        result["state_reason"] = "no_configured_underlying_has_a_fresh_tick"
    result["fresh_symbol_count"] = fresh_count
    result["stale_symbol_count"] = stale_count
    result["waiting_symbol_count"] = waiting_count
    result["configured_symbol_count"] = len(states)
    return result

def _safe_snapshot(component):
    """Return a stable diagnostic payload while feed components are starting/stopping."""
    if component is None:
        return {"state": "not_started"}
    try:
        snapshot = component.snapshot()
        return snapshot if isinstance(snapshot, dict) else {"state": "snapshot_unavailable"}
    except Exception as exc:
        return {"state": "snapshot_error", "reason": str(exc)[:240]}


class BoxSpreadLiveTarget:
    def __init__(self, underlying: str, instrument_class: str, expiry: str|None=None):
        self.underlying=underlying.strip().upper(); self.instrument_class=instrument_class.strip().upper(); self.expiry=expiry


class LiveBoxSpreadRunner:
    def __init__(self,data_db,targets,*,allowed_stock_symbols,future_master=None,auth=None,on_results=None,policy=None):
        self.data_db=data_db; self.targets=tuple(sorted(targets, key=lambda target: (0 if target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS else 1, target.underlying.strip().upper()))); self.allowed_stock_symbols=frozenset(allowed_stock_symbols)
        self.master=future_master or InstrumentMaster(); self.auth=auth or AngelOneAuth(); self.on_results=on_results
        self.policy=policy or ScanPolicy(); self.stop_event=Event(); self.recorder=None; self.feed=None; self.tracker=None; self.scanner=None
        self.depth_shadow = BoxLiveDepthShadowFeed(enabled=bool(settings.LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED))
        self._depth_shadow_lock = Lock()
        self._depth_shadow_last_comparisons = []
        self.depth_shadow_diagnostics = BoxDepthShadowDiagnostics()
        self._feed_thread = None
        self._last_feed_log_signature = None
        self._readiness = {
            "state": "starting",
            "ready_symbols": [],
            "waiting_symbols": [],
            "unavailable_symbols": [],
            "underlying_websocket": {"state": "not_started"},
            "option_websocket": {"state": "not_started"},
        }

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

    def _log_feed_state_change(self):
        """Log only meaningful underlying WebSocket transitions, not every poll."""
        if self.feed is None:
            return
        snapshot = _box_underlying_health_snapshot(_safe_snapshot(self.feed))
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

    @staticmethod
    def _depth_shadow_descriptors(subscriptions):
        """Translate Box selector contracts into the descriptor schema required by depth feed."""
        exchange_by_type = {2: "NFO", 4: "BFO"}
        descriptors = []
        for item in subscriptions:
            exchange = exchange_by_type.get(int(item.exchange_type))
            if exchange is None:
                raise ValueError(f"unsupported Box Spread option exchange type: {item.exchange_type}")
            descriptors.append(CommonStrategyMarketFeed.descriptor(
                exchange=exchange,
                token=item.token,
                symbol=item.symbol,
                instrument_type="option",
                segment=exchange,
                expiry=item.expiry,
                strike=item.strike,
                option_type=item.option_type,
                lot_size=item.lot_size,
                underlying=item.underlying,
            ))
        return tuple(descriptors)

    def _open_position_subscriptions(self):
        """Keep option contracts for persisted open paper positions in the common feed."""
        with SessionLocal() as session:
            trades = session.scalars(
                select(BoxSpreadPaperTradeRecord).where(
                    BoxSpreadPaperTradeRecord.status.notin_(("CLOSED", "CANCELLED"))
                )
            ).all()
            if not trades:
                return []
            entries = session.scalars(
                select(BoxSpreadPaperLedgerEntry).where(
                    BoxSpreadPaperLedgerEntry.trade_id.in_([trade.id for trade in trades])
                )
            ).all()
        signed_by_trade = {}
        for entry in entries:
            role_map = signed_by_trade.setdefault(entry.trade_id, {})
            role_map[entry.role] = role_map.get(entry.role, 0) + (
                entry.quantity if entry.action == "BUY" else -entry.quantity
            )
        subscriptions = []
        for trade in trades:
            if not any(signed_by_trade.get(trade.id, {}).values()):
                continue
            try:
                selection = select_box_contracts(
                    self.master, underlying=trade.underlying,
                    instrument_class=trade.instrument_class,
                    atm_strike=(float(trade.low_strike) + float(trade.high_strike)) / 2.0,
                    expiry=trade.expiry, allowed_stock_symbols=self.allowed_stock_symbols,
                    policy=self.policy,
                    required_strikes=(float(trade.low_strike), float(trade.high_strike)),
                )
                subscriptions.extend(selection.subscriptions)
            except (LookupError, ValueError) as exc:
                app_logger.warning(
                    "Box Spread paper position quote subscription unavailable for trade {}: {}",
                    trade.id, exc,
                )
        return subscriptions
    def _run_session(self):
        # main.py prepares the shared master before starting this runner. Reuse
        # that validated snapshot instead of triggering a second network refresh.
        if not getattr(self.master, "_loaded", False) or not getattr(self.master, "instruments", None):
            download_box_spread_instruments(self.master)
        symbols=tuple(dict.fromkeys(t.underlying for t in self.targets))
        from datetime import datetime
        today = datetime.now().date()
        strikes = {}
        selected_expiries = {}
        for target in self.targets:
            wanted = target.expiry
            if wanted is None and target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS:
                expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                option_expiries = []
                for item in self.master.instruments:
                    if str(item.get("exch_seg", "")).upper() != expected_segment or str(item.get("name", "")).strip().upper() != target.underlying:
                        continue
                    if str(item.get("instrumenttype", "")).upper() not in {"OPTIDX", "OPTSTK"}:
                        continue
                    text = str(item.get("expiry", "")).strip().upper()
                    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
                        try:
                            value = datetime.strptime(text, fmt).date()
                            if value >= today:
                                option_expiries.append((value, text))
                            break
                        except ValueError:
                            continue
                if option_expiries:
                    wanted = min(option_expiries)[1]
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
                            if value >= today: expiries.append((value, text))
                            break
                        except ValueError:
                            continue
                if expiries: wanted = min(expiries)[1]
            if wanted:
                symbol_key = target.underlying.strip().upper()
                selected_expiries.setdefault(symbol_key, [])
                if wanted not in selected_expiries[symbol_key]:
                    selected_expiries[symbol_key].append(wanted)
                expiry_strikes = concrete_strikes_from_master(
                    self.master.instruments,
                    symbols=(target.underlying,),
                    expiry=wanted,
                    exchange_segment=("BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"),
                ).get(target.underlying.strip().upper(), ())
                symbol_key = target.underlying.strip().upper()
                # Weekly and monthly targets share one spot/ATM tracker per
                # underlying. Union their real strikes so the later expiry
                # cannot overwrite the earlier expiry's ATM coverage.
                strikes[symbol_key] = tuple(sorted(set(strikes.get(symbol_key, ())) | set(expiry_strikes)))
        # Track every configured target for spot quotes even if an option chain
        # is temporarily unavailable. Empty strike sets intentionally disable
        # ATM readiness while preserving source-backed spot snapshots.
        for symbol in symbols:
            strikes.setdefault(symbol.strip().upper(), ())
        self.tracker=LiveSyntheticAtmTracker(strikes_by_symbol=strikes)
        index_symbols=frozenset(t.underlying for t in self.targets if t.instrument_class=="INDEX")
        self.feed=LiveSyntheticUnderlyingFeed(symbols,tracker=self.tracker,instrument_master=self.master,auth=self.auth,index_symbols=index_symbols,consumer="box-underlyings")
        self._feed_thread = Thread(target=self.feed.run_forever,daemon=True,name="box-underlying-feed")
        self._feed_thread.start()
        try:
            while not self.stop_event.is_set():
                self._log_feed_state_change()
                underlying_snapshot = _box_underlying_health_snapshot(_safe_snapshot(self.feed))
                # Box Spread accepts only source-fresh underlying prices. Keep this
                # stricter than the generic tracker lookup used by other consumers.
                atms = {symbol: self.tracker.atm(symbol, time_ns(), max_age_seconds=5.0) for symbol in symbols}
                ready_targets = tuple(target for target in self.targets if atms.get(target.underlying) is not None)
                ready_symbols = sorted({target.underlying for target in ready_targets})
                waiting_symbols = sorted(set(symbols) - set(ready_symbols))
                self._readiness = {
                    "state": "ready" if ready_targets else "waiting_for_underlying_ticks",
                    "ready_symbols": ready_symbols,
                    "waiting_symbols": waiting_symbols,
                    "unavailable_symbols": [],
                    "selected_expiries": dict(selected_expiries),
                    "contract_expiries": {},
                    "subscription_counts": {},
                    "underlying_websocket": underlying_snapshot,
                    "option_websocket": _safe_snapshot(self.recorder),
                }
                if not ready_targets:
                    sleep(.25)
                    continue
                subs=[]
                unavailable_symbols = []
                contract_expiries = {}
                subscription_counts = {}
                for target in ready_targets:
                    # Reuse the same valid expiry used to seed the ATM tracker.
                    # Letting the selector infer expiry independently can pick an
                    # expired option chain from the instrument master.
                    wanted = target.expiry or next(iter(selected_expiries.get(target.underlying.strip().upper(), ())), None)
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
                    symbol_key = target.underlying.strip().upper()
                    contract_expiries.setdefault(symbol_key, [])
                    contract_expiry = str(sel.expiry)
                    if contract_expiry not in contract_expiries[symbol_key]:
                        contract_expiries[symbol_key].append(contract_expiry)
                    subscription_counts.setdefault(symbol_key, [])
                    subscription_counts[symbol_key].append({"expiry": contract_expiry, "count": len(sel.subscriptions)})
                    subs.extend(sel.subscriptions)
                # Preserve held-position strikes when the ATM subscription window moves.
                subs.extend(self._open_position_subscriptions())
                self._readiness = {
                    "state": "ready" if subs else "waiting_for_contracts",
                    "ready_symbols": ready_symbols,
                    "waiting_symbols": waiting_symbols,
                    "unavailable_symbols": unavailable_symbols,
                    "selected_expiries": dict(selected_expiries),
                    "contract_expiries": contract_expiries,
                    "subscription_counts": subscription_counts,
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
                def apply_depth_gate(opportunities):
                    from math import isfinite
                    gated = []
                    comparisons = []
                    # Never permit configuration to weaken the two-common-lot safety floor.
                    minimum_lots = max(2, int(settings.LIVE_BOX_SPREAD_MIN_EXECUTABLE_LOTS))
                    for opportunity in opportunities:
                        try:
                            comparison = self.depth_shadow.compare_opportunity(opportunity) if self.depth_shadow.enabled else {"status": "INCOMPLETE", "reason": "DEPTH_FEED_DISABLED"}
                            self.depth_shadow_diagnostics.record(comparison)
                            comparisons.append(comparison)
                            if comparison.get("status") != "COMPLETE":
                                gated.append(replace(opportunity, executable_edge=0.0, edge_per_lot=0.0, gross_pnl=0.0, max_executable_lots=0, tradability_status="NOT_TRADABLE", tradability_reason=str(comparison.get("reason") or "DEPTH_INCOMPLETE"), l1_reference_edge=float(opportunity.executable_edge), depth_executable_lots=0, depth_quantity=0))
                                continue
                            lots = int(comparison.get("max_executable_lots") or 0)
                            depth_edge = float(comparison.get("depth_edge_per_unit"))
                            quantity = int(comparison.get("quantity") or 0)
                            lot_size = int(opportunity.low.lot_size)
                            if not isfinite(depth_edge) or depth_edge <= 0 or lots < minimum_lots or quantity != lots * lot_size:
                                reason = "NON_POSITIVE_DEPTH_EDGE" if isfinite(depth_edge) and depth_edge <= 0 else "BELOW_MINIMUM_EXECUTABLE_LOTS" if lots < minimum_lots else "INVALID_DEPTH_ALLOCATION"
                                gated.append(replace(opportunity, executable_edge=0.0, edge_per_lot=0.0, gross_pnl=0.0, max_executable_lots=0, tradability_status="NOT_TRADABLE", tradability_reason=reason, l1_reference_edge=float(opportunity.executable_edge), depth_executable_lots=lots, depth_quantity=quantity))
                                continue
                            gated.append(replace(opportunity, executable_edge=depth_edge, edge_per_lot=depth_edge * lot_size, gross_pnl=depth_edge * quantity, max_executable_lots=lots, tradability_status="TRADABLE", tradability_reason=None, l1_reference_edge=float(opportunity.executable_edge), depth_edge_per_unit=depth_edge, depth_edge_per_lot=depth_edge * lot_size, depth_executable_lots=lots, depth_quantity=quantity, depth_legs=tuple(comparison.get("legs") or ()), depth_entry_prices={leg["role"]: float(leg["vwap"]) for leg in (comparison.get("legs") or ())}, depth_entry_type="NET_DEBIT" if str(opportunity.direction).strip().upper() == "LONG" else "NET_CREDIT", depth_net_entry_price=abs(float(comparison.get("net_entry_price") or 0.0))))
                        except Exception:
                            app_logger.exception("Box Spread four-leg depth gate failed closed")
                            gated.append(replace(opportunity, executable_edge=0.0, edge_per_lot=0.0, gross_pnl=0.0, max_executable_lots=0, tradability_status="NOT_TRADABLE", tradability_reason="DEPTH_EVALUATION_ERROR", l1_reference_edge=float(opportunity.executable_edge), depth_executable_lots=0, depth_quantity=0))
                    if comparisons:
                        with self._depth_shadow_lock:
                            self._depth_shadow_last_comparisons = comparisons[-20:]
                    return tuple(gated)

                pipeline=LiveBoxSpreadPipeline(scanner, SessionLocal, on_results=self.on_results, spot_provider=lambda symbol: self.tracker.spot_snapshot(symbol) if self.tracker is not None else None, result_transform=apply_depth_gate)
                subscribed_strikes = {}
                for sub in subs:
                    symbol = str(getattr(sub, "underlying", "") or "").strip().upper()
                    try:
                        strike = float(sub.strike)
                    except (AttributeError, TypeError, ValueError):
                        continue
                    if symbol:
                        subscribed_strikes.setdefault(symbol, set()).add(strike)
                # Log and trigger each refresh only once per recorder session. The
                # full option consumer is detached when the ATM leaves its subscribed
                # strike window, so the exact symbol/range is essential to diagnose churn.
                refresh_requested = Event()

                def observe(payload):
                    results = pipeline.observe(payload)
                    symbol=str(payload.get("underlying") or "").strip().upper()
                    current=self.tracker.atm(symbol,time_ns(),max_age_seconds=10.0)
                    coverage = subscribed_strikes.get(symbol, set())
                    if (
                        _atm_outside_subscribed_range(current, coverage)
                        and self.recorder is not None
                        and not refresh_requested.is_set()
                    ):
                        refresh_requested.set()
                        app_logger.warning(
                            "Box Spread option subscription refresh requested: "
                            "reason=atm_outside_subscribed_range underlying={} atm_strike={} "
                            "subscribed_min_strike={} subscribed_max_strike={} "
                            "subscribed_strike_count={} total_subscription_count={} quote_underlying={}",
                            symbol,
                            current,
                            min(coverage),
                            max(coverage),
                            len(coverage),
                            len(subs),
                            str(payload.get("underlying") or "").strip().upper(),
                        )
                        self.recorder.stop()
                if not self.depth_shadow.enabled:
                    raise RuntimeError("Box Spread live paper opportunities require the dedicated five-level depth feed")
                # Fail closed: never start an L1-only live opportunity publisher.
                depth_descriptors = self._depth_shadow_descriptors(subs)
                self.depth_shadow.start(depth_descriptors)
                self.recorder=LiveSyntheticOptionFutureRecorder(self.data_db,subs,auth=self.auth,on_observation=observe,consumer="box-options")
                recorder = self.recorder
                recorder_thread = Thread(target=recorder.run_forever, daemon=True, name="box-option-recorder")
                recorder_thread.start()
                self._readiness["option_websocket"] = {
                    "state": "starting",
                    "subscription_count": len(subs),
                }
                down_since = None
                while not self.stop_event.is_set() and recorder_thread.is_alive() and self.recorder is recorder:
                    self._log_feed_state_change()
                    self._readiness["underlying_websocket"] = _box_underlying_health_snapshot(_safe_snapshot(self.feed))
                    recorder_snapshot = _safe_snapshot(recorder)
                    self._readiness["option_websocket"] = recorder_snapshot
                    feed_snapshot = recorder_snapshot.get("feed")
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
            feed_thread, self._feed_thread = self._feed_thread, None
            recorder, self.recorder = self.recorder, None
            if recorder is not None:
                recorder.stop()
            self.depth_shadow.stop()
            if feed is not None:
                feed.stop()
            if feed_thread is not None and callable(getattr(feed_thread, "is_alive", None)) and feed_thread.is_alive():
                feed_thread.join(timeout=2.0)

    def snapshot(self):
        """Return lightweight runtime state for shared-feed diagnostics."""
        depth_snapshot = self.depth_shadow.snapshot()
        with self._depth_shadow_lock:
            last_comparisons = list(self._depth_shadow_last_comparisons)
        return {
            "running": not self.stop_event.is_set(),
            "targets": len(self.targets),
            "underlying_feed_thread_alive": bool(
                self._feed_thread
                and callable(getattr(self._feed_thread, "is_alive", None))
                and self._feed_thread.is_alive()
            ),
            "underlying_feed": _box_underlying_health_snapshot(_safe_snapshot(self.feed)),
            "option_recorder": _safe_snapshot(self.recorder),
            "readiness": dict(self._readiness),
            "scanner_diagnostics": None if self.scanner is None else self.scanner.diagnostics_snapshot(),
            "depth_shadow": {
                "enabled": self.depth_shadow.enabled,
                "running": depth_snapshot.get("running", False),
                "record_count": depth_snapshot.get("record_count", 0),
                "counts": depth_snapshot.get("counts", {}),
                "last_comparisons": last_comparisons,
                "comparison_diagnostics": self.depth_shadow_diagnostics.snapshot(),
            },
        }

    def stop(self):
        self.stop_event.set()
        if self.feed:self.feed.stop()
        if self.recorder:self.recorder.stop()
        self.depth_shadow.stop()

__all__=["BoxSpreadLiveTarget","LiveBoxSpreadRunner"]

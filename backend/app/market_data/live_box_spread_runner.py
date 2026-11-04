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
from app.scanner.box_spread_publication_gate import BOX_SPREAD_PUBLICATION_GATE
from app.scanner.box_spread import BoxSpreadScanConfig
from app.scanner.box_spread_live_depth_shadow import BoxLiveDepthShadowFeed
from app.scanner.box_spread_depth_shadow_diagnostics import BoxDepthShadowDiagnostics
from app.core.logger import app_logger

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
# Temporarily excluded from the Box Spread scanner universe and all its feeds.
BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS = frozenset()
WEEKLY_EXPIRY_PRIORITY_SYMBOLS = frozenset({"NIFTY", "SENSEX"})
INDEX_EDGE_THRESHOLDS = {
    "NIFTY": 5.0,
    "FINNIFTY": 5.0,
    "MIDCPNIFTY": 10.0,
    "BANKNIFTY": 10.0,
    "SENSEX": 10.0,
    "BANKEX": 10.0,
}

def _depth_edge_below_minimum(depth_edge: float, minimum_edge: float) -> bool:
    """Apply the configured per-index floor to the final four-leg depth VWAP edge."""
    return float(depth_edge) + 1e-9 < float(minimum_edge)


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
    def __init__(self, underlying: str, instrument_class: str, expiry: str|None=None, *, weekly_only: bool=False):
        self.underlying=underlying.strip().upper(); self.instrument_class=instrument_class.strip().upper(); self.expiry=expiry
        self.weekly_only = bool(weekly_only)


class LiveBoxSpreadRunner:
    def __init__(self,data_db,targets,*,allowed_stock_symbols,future_master=None,auth=None,on_results=None,policy=None,consumer_namespace="box",publish_enabled=True,publication_gate=None):
        self.data_db=data_db; self.targets=tuple(sorted(targets, key=lambda target: (0 if target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS else 1, target.underlying.strip().upper()))); self.allowed_stock_symbols=frozenset(allowed_stock_symbols)
        self.consumer_namespace = str(consumer_namespace or "box").strip()
        if not self.consumer_namespace or any(not (char.isalnum() or char in "-_:") for char in self.consumer_namespace):
            raise ValueError("consumer_namespace must contain only letters, numbers, hyphens, underscores or colons")
        self.master=future_master or InstrumentMaster(); self.auth=auth or AngelOneAuth(); self.on_results=on_results
        self.policy=policy or ScanPolicy(); self.stop_event=Event(); self.recorder=None; self.feed=None; self.tracker=None; self.scanner=None; self.pipeline=None
        self._publish_enabled = bool(publish_enabled)
        self._publishing_requested = bool(publish_enabled)
        self.publication_gate = publication_gate or BOX_SPREAD_PUBLICATION_GATE
        self._publisher_owner = object()
        if self._publish_enabled:
            self.publication_gate.activate(self._publisher_owner)
        self._reconfigure_event = Event()
        self._targets_lock = Lock()
        self._pending_targets = None
        self.depth_shadow = BoxLiveDepthShadowFeed(enabled=bool(settings.LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED), consumer=f"{self.consumer_namespace}-live-depth-shadow")
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

    def activate_publishing(self):
        """Allow this warmed session to become the sole live result publisher."""
        self._publishing_requested = True
        self._publish_enabled = True
        self.publication_gate.activate(self._publisher_owner)
        pipeline = self.pipeline
        if pipeline is not None:
            pipeline.set_publish_enabled(True)

    def deactivate_publishing(self, *, preserve_request=False):
        """Stop this session from publishing before its feeds are detached."""
        if not preserve_request:
            self._publishing_requested = False
        self._publish_enabled = False
        pipeline = self.pipeline
        if pipeline is not None:
            pipeline.set_publish_enabled(False)
        self.publication_gate.deactivate(self._publisher_owner)
    @staticmethod
    def staged_session_readiness(snapshot, required_targets):
        """Fail closed unless each selected underlying and expiry has matching live quotes/depth.

        Accept legacy symbol-only iterables for focused unit tests and diagnostics.
        Runtime callers pass (underlying, expiry) pairs so weekly and monthly
        expiries cannot accidentally satisfy one another's readiness.
        """
        def expiry_key(value):
            if value is None:
                return ""
            raw = str(value).strip().upper()
            if not raw:
                return ""
            from datetime import datetime
            for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y%m%d")
                except ValueError:
                    continue
            return raw

        required_symbols = set()
        required_expiry_groups = set()
        symbols_requiring_depth = set()
        for item in required_targets:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                symbol = str(item[0] or "").strip().upper()
                expiry = expiry_key(item[1])
                if symbol:
                    required_symbols.add(symbol)
                    if expiry:
                        required_expiry_groups.add((symbol, expiry))
                        symbols_requiring_depth.add(symbol)
            else:
                symbol = str(item).strip().upper()
                if symbol:
                    required_symbols.add(symbol)
        if not required_symbols:
            return {"ready": False, "state": "WAITING_FOR_TARGETS", "reasons": ["no_required_symbols"]}

        underlying = snapshot.get("underlying_feed") or {}
        symbol_rows = underlying.get("symbols") if isinstance(underlying, dict) else {}
        symbol_rows = symbol_rows if isinstance(symbol_rows, dict) else {}
        missing_underlyings = sorted(
            symbol for symbol in required_symbols
            if str((symbol_rows.get(symbol) or {}).get("state") or "").upper() != "LIVE"
        )

        option = snapshot.get("option_recorder") or {}
        option_feed = option.get("feed") or {}
        fresh_option_groups = {}
        for contract in option.get("fresh_contracts") or []:
            if not isinstance(contract, dict):
                continue
            key = (
                str(contract.get("underlying") or "").strip().upper(),
                expiry_key(contract.get("expiry")),
            )
            option_type = str(contract.get("option_type") or "").strip().upper()
            counts = fresh_option_groups.setdefault(key, {"CE": 0, "PE": 0})
            if option_type in counts:
                counts[option_type] += 1
        option_ready = (
            int(option.get("subscriptions", 0) or 0) >= 4
            and int(option_feed.get("active_instruments", 0) or 0) > 0
            and bool(option_feed.get("connected_groups"))
            and not LiveBoxSpreadRunner._feed_is_down(option_feed)
        )

        depth = snapshot.get("depth_shadow") or {}
        depth_rows = depth.get("readiness") or []
        ready_depth_symbols = set()
        ready_expiry_groups = set()
        missing_option_groups = []
        for row in depth_rows:
            if not isinstance(row, dict):
                continue
            underlying_symbol = str(row.get("underlying") or "").strip().upper()
            expiry_value = expiry_key(row.get("expiry"))
            if underlying_symbol not in required_symbols:
                continue
            if required_expiry_groups and (underlying_symbol, expiry_value) not in required_expiry_groups:
                continue
            option_counts = fresh_option_groups.get((underlying_symbol, expiry_value), {})
            exact_option_ready = (
                int(option_counts.get("CE", 0)) >= 2
                and int(option_counts.get("PE", 0)) >= 2
            )
            depth_ready = (
                str(row.get("state") or "").upper() == "READY"
                and int(row.get("executable_depth_ce", 0) or 0) >= 2
                and int(row.get("executable_depth_pe", 0) or 0) >= 2
                and int(row.get("executable_depth_contracts", 0) or 0) >= 4
            )
            if depth_ready and exact_option_ready:
                ready_depth_symbols.add(underlying_symbol)
                ready_expiry_groups.add((underlying_symbol, expiry_value))
            elif depth_ready and not exact_option_ready:
                missing_option_groups.append(f"{underlying_symbol}:{expiry_value or 'UNKNOWN'}")

        if required_expiry_groups:
            missing_expiry_groups = sorted(required_expiry_groups - ready_expiry_groups)
            missing_depth = sorted(symbols_requiring_depth - ready_depth_symbols)
        else:
            missing_expiry_groups = []
            missing_depth = []

        reasons = []
        if missing_underlyings:
            reasons.append("underlying_ticks_not_fresh:" + ",".join(missing_underlyings))
        if required_expiry_groups and not option_ready:
            reasons.append("option_recorder_not_connected")
        if missing_option_groups:
            reasons.append("exact_option_contracts_not_fresh:" + ",".join(sorted(missing_option_groups)))
        if required_expiry_groups and (not depth.get("enabled") or not depth.get("running")):
            reasons.append("depth_shadow_not_running")
        if missing_depth:
            reasons.append("executable_two_lot_depth_or_matching_option_quotes_not_ready:" + ",".join(missing_depth))
        if missing_expiry_groups:
            reasons.append("missing_expiry_readiness:" + ",".join(f"{symbol}:{expiry}" for symbol, expiry in missing_expiry_groups))
        return {
            "ready": not reasons,
            "state": "READY" if not reasons else "WAITING_FOR_FEEDS",
            "required_symbols": sorted(required_symbols),
            "required_expiry_groups": [list(item) for item in sorted(required_expiry_groups)],
            "missing_expiry_groups": [list(item) for item in missing_expiry_groups],
            "missing_underlying_symbols": missing_underlyings,
            "missing_depth_symbols": missing_depth,
            "option_recorder_ready": option_ready,
            "reasons": reasons,
        }

    def staged_readiness(self):
        """Read the live session state using the same strict gate used before handoff."""
        return self.staged_session_readiness(
            self.snapshot(),
            {(target.underlying, target.expiry) for target in self.targets},
        )

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

    def request_reconfigure(self, targets):
        """Queue a validated target set; the current session tears down once before applying it."""
        normalized = tuple(sorted(targets, key=lambda target: (0 if target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS else 1, target.underlying.strip().upper(), str(target.expiry or ""))))
        if not normalized:
            raise ValueError("Box Spread requires at least one selected index target")
        with self._targets_lock:
            self._pending_targets = normalized
            self._reconfigure_event.set()
        return {"status": "RECONFIGURE_QUEUED", "target_count": len(normalized), "symbols": sorted({target.underlying.strip().upper() for target in normalized})}

    def _apply_pending_targets(self):
        with self._targets_lock:
            if not self._reconfigure_event.is_set() or self._pending_targets is None:
                return False
            self.targets = self._pending_targets
            self._pending_targets = None
            self._reconfigure_event.clear()
            self._readiness = {"state": "reconfiguring", "ready_symbols": [], "waiting_symbols": [], "unavailable_symbols": []}
            self.scanner = None
            self.tracker = None
        return True

    def run_forever(self):
        retry_delay = 5.0
        max_retry_delay = 60.0
        while not self.stop_event.is_set():
            try:
                self._run_session()
                if self._apply_pending_targets():
                    retry_delay = 1.0
                    continue
                retry_delay = 5.0
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                if self._apply_pending_targets():
                    retry_delay = 1.0
                    continue
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
            # Selector output is normally SyntheticSubscription, while tests and
            # compatible callers may already provide canonical descriptors.
            if hasattr(item, "key") and hasattr(item, "exchange") and hasattr(item, "instrument_type"):
                descriptors.append(item)
                continue
            try:
                exchange_type = int(item.exchange_type)
            except (AttributeError, TypeError, ValueError) as exc:
                raise TypeError(
                    "Box Spread depth contracts must be SyntheticSubscription or InstrumentDescriptor"
                ) from exc
            exchange = exchange_by_type.get(exchange_type)
            if exchange is None:
                raise ValueError(f"unsupported Box Spread option exchange type: {exchange_type}")
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
        selected_symbols = {target.underlying.strip().upper() for target in self.targets}
        for trade in trades:
            if str(trade.underlying or "").strip().upper() not in selected_symbols:
                continue
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
        # A reconfigured session may reclaim an idle gate, but must never
        # override a newer staged runner that already owns publication.
        if self._publishing_requested:
            self.publication_gate.claim_if_unowned(self._publisher_owner)
            self._publish_enabled = self.publication_gate.is_owner(self._publisher_owner)
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
            if wanted is None and target.underlying.strip().upper() in WEEKLY_EXPIRY_PRIORITY_SYMBOLS and not getattr(target, "weekly_only", False):
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
        self.feed=LiveSyntheticUnderlyingFeed(symbols,tracker=self.tracker,instrument_master=self.master,auth=self.auth,index_symbols=index_symbols,consumer=f"{self.consumer_namespace}-underlyings")
        self._feed_thread = Thread(target=self.feed.run_forever,daemon=True,name="box-underlying-feed")
        self._feed_thread.start()
        try:
            while not self.stop_event.is_set() and not self._reconfigure_event.is_set():
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
                    # Weekly-only targets must fail closed when the instrument
                    # master has no classified weekly expiry. Passing expiry=None
                    # to the selector would let it infer a monthly contract.
                    if getattr(target, "weekly_only", False) and wanted is None:
                        unavailable_symbols.append({
                            "symbol": target.underlying,
                            "reason": "WEEKLY_EXPIRY_UNAVAILABLE",
                        })
                        continue
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
                            minimum_edge = INDEX_EDGE_THRESHOLDS.get(
                                str(opportunity.low.underlying).strip().upper(), 10.0
                            )
                            if (
                                not isfinite(depth_edge)
                                or depth_edge <= 0
                                or _depth_edge_below_minimum(depth_edge, minimum_edge)
                                or lots < minimum_lots
                                or quantity != lots * lot_size
                            ):
                                reason = (
                                    "NON_POSITIVE_DEPTH_EDGE"
                                    if isfinite(depth_edge) and depth_edge <= 0
                                    else "BELOW_MINIMUM_DEPTH_EDGE"
                                    if isfinite(depth_edge) and _depth_edge_below_minimum(depth_edge, minimum_edge)
                                    else "BELOW_MINIMUM_EXECUTABLE_LOTS"
                                    if lots < minimum_lots
                                    else "INVALID_DEPTH_ALLOCATION"
                                )
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

                pipeline=LiveBoxSpreadPipeline(scanner, SessionLocal, on_results=self.on_results, spot_provider=lambda symbol: self.tracker.spot_snapshot(symbol) if self.tracker is not None else None, result_transform=apply_depth_gate, publish_enabled=self._publish_enabled, publisher_gate=self.publication_gate, publisher_owner=self._publisher_owner)
                self.pipeline = pipeline
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
                self.recorder=LiveSyntheticOptionFutureRecorder(self.data_db,subs,auth=self.auth,on_observation=observe,consumer=f"{self.consumer_namespace}-options")
                recorder = self.recorder
                recorder_thread = Thread(target=recorder.run_forever, daemon=True, name="box-option-recorder")
                recorder_thread.start()
                self._readiness["option_websocket"] = {
                    "state": "starting",
                    "subscription_count": len(subs),
                }
                down_since = None
                while not self.stop_event.is_set() and not self._reconfigure_event.is_set() and recorder_thread.is_alive() and self.recorder is recorder:
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
            self.deactivate_publishing(preserve_request=True)
            self.pipeline = None
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
            "target_symbols": sorted({target.underlying.strip().upper() for target in self.targets}),
            "reconfigure_pending": self._reconfigure_event.is_set(),
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
                "readiness": depth_snapshot.get("readiness", []),
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

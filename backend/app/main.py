from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
from pydantic import BaseModel
import asyncio
import queue
import threading
import math
import time as time_module
import uuid
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logger import app_logger
from app.core.diagnostics import runtime_diagnostics
from app.core.exceptions import TradingAppException, trading_exception_handler, global_exception_handler
from app.core.database import engine, Base, SessionLocal, check_database
from app.core.schema_migrations import run_schema_migrations
from app.core.iv_basket_guards import install_iv_basket_immutability_guards
from app.models import User, Instrument, Session, Position, SystemLog, BoxSpreadIndexSelection
from app.models.iv_scanner_control import IVScannerControl
from app.models.iv_constituent_basket_version import IVConstituentBasketVersion  # noqa: F401 - register isolated IV basket history table
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult
from app.models.live_synthetic_pair_history import LiveSyntheticPairHistory
from app.models.synthetic_trace_status_check import SyntheticTraceStatusCheck
from app.models.synthetic_trace_opportunity import SyntheticTraceOpportunity
from app.algo.auth import AngelOneAuth
from app.market_data.live_cash_future_stream import live_cash_future_health
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
from app.market_data.common_strategy_feed import shared_common_manager
from app.market_data.live_calendar_spread_stream import LiveCalendarSpreadOneSecondCollector
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget, select_nearest_option_expiry, select_monthly_option_expiry
from app.market_data.live_synthetic_underlying import filter_resolvable_index_symbols
from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner, BoxSpreadLiveTarget, INDEX_EDGE_THRESHOLDS, BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS
from app.market_data.box_spread_runner_supervisor import BoxSpreadRunnerSupervisor
from app.market_data.instruments import InstrumentMaster
from app.market_data.box_spread_expiry import classify_index_expiry_dates
from app.market_data.box_spread_instrument_cache import download_box_spread_instruments
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS, TOP15_LIQUID_STOCK_SYMBOLS_ORDERED, OPTION_STRATEGY_INDEX_SYMBOLS, NIFTY50_INDEX_SYMBOLS
BSE_BOX_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.market_data.routes import router as market_data_router, configure_live_cash_future_runner_snapshot
from app.scanner.routes import router as scanner_router, full_fno_router, configure_live_cash_future_snapshot
from app.scanner.weekly_swing_scanner import router as weekly_swing_scanner_router, weekly_swing_daily_refresh_loop
from app.scanner.auto_routes import router as auto_scanner_router, discover_cash_future_symbols
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
from app.scanner.calendar_spread_routes import router as calendar_spread_scanner_router, configure as configure_calendar_spread_scanner, configure_feed_snapshot as configure_calendar_spread_feed_snapshot
from app.scanner.live_synthetic_routes import router as live_synthetic_router, configure as configure_live_synthetic, configure_diagnostics as configure_live_synthetic_diagnostics, configure_pairs as configure_live_synthetic_pairs
from app.scanner.live_synthetic_trace_routes import router as live_synthetic_trace_router
from app.scanner.live_box_spread_routes import router as live_box_spread_router, configure as configure_live_box_spread, configure_diagnostics as configure_live_box_spread_diagnostics, configure_monitor as configure_live_box_spread_monitor, configure_spot_provider as configure_live_box_spread_spot, configure_index_selection_provider as configure_live_box_index_selection, set_expiry_types as set_live_box_expiry_types
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime
from app.scanner.iv_skew_flip.instruments import download_iv_instruments
from app.scanner.iv_skew_flip.basket_registry import summarize_basket_registry
from app.scanner.iv_skew_flip.basket_runtime import load_verified_runtime_baskets
from app.scanner.iv_skew_flip.validation import SignalEvent, load_persisted_forward_outcomes, summarize_forward_outcomes, summarize_walk_forward_by_date
from app.models.iv_signal_event import IVSignalEvent
from app.scanner.box_spread_trace_routes import router as box_spread_trace_router
from app.scanner.box_spread_paper_routes import router as box_spread_paper_router
from app.scanner.box_spread_paper_ledger import BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry
from app.scanner.box_spread_paper_rule_routes import router as box_spread_paper_rule_router, paper_rule_monitor_loop
from app.scanner.live_box_spread_websocket import router as live_box_spread_websocket_router, publish as publish_live_box_spread_opportunities, configure as configure_live_box_spread_websocket, configure_spot_provider as configure_live_box_spread_websocket_spot, configure_depth_provider as configure_live_box_spread_websocket_depth, set_expiry_types as set_live_box_websocket_expiry_types
from app.intelligence.routes import router as intelligence_router
from app.alert_routes import router as alert_router
from app.backtesting.replay_routes import create_replay_router
from app.scanner.cash_future_collector import CashFutureHistoryCollector
from app.brokers.routes import router as brokers_router
from app.backtesting.download_status_routes import create_download_status_router
from app.backtesting.cash_future_download_routes import CashFutureDownloadManager, create_cash_future_download_router
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.contract_master_sync import DailyContractMasterSync
from app.backtesting.monthly_results_routes import router as monthly_results_router
from app.backtesting.cash_future_strategy_routes import router as cash_future_strategy_router
from app.backtesting.calendar_spread_strategy_routes import router as calendar_spread_strategy_router
from app.backtesting.universal_factory import create_universal_ledger
from app.backtesting.universal_result_routes import create_universal_result_router
from app.backtesting.universal_result_service import UniversalResultService
from app.scanner import box_spread_paper_ledger as _box_spread_paper_ledger_models  # noqa: F401

run_schema_migrations()
Base.metadata.create_all(bind=engine)
install_iv_basket_immutability_guards(engine)

# Reuse one process-local instrument-master manager for live feed/API resolution.
# Its own lazy cache prevents repeated OpenAPIScripMaster downloads.
instrument_master = InstrumentMaster()
iv_skew_flip_runtime: IVSkewFlipRuntime | None = None
_iv_skew_flip_runtime_lock = threading.RLock()
live_cash_future_scanner = LiveCashFutureScanner()
configure_live_cash_future_snapshot(live_cash_future_scanner.snapshot)
live_cash_future_runner: LiveCashFutureCommonRunner | None = None
configure_live_cash_future_runner_snapshot(
    lambda: None if live_cash_future_runner is None else live_cash_future_runner.snapshot()
)
live_calendar_spread_scanner = LiveCalendarSpreadScanner(
    minimum_gap_points=settings.LIVE_CALENDAR_SPREAD_MIN_GAP_POINTS,
    minimum_gross_profit=settings.LIVE_CALENDAR_SPREAD_MIN_GROSS_PROFIT,
)
configure_calendar_spread_scanner(live_calendar_spread_scanner)
configure_calendar_spread_feed_snapshot(lambda: None if live_calendar_spread_runner is None else live_calendar_spread_runner.snapshot())
live_synthetic_latest_results: tuple = ()
live_synthetic_runner: LiveSyntheticRunner | None = None
_live_synthetic_universe_selection = "INDEX"
_live_synthetic_universe_restart_requested = threading.Event()
_live_synthetic_universe_status = {
    "configured_stock_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
    "eligible_future_symbols": [],
    "missing_future_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
    "configured_index_symbols": sorted(NIFTY50_INDEX_SYMBOLS),
    "eligible_index_symbols": [],
    "missing_index_underlyings": [],
    "eligible_target_symbols": [],
    "missing_option_expiry_symbols": [],
    "checked_at": None,
}
live_box_spread_latest_results: tuple = ()
live_box_spread_runner: LiveBoxSpreadRunner | None = None
live_box_spread_supervisor: BoxSpreadRunnerSupervisor | None = None
BOX_SPREAD_INDEX_SELECTION_LOCK = threading.Lock()
BOX_SPREAD_INDEX_PRESETS = {
    "ALL": tuple(sorted(OPTION_STRATEGY_INDEX_SYMBOLS)),
    "NIFTY_SENSEX": ("NIFTY", "SENSEX"),
    "NIFTY_SENSEX_BANKNIFTY": ("NIFTY", "SENSEX", "BANKNIFTY"),
}
BOX_SPREAD_DEFAULT_INDEX_SELECTION = ("NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX")


def _normalize_box_spread_index_symbols(symbols):
    if not isinstance(symbols, (list, tuple, set, frozenset)):
        raise ValueError("symbols must be a non-empty list")
    normalized = tuple(sorted({str(item or "").strip().upper() for item in symbols if str(item or "").strip()}))
    unsupported = sorted(set(normalized) - set(OPTION_STRATEGY_INDEX_SYMBOLS))
    if unsupported:
        raise ValueError(f"unsupported Box Spread indices: {', '.join(unsupported)}")
    if not normalized:
        raise ValueError("select at least one Box Spread index")
    return normalized


def _read_box_spread_selection_payload(raw):
    """Read both legacy list rows and the structured index/expiry selection."""
    payload = json.loads(raw or "[]")
    if isinstance(payload, list):
        return {"symbols": payload, "weekly_only_symbols": []}
    if isinstance(payload, dict):
        return {
            "symbols": payload.get("symbols", []),
            "weekly_only_symbols": payload.get("weekly_only_symbols", []),
        }
    raise ValueError("invalid saved Box Spread index selection")


def _get_box_spread_index_selection():
    try:
        with SessionLocal() as db:
            saved = db.get(BoxSpreadIndexSelection, 1)
            if saved is not None:
                payload = _read_box_spread_selection_payload(saved.symbols_json)
                return _normalize_box_spread_index_symbols(payload["symbols"])
    except Exception as exc:
        app_logger.exception("Box Spread index selection read failed; refusing to broaden the live subscription universe")
        raise RuntimeError("Box Spread index selection is unavailable; live index subscriptions are paused until it can be read") from exc
    return BOX_SPREAD_DEFAULT_INDEX_SELECTION


def _get_box_spread_weekly_only_symbols():
    try:
        with SessionLocal() as db:
            saved = db.get(BoxSpreadIndexSelection, 1)
            if saved is None:
                return ()
            payload = _read_box_spread_selection_payload(saved.symbols_json)
            weekly = _normalize_box_spread_index_symbols(payload["weekly_only_symbols"]) if payload.get("weekly_only_symbols") else ()
            return tuple(symbol for symbol in weekly if symbol in {"NIFTY", "SENSEX"})
    except Exception as exc:
        app_logger.exception("Box Spread expiry selection read failed; refusing to broaden the live subscription universe")
        raise RuntimeError("Box Spread expiry selection is unavailable; live subscriptions are paused until it can be read") from exc


def _save_box_spread_index_selection(symbols, weekly_only_symbols=()):
    normalized = _normalize_box_spread_index_symbols(symbols)
    weekly = tuple(sorted({str(item or "").strip().upper() for item in weekly_only_symbols if str(item or "").strip()}))
    unsupported_weekly = sorted(set(weekly) - {"NIFTY", "SENSEX"})
    if unsupported_weekly:
        raise ValueError(f"weekly-only mode is supported only for NIFTY and SENSEX: {', '.join(unsupported_weekly)}")
    if set(weekly) - set(normalized):
        raise ValueError("weekly-only indices must also be included in selected indices")
    payload = {"symbols": list(normalized), "weekly_only_symbols": list(weekly)}
    with SessionLocal() as db:
        saved = db.get(BoxSpreadIndexSelection, 1)
        if saved is None:
            saved = BoxSpreadIndexSelection(id=1, symbols_json=json.dumps(payload))
            db.add(saved)
        else:
            saved.symbols_json = json.dumps(payload)
            saved.updated_at = datetime.now(timezone.utc)
        db.commit()
    return normalized


def _box_spread_targets_from_master(master, symbols, weekly_only_symbols=()):
    """Build exact selected-index option targets without touching other consumers."""
    selected = _normalize_box_spread_index_symbols(symbols)
    weekly_only = set(weekly_only_symbols or ())
    if weekly_only - {"NIFTY", "SENSEX"} or weekly_only - set(selected):
        raise ValueError("weekly-only mode is valid only for selected NIFTY/SENSEX")
    today = datetime.now(IST).date()
    targets = []
    expiry_types = {}
    for symbol in selected:
        segment = "BFO" if symbol in BSE_BOX_INDEX_SYMBOLS else "NFO"
        found = {}
        for contract in getattr(master, "instruments", ()) or ():
            if str(contract.get("exch_seg", "")).strip().upper() != segment:
                continue
            if str(contract.get("instrumenttype", "")).strip().upper() != "OPTIDX":
                continue
            if str(contract.get("name", "")).strip().upper() != symbol:
                continue
            raw = str(contract.get("expiry", "")).strip()
            parsed = None
            for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
                try:
                    parsed = datetime.strptime(raw.upper(), fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is not None and parsed >= today:
                found.setdefault(parsed, raw)
        dates = sorted(found)
        classifications = classify_index_expiry_dates(symbol, dates)
        for day, raw in found.items():
            expiry_types[(symbol, raw.upper())] = classifications.get(day, "UNKNOWN")
        expiries = [(day, found[day], classifications.get(day, "UNKNOWN")) for day in dates]
        weekly = next((item for item in expiries if item[2] == "WEEKLY"), None)
        monthly = next((item for item in expiries if item[2] == "MONTHLY"), None)
        selected_expiries = []
        if symbol in weekly_only:
            # Explicit weekly-only means monthly contracts must never be subscribed.
            if weekly:
                selected_expiries.append(weekly)
        else:
            if weekly:
                selected_expiries.append(weekly)
            if monthly and (not weekly or monthly[0] != weekly[0]):
                selected_expiries.append(monthly)
            if not selected_expiries and expiries:
                selected_expiries.append(expiries[0])
        if selected_expiries:
            targets.extend(
                BoxSpreadLiveTarget(symbol, "INDEX", expiry=item[1], weekly_only=symbol in weekly_only)
                for item in selected_expiries
            )
        else:
            # Preserve spot-only monitoring without allowing a weekly-only index
            # to fall back to a monthly option chain in the runner.
            targets.append(BoxSpreadLiveTarget(symbol, "INDEX", expiry=None, weekly_only=symbol in weekly_only))
    return tuple(targets), expiry_types


configure_live_synthetic(lambda: live_synthetic_latest_results)
configure_live_synthetic_diagnostics(lambda: None if live_synthetic_runner is None else live_synthetic_runner.snapshot())
configure_live_synthetic_pairs(lambda: None if live_synthetic_runner is None else live_synthetic_runner.pair_monitor_snapshot())
configure_live_box_spread(lambda: live_box_spread_latest_results)
def _get_box_spread_active_index_symbols():
    """Expose the publisher's active index universe during make-before-break handoff.

    The persisted selection may already point at a staged candidate while the
    previous runner is still publishing. Spot snapshots must follow the active
    runner until the supervisor transfers ownership.
    """
    runner = live_box_spread_runner
    if runner is None:
        return _get_box_spread_index_selection()
    return tuple(sorted({
        str(target.underlying).strip().upper()
        for target in runner.targets
        if str(getattr(target, "underlying", "") or "").strip()
    }))


configure_live_box_index_selection(_get_box_spread_active_index_symbols)
configure_live_box_spread_monitor(lambda: None if live_box_spread_runner is None or live_box_spread_runner.scanner is None else live_box_spread_runner.scanner.monitor_snapshot())
configure_live_box_spread_diagnostics(
    lambda: None if live_box_spread_runner is None else live_box_spread_runner.snapshot().get("scanner_diagnostics")
)
configure_live_box_spread_websocket(lambda: live_box_spread_latest_results)
def _live_box_spread_spot_snapshot(symbol: str):
    runner = live_box_spread_runner
    tracker = None if runner is None else runner.tracker
    return None if tracker is None else tracker.spot_snapshot(symbol, max_age_seconds=10.0)

configure_live_box_spread_spot(_live_box_spread_spot_snapshot)
configure_live_box_spread_websocket_spot(_live_box_spread_spot_snapshot)
configure_live_box_spread_websocket_depth(
    lambda opportunity: (
        live_box_spread_runner.depth_shadow.depth_for_opportunity(opportunity)
        if live_box_spread_runner is not None else {}
    )
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task, _weekly_swing_refresh_task, iv_skew_flip_runtime
    app_logger.info(f"{settings.app_name} started successfully in {settings.environment} mode")
    # Recovery is intentionally deferred until application startup so the schema
    # migration module has no dependency on scanner/backtest job modules.
    from app.scanner.backtest_jobs import recover_interrupted_jobs
    from app.brokers.real_owner_state import reconcile_owner_state_on_startup
    recover_interrupted_jobs()
    # Broker adapter sessions are process-local and are not restored across
    # restarts. Never trust a persisted connected flag after process startup.
    with SessionLocal() as owner_state_db:
        reconcile_owner_state_on_startup(owner_state_db)
    # Restore only the IV Scanner when its own persisted switch is ON.
    if _get_iv_scanner_enabled():
        try:
            _start_iv_skew_flip_runtime()
        except Exception:
            app_logger.exception("Persisted IV Scanner ON could not be restored; runner remains STOPPED")
    if _weekly_swing_refresh_task is None:
        _weekly_swing_refresh_task = asyncio.create_task(weekly_swing_daily_refresh_loop())
    if settings.BACKTESTING_ENABLED and settings.BACKTEST_CONTRACT_MASTER_AUTO_SYNC and _contract_master_sync_task is None:
        _contract_master_sync_task = asyncio.create_task(_contract_master_sync_loop())
    if _collector_enabled() and _history_collector_task is None:
        _history_collector_task = asyncio.create_task(_cash_future_history_loop())
    if settings.LIVE_CASH_FUTURE_DATA_ENABLED and _live_cash_future_task is None:
        _live_cash_future_task = asyncio.create_task(_live_cash_future_loop())
    if settings.LIVE_CALENDAR_SPREAD_DATA_ENABLED and _live_calendar_spread_task is None:
        _live_calendar_spread_task = asyncio.create_task(_live_calendar_spread_loop())
    if settings.LIVE_SYNTHETIC_DATA_ENABLED and _live_synthetic_task is None:
        _live_synthetic_task = asyncio.create_task(_live_synthetic_loop())
    if settings.LIVE_BOX_SPREAD_DATA_ENABLED and _live_box_spread_task is None:
        _live_box_spread_task = asyncio.create_task(_live_box_spread_loop())
    paper_rule_task = asyncio.create_task(paper_rule_monitor_loop())
    try:
        yield
    finally:
        # IV scanner is opt-in and owns only its named shared-feed consumers.
        if iv_skew_flip_runtime is not None:
            iv_skew_flip_runtime.stop()
            iv_skew_flip_runtime = None
        # Signal blocking worker threads before cancelling their asyncio wrappers.
        # This makes to_thread(run_forever) unwind promptly during service stop.
        _signal_live_runner_shutdown()
        paper_rule_task.cancel()
        for task in (_history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task, _weekly_swing_refresh_task):
            if task is not None:
                task.cancel()
        shutdown_tasks = tuple(
            task
            for task in (
                _history_collector_task,
                _contract_master_sync_task,
                _live_cash_future_task,
                _live_calendar_spread_task,
                _live_synthetic_task,
                _live_box_spread_task,
                _weekly_swing_refresh_task,
                paper_rule_task,
            )
            if task is not None
        )
        if shutdown_tasks:
            # Await all cancelled background tasks concurrently. Sequential
            # awaits can add each runner's cleanup timeout and exceed systemd's
            # 30s service stop deadline.
            results = await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    app_logger.error("Background task shutdown failed: %s", result)
        _history_collector_task = None
        _contract_master_sync_task = None
        _live_cash_future_task = None
        _live_calendar_spread_task = None
        _live_synthetic_task = None
        _live_box_spread_task = None
        _weekly_swing_refresh_task = None
        if backtest_download_manager is not None:
            backtest_download_manager.close()
        if backtest_status_store is not None:
            backtest_status_store.close()
        if universal_result_ledger is not None:
            universal_result_ledger.close()

app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://([a-z0-9-]+\.)?vercel\.app$|http://localhost(:\d+)?$|http://127\.0\.0\.1(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(TradingAppException, trading_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

app.include_router(brokers_router)
app.include_router(arbitrage_router)
app.include_router(instruments_router)
app.include_router(market_data_router)
app.include_router(scanner_router)
app.include_router(weekly_swing_scanner_router)
app.include_router(full_fno_router)
app.include_router(auto_scanner_router)
app.include_router(alert_router)
if settings.BACKTESTING_ENABLED:
    app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))
app.include_router(calendar_spread_scanner_router)
app.include_router(live_synthetic_router)
app.include_router(live_synthetic_trace_router)
app.include_router(live_box_spread_router)
app.include_router(box_spread_trace_router)
app.include_router(box_spread_paper_router)
app.include_router(box_spread_paper_rule_router)
app.include_router(live_box_spread_websocket_router)
app.include_router(intelligence_router)
if settings.BACKTESTING_ENABLED:
    app.include_router(monthly_results_router)
    app.include_router(cash_future_strategy_router)
    app.include_router(calendar_spread_strategy_router)

# Durable backtesting-download status is kept in its own SQLite file so API
# requests never depend on an in-memory status object. The path can be
# overridden for deployments through the BACKTEST_STATUS_DB environment var.
backtest_status_store = None
backtest_download_manager = None
universal_result_ledger = None
if settings.BACKTESTING_ENABLED:
    BACKTEST_STATUS_DB = Path(settings.BACKTEST_STATUS_DB)
    BACKTEST_STATUS_DB.parent.mkdir(parents=True, exist_ok=True)
    backtest_status_store = HistoricalDownloadStatusStore(str(BACKTEST_STATUS_DB))
    app.include_router(create_download_status_router(backtest_status_store))

    # Historical download/backtesting storage is opt-in and unavailable by default.
    backtest_download_manager = CashFutureDownloadManager(
        data_db=settings.BACKTEST_DATA_DB,
        contract_db=settings.BACKTEST_CONTRACT_DB,
        status_store=backtest_status_store,
    )
    app.include_router(create_cash_future_download_router(backtest_download_manager))

    universal_result_ledger = create_universal_ledger(settings.BACKTEST_RESULT_LEDGER_DB)
    universal_result_service = UniversalResultService(universal_result_ledger)
    app.include_router(create_universal_result_router(universal_result_service))

DASHBOARD_FILE = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"
BROKER_SETTINGS_FILE = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "broker.html"


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    """Serve the web dashboard and Cash-Future scanner connector."""
    html = DASHBOARD_FILE.read_text(encoding="utf-8")
    connector = r'''
<script>

(async function connectCashFutureScanner(){
  const api=window.location.origin;
  const log=document.getElementById('terminal-logs');
  const spread=document.getElementById('arb-spread');
  const live=document.getElementById('live-price');
  const liveStatus=document.getElementById('live-status');
  const section=document.createElement('div');
  section.className='card'; section.style.marginTop='15px'; section.style.borderLeftColor='#22c55e';
  section.innerHTML='<h3>Cash–Future Opportunities</h3><div id="cf-summary" style="font-size:12px;color:#94a3b8">Scanning backend…</div><div style="overflow-x:auto;margin-top:10px"><table id="cf-table" style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr><th align="left">Symbol</th><th>Cash</th><th>Future</th><th>Gap</th><th>Margin</th><th>Net</th><th>ROI</th></tr></thead><tbody></tbody></table></div>';
  const pnl=document.querySelector('.card[style*="border-left-color:#facc15"]');
  const container=document.querySelector('.container');
  if(!container){return;}
  (pnl?.parentNode||container).insertBefore(section,pnl||null);
  const summary=document.getElementById('cf-summary'),tbody=document.querySelector('#cf-table tbody');
  if(!summary||!tbody){return;}
  async function scan(){
    try{
      summary.textContent='Scanning backend Cash-Future opportunities…';
      const r=await fetch(`${api}/api/v1/scanner/cash-future/live/fast?limit=500`,{cache:'no-store'});
      if(!r.ok) throw new Error(`Scanner API ${r.status}`);
      const p=await r.json(), rows=Array.isArray(p.data)?p.data:[];
      tbody.innerHTML='';
      rows.slice(0,20).forEach(x=>{
        const tr=document.createElement('tr');
        tr.innerHTML=`<td>${x.symbol??'-'}</td><td>${Number(x.cash_ltp??0).toFixed(2)}</td><td>${Number(x.future_ltp??0).toFixed(2)}</td><td>${Number(x.gap??0).toFixed(2)}</td><td>${Number(x.margin_required??0).toFixed(2)}</td><td>${Number(x.net_profit??0).toFixed(2)}</td><td>${Number(x.roi_pct??0).toFixed(2)}%</td>`;
        tbody.appendChild(tr);
      });
      summary.textContent=`Backend connected • ${p.scanned_observations??0} scanned • ${p.opportunity_count??rows.length} executable opportunities`;
      const best=rows[0];
      if(best){
        if(spread) spread.innerHTML=`Arbitrage: <strong>${best.symbol} • Gap ₹${Number(best.gap??0).toFixed(2)} • Net ₹${Number(best.net_profit??0).toFixed(2)}</strong>`;
        if(live) live.textContent=Number(best.cash_ltp??0).toFixed(2);
        if(liveStatus) liveStatus.textContent=`Cash price from Cash-Future scanner • ${best.symbol}`;
      }
      if(log){log.innerHTML+=`<br>[${new Date().toLocaleTimeString()}] Cash-Future scan: ${rows.length} executable.`; log.scrollTop=log.scrollHeight;}
    }catch(e){summary.textContent=`Scanner unavailable: ${e.message}`;}
  }
  await scan(); setInterval(scan,30000);
})();
</script>
'''
    return HTMLResponse(content=html.replace("</body>", connector + "</body>"), media_type="text/html")



@app.get("/dashboard/broker", include_in_schema=False)
def broker_settings():
    """Serve the authenticated user's broker settings page."""
    html = BROKER_SETTINGS_FILE.read_text(encoding="utf-8")
    return HTMLResponse(content=html, media_type="text/html")


@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """Stream the Home Command Center from the existing process-local live scanner.

    The browser receives one server-side stream and never opens a broker socket.
    The existing Angel One 1-second Cash-Future stream remains the sole market feed.
    """
    await websocket.accept()
    try:
        while True:
            snapshot = live_cash_future_scanner.snapshot(max_age_seconds=5.0, limit=50)
            health = live_cash_future_scanner.health()
            cash_count = len(snapshot)
            calendar_snapshot = live_calendar_spread_scanner.snapshot(limit=200)
            calendar_count = len(calendar_snapshot)
            synthetic_snapshot = _fresh_live_synthetic_results(live_synthetic_latest_results)
            box_snapshot = live_box_spread_latest_results
            def _ts(items):
                if not items:
                    return None
                first = items[0]
                value = getattr(first, "timestamp_ns", None)
                if value is None:
                    value = getattr(getattr(first, "option", None), "timestamp_ns", None)
                if value is None:
                    value = getattr(getattr(first, "low", None), "timestamp_ns", None)
                return value
            await websocket.send_json({
                "type": "dashboard_snapshot",
                "timestamp": datetime.now(IST).isoformat(),
                "scanner": {
                    "mode": "live-fast",
                    "data": snapshot,
                    "health": health,
                    "opportunity_count": sum(
                        1 for item in snapshot
                        if float(item.get("gap", 0) or 0) > 0
                        and float(item.get("net_gap", 0) or 0) > 0
                        and item.get("lifecycle") != "EXPIRED"
                    ),
                },
                "integration": {
                    "transport": "server-websocket",
                    "broker_socket_per_browser": False,
                    "cash_future": {"count": cash_count, "timestamp_ns": _ts(snapshot)},
                    "calendar_spread": {"count": calendar_count, "timestamp_ns": _ts(calendar_snapshot)},
                    "synthetic_arbitrage": {"count": len(synthetic_snapshot), "timestamp_ns": _ts(synthetic_snapshot)},
                    "box_spread": {"count": len(box_snapshot), "timestamp_ns": _ts(box_snapshot)},
                },
                "live_orders": "OFF",
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        app_logger.warning("Dashboard WebSocket closed: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

_history_collector_task: asyncio.Task | None = None
_weekly_swing_refresh_task: asyncio.Task | None = None
_contract_master_sync_task: asyncio.Task | None = None
_live_cash_future_task: asyncio.Task | None = None
_live_calendar_spread_task: asyncio.Task | None = None
live_calendar_spread_runner: LiveCalendarSpreadOneSecondCollector | None = None
IST = ZoneInfo("Asia/Kolkata")
_live_synthetic_task: asyncio.Task | None = None
_live_box_spread_task: asyncio.Task | None = None
MARKET_OPEN = time(9, 15)


def _signal_live_runner_shutdown() -> None:
    """Signal worker-thread runners before cancelling their asyncio wrappers.

    The live runners execute blocking run_forever() methods through
    asyncio.to_thread(). Cancelling the asyncio task does not itself stop the
    worker thread. Set each runner's interrupt event first so run_forever() can
    unwind and perform its normal cleanup instead of reaching systemd's
    30-second stop deadline.
    """
    if live_box_spread_supervisor is not None:
        threading.Thread(
            target=live_box_spread_supervisor.stop,
            name="box-spread-supervisor-shutdown",
            daemon=True,
        ).start()
    for runner in (
        live_cash_future_runner,
        live_calendar_spread_runner,
        live_synthetic_runner,
        live_box_spread_runner,
    ):
        if runner is None:
            continue
        stop_event = getattr(runner, "stop_event", None)
        if stop_event is not None:
            stop_event.set()
            continue
        stop_requested = getattr(runner, "_stop_requested", None)
        if stop_requested is not None:
            stop_requested.set()
            continue
        app_logger.warning("Live runner has no interrupt event during shutdown: %s", type(runner).__name__)

MARKET_CLOSE = time(15, 30)


def _collector_enabled() -> bool:
    return settings.CASH_FUTURE_HISTORY_ENABLED


def _collector_symbols() -> list[str]:
    configured = [item.strip().upper() for item in settings.CASH_FUTURE_HISTORY_SYMBOLS.split(",") if item.strip()]
    if configured:
        return configured
    try:
        return discover_cash_future_symbols(limit=50)
    except Exception as exc:
        app_logger.warning(f"Cash-Future dynamic symbol discovery failed: {exc}")
        return []


def _collector_interval() -> int:
    return max(15, settings.CASH_FUTURE_HISTORY_INTERVAL_SECONDS)


def _market_is_open(now: datetime) -> bool:
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


async def _cash_future_history_loop() -> None:
    interval = _collector_interval()
    app_logger.info(f"Cash-Future history collector started: {interval}s interval")
    while True:
        try:
            now_ist = datetime.now(IST)
            if _market_is_open(now_ist):
                symbols = _collector_symbols()
                if not symbols:
                    app_logger.warning("Cash-Future history collector has no active symbols")
                else:
                    collector = CashFutureHistoryCollector(symbols)
                    db = SessionLocal()
                    try:
                        result = await asyncio.to_thread(collector.collect, db)
                        app_logger.info(
                            f"Cash-Future history cycle complete: {len(result['collected'])} observations, "
                            f"{len(result['errors'])} errors"
                        )
                    finally:
                        db.close()
            else:
                app_logger.debug("Cash-Future history collector sleeping outside NSE market hours")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(f"Cash-Future history cycle failed: {exc}")
        await asyncio.sleep(interval)



def _stop_live_runner_nonblocking(runner, *, name: str) -> None:
    """Request runner shutdown without blocking the asyncio event loop.

    Runner stop paths also close shared broker sockets and can legitimately take
    longer than the systemd 30-second service deadline. The runner's interrupt
    event is set synchronously by stop(); potentially blocking cleanup runs in a
    daemon thread so application shutdown can finish promptly.
    """
    threading.Thread(target=runner.stop, name=name, daemon=True).start()


async def _run_live_runner_in_daemon_thread(
    runner, *, name: str, error_holder: list[BaseException] | None = None,
    stop_on_exit: bool = True,
) -> None:
    """Run a blocking market runner in a daemon thread.

    When requested, capture worker-thread exceptions so the async supervisor can
    retry instead of treating a dead worker as a normal stop. Supervisors that
    need to distinguish unexpected worker exit from requested shutdown can defer
    stop() to their own finally block with stop_on_exit=False.
    """
    def _target() -> None:
        try:
            runner.run_forever()
        except BaseException as exc:
            runtime_diagnostics.record(
                component="Scanner Runner" if "runner" in name.lower() else "Runtime",
                severity="CRITICAL" if isinstance(exc, (SystemExit, KeyboardInterrupt)) else "ERROR",
                error_type=type(exc).__name__,
                message=str(exc) or repr(exc),
                context={"worker": name},
            )
            if error_holder is not None:
                error_holder.append(exc)
            else:
                raise

    worker = threading.Thread(target=_target, name=name, daemon=True)
    # Expose the actual worker lifecycle to runner health snapshots. A clear
    # stop event alone does not prove the worker thread is still alive.
    try:
        setattr(runner, "_runner_worker", worker)
    except Exception:
        pass
    worker.start()
    try:
        while worker.is_alive():
            await asyncio.sleep(0.25)
        if error_holder:
            raise error_holder[0]
    finally:
        if stop_on_exit:
            _stop_live_runner_nonblocking(runner, name=f"{name}-stop")


async def _live_cash_future_loop() -> None:
    """Supervise the live scanner feed with bounded exponential retry backoff."""
    global live_cash_future_runner
    retry_delay = 5.0
    max_retry_delay = 60.0
    while True:
        runner = LiveCashFutureCommonRunner(
            settings.BACKTEST_DATA_DB,
            instrument_master=instrument_master,
            on_payload=lambda payload: live_cash_future_scanner.observe(
                payload, session_factory=SessionLocal
            ),
        )
        live_cash_future_runner = runner
        try:
            worker_errors: list[BaseException] = []
            await _run_live_runner_in_daemon_thread(
                runner,
                name="live-cash-future-runner",
                error_holder=worker_errors,
            )
            if not runner.stop_event.is_set():
                app_logger.warning(
                    "Live Cash-Future runner exited unexpectedly; retrying in {:.1f}s",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)
            else:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(
                "Live Cash-Future runner failed; retrying in {:.1f}s: {}",
                retry_delay, exc,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(max_retry_delay, retry_delay * 2.0)
        finally:
            _stop_live_runner_nonblocking(runner, name="live-cash-future-final-stop")
            if live_cash_future_runner is runner:
                live_cash_future_runner = None

async def _live_synthetic_loop() -> None:
    """Supervise the synthetic live runner with bounded retry backoff."""
    global live_synthetic_runner, live_synthetic_latest_results
    retry_delay = 5.0
    max_retry_delay = 60.0
    while True:
        master = instrument_master
        runner = None
        try:
            master.download()
            today = datetime.now(IST).date()
            top15_liquid_stocks: set[str] = set()
            index_symbols: set[str] = set()
            commodity_symbols: set[str] = set()
            for item in master.instruments:
                segment = str(item.get("exch_seg", "")).strip().upper()
                typ = str(item.get("instrumenttype", "")).strip().upper()
                expiry_text = str(item.get("expiry", "")).strip()
                name = str(item.get("name", "")).strip().upper()
                if not expiry_text or not name:
                    continue
                try:
                    expiry = datetime.strptime(expiry_text.upper(), "%d%b%Y").date()
                except ValueError:
                    try:
                        expiry = datetime.strptime(expiry_text.upper(), "%d%b%y").date()
                    except ValueError:
                        continue
                if expiry < today:
                    continue
                if typ == "FUTSTK" and segment == "NFO" and name in TOP15_LIQUID_STOCK_SYMBOLS:
                    top15_liquid_stocks.add(name)
                elif (
                    typ == "FUTIDX"
                    and segment in {"NFO", "BFO"}
                    and name in NIFTY50_INDEX_SYMBOLS
                ):
                    index_symbols.add(name)
                elif typ == "FUTCOM" and segment == "MCX":
                    commodity_symbols.add(name)

            stock_symbols = [symbol for symbol in TOP15_LIQUID_STOCK_SYMBOLS_ORDERED if symbol in top15_liquid_stocks]
            missing_future_symbols = [symbol for symbol in TOP15_LIQUID_STOCK_SYMBOLS_ORDERED if symbol not in top15_liquid_stocks]
            raw_index_symbols = sorted(index_symbols)
            index_symbols = list(filter_resolvable_index_symbols(master, raw_index_symbols))
            missing_index_underlyings = sorted(set(raw_index_symbols) - set(index_symbols))
            commodity_symbols = sorted(commodity_symbols)
            if missing_index_underlyings:
                app_logger.warning(
                    "Synthetic live index underlyings skipped because no concrete Angel token exists: {}",
                    ", ".join(missing_index_underlyings),
                )

            # The selected universe is authoritative: ALL = supported indices + top 15 liquid stocks.
            # An index-only selection must never silently fall back to stocks or other indices.
            selection = _live_synthetic_universe_selection
            if selection == "INDEX":
                target_specs = [(symbol, "INDEX") for symbol in index_symbols]
            elif selection == "ALL":
                target_specs = (
                    [(symbol, "STOCK") for symbol in stock_symbols]
                    + [(symbol, "INDEX") for symbol in index_symbols]
                )
            elif selection == "TOP15":
                target_specs = [(symbol, "STOCK") for symbol in stock_symbols]
            elif selection in NIFTY50_INDEX_SYMBOLS:
                target_specs = [(selection, "INDEX")] if selection in index_symbols else []
            else:
                target_specs = []
            targets = []
            missing_expiry = []
            for symbol, instrument_class in target_specs:
                # Synthetic Future must pair options with the listed future's
                # expiry for both stocks and indices; weekly options belong to
                # the separate Box Spread scanner only.
                expiry_selector = select_monthly_option_expiry
                expiry = expiry_selector(
                    master.instruments,
                    underlying=symbol,
                    instrument_class=instrument_class,
                    today=today,
                )
                if expiry is None:
                    missing_expiry.append(symbol)
                    continue
                targets.append(SyntheticLiveTarget(symbol, instrument_class, expiry=expiry))
            targets = tuple(targets)
            _live_synthetic_universe_status.update({
                "configured_stock_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
                "eligible_future_symbols": list(stock_symbols),
                "missing_future_symbols": list(missing_future_symbols),
                "configured_index_symbols": sorted(NIFTY50_INDEX_SYMBOLS),
                "eligible_index_symbols": list(index_symbols),
                "missing_index_underlyings": list(missing_index_underlyings),
                "eligible_target_symbols": [target.underlying for target in targets],
                "missing_option_expiry_symbols": sorted(set(missing_expiry)),
                "checked_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            })
            if missing_expiry:
                app_logger.warning(
                    "Synthetic live targets skipped because no current option expiry exists: {}",
                    ", ".join(sorted(set(missing_expiry))),
                )
            if not targets:
                app_logger.warning(
                    "Synthetic live runner found no eligible top-15 stock/monthly-index option targets; retrying in {:.1f}s",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)
                continue

            runner = LiveSyntheticRunner(
                settings.BACKTEST_DATA_DB,
                targets,
                allowed_stock_symbols=frozenset(),
                instrument_master=master,
                auth=AngelOneAuth(),
                stock_universe_provider=lambda: tuple(stock_symbols),
                on_results=lambda results: _update_live_synthetic_results(results),
            )
            live_synthetic_runner = runner
            await _run_live_runner_in_daemon_thread(
                runner, name="live-synthetic-runner"
            )
            if not runner.stop_event.is_set():
                app_logger.warning(
                    "Live Synthetic runner exited unexpectedly; retrying in {:.1f}s",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)
            elif _live_synthetic_universe_restart_requested.is_set():
                _live_synthetic_universe_restart_requested.clear()
                retry_delay = 5.0
                continue
            else:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(
                "Live Synthetic runner failed; retrying in {:.1f}s: {}",
                retry_delay, exc,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(max_retry_delay, retry_delay * 2.0)
        finally:
            if runner is not None:
                _stop_live_runner_nonblocking(
                    runner, name="live-synthetic-final-stop"
                )
            if live_synthetic_runner is runner:
                live_synthetic_runner = None


def _fresh_live_synthetic_results(results: tuple, max_age_seconds: float = 5.0) -> tuple:
    """Keep dashboard integration counts aligned with the live endpoint's freshness window."""
    cutoff_ns = time_module.time_ns() - int(max_age_seconds * 1_000_000_000)
    fresh = []
    for result in results:
        try:
            timestamp_ns = int(result.option.timestamp_ns)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if timestamp_ns >= cutoff_ns:
            fresh.append(result)
    return tuple(fresh)


# The scanner callback can run once per underlying/scan bucket. Replacing the
# global tuple on every callback lets a quiet stock or index erase fresh results
# emitted by another symbol milliseconds earlier. Keep a bounded, freshness-only
# cross-symbol snapshot; the live API still enforces its own five-second cutoff.
_live_synthetic_results_lock = threading.Lock()


def _synthetic_result_key(result) -> tuple:
    option = getattr(result, "option", None)
    if option is None:
        return ("", "", "", "", "")
    try:
        strike = format(float(getattr(option, "strike", "")), ".12g")
    except (TypeError, ValueError, OverflowError):
        strike = str(getattr(option, "strike", "") or "").strip().upper()
    return (
        str(getattr(option, "underlying", "") or "").strip().upper(),
        str(getattr(option, "instrument_class", "") or "").strip().upper(),
        str(getattr(option, "expiry", "") or "").strip().upper(),
        strike,
        str(getattr(result, "direction", "") or "").strip().upper(),
    )


def _update_live_synthetic_results(results: tuple) -> None:
    global live_synthetic_latest_results
    # Keep only genuinely fresh results and merge by contract/direction. Empty
    # per-symbol scans must not erase other symbols' still-fresh opportunities.
    with _live_synthetic_results_lock:
        combined = _fresh_live_synthetic_results(
            tuple(live_synthetic_latest_results) + tuple(results or ())
        )
        latest = {}
        for result in combined:
            key = _synthetic_result_key(result)
            try:
                timestamp_ns = int(result.option.timestamp_ns)
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            try:
                edge = float(getattr(result, "executable_edge", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                edge = 0.0
            previous = latest.get(key)
            if previous is None or timestamp_ns > previous[0] or (
                timestamp_ns == previous[0] and edge > previous[1]
            ):
                latest[key] = (timestamp_ns, edge, result)
        live_synthetic_latest_results = tuple(item[2] for item in latest.values())


def _start_box_spread_runner_worker(runner):
    """Start the initial Box Spread worker and capture fatal thread errors."""
    errors = []

    def target():
        try:
            runner.run_forever()
        except BaseException as exc:
            runtime_diagnostics.record(
                component="Scanner Runner",
                severity="CRITICAL" if isinstance(exc, (SystemExit, KeyboardInterrupt)) else "ERROR",
                error_type=type(exc).__name__,
                message=str(exc) or repr(exc),
                context={"worker": "live-box-spread-runner"},
            )
            errors.append(exc)
            runner.stop()

    worker = threading.Thread(target=target, name="live-box-spread-runner", daemon=True)
    try:
        setattr(runner, "_runner_worker", worker)
    except Exception:
        pass
    worker.start()
    return worker, errors


async def _live_box_spread_loop() -> None:
    """Supervise Box Spread with staged make-before-break runner handoffs."""
    global live_box_spread_runner, live_box_spread_supervisor, live_box_spread_latest_results
    retry_delay = 5.0
    max_retry_delay = 60.0
    while True:
        master = InstrumentMaster()
        runner = None
        supervisor = None
        try:
            # Box Spread uses its own recent instrument-master cache on download failure.
            # The shared InstrumentMaster behavior for other scanners remains unchanged.
            download_box_spread_instruments(master)
            today = datetime.now(IST).date()
            stock_symbols, index_symbols = set(), set()
            for item in master.instruments:
                segment = str(item.get("exch_seg", "")).strip().upper()
                typ = str(item.get("instrumenttype", "")).strip().upper()
                expiry_text = str(item.get("expiry", "")).strip()
                if not expiry_text:
                    continue
                try:
                    expiry = datetime.strptime(expiry_text.upper(), "%d%b%Y").date()
                except ValueError:
                    try:
                        expiry = datetime.strptime(expiry_text.upper(), "%d%b%y").date()
                    except ValueError:
                        continue
                if expiry < today:
                    continue
                name = str(item.get("name", "")).strip().upper()
                if not name:
                    continue
                # Live Box Spread is intentionally index-only; stock contracts are not subscribed.
                if typ == "OPTIDX" and name in INDEX_EDGE_THRESHOLDS and name in OPTION_STRATEGY_INDEX_SYMBOLS and name not in BOX_SPREAD_EXCLUDED_INDEX_SYMBOLS:
                    expected_segment = "BFO" if name in BSE_BOX_INDEX_SYMBOLS else "NFO"
                    if segment == expected_segment:
                        index_symbols.add(name)

            selected_symbols = _get_box_spread_index_selection()
            weekly_only_symbols = _get_box_spread_weekly_only_symbols()
            targets, expiry_types = _box_spread_targets_from_master(master, selected_symbols, weekly_only_symbols)
            set_live_box_expiry_types(expiry_types)
            set_live_box_websocket_expiry_types(expiry_types)
            if not targets:
                app_logger.warning(
                    "Box Spread live runner found no current F&O targets; retrying in {:.1f}s",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)
                continue

            runner = LiveBoxSpreadRunner(
                settings.BACKTEST_DATA_DB,
                targets,
                allowed_stock_symbols=frozenset(stock_symbols),
                future_master=master,
                auth=AngelOneAuth(),
                on_results=lambda results: _update_live_box_spread_results(results),
            )
            live_box_spread_runner = runner
            worker, worker_errors = _start_box_spread_runner_worker(runner)

            def make_staged_runner(new_targets):
                return LiveBoxSpreadRunner(
                    settings.BACKTEST_DATA_DB,
                    new_targets,
                    allowed_stock_symbols=runner.allowed_stock_symbols,
                    future_master=runner.master,
                    auth=AngelOneAuth(),
                    on_results=lambda results: _update_live_box_spread_results(results),
                    consumer_namespace=f"box-stage-{uuid.uuid4().hex[:12]}",
                    publish_enabled=False,
                    publication_gate=runner.publication_gate,
                )

            def set_active_runner(active_runner):
                global live_box_spread_runner, live_box_spread_latest_results
                live_box_spread_runner = active_runner
                # Change global weekly/monthly filters only after the staged
                # runner owns publication; during warm-up the old session's
                # expiry routing must remain untouched.
                active_symbols = {
                    str(target.underlying).strip().upper()
                    for target in active_runner.targets
                }
                active_weekly_only = _get_box_spread_weekly_only_symbols()
                _active_targets, active_expiry_types = _box_spread_targets_from_master(
                    active_runner.master, active_symbols, active_weekly_only
                )
                set_live_box_expiry_types(active_expiry_types)
                set_live_box_websocket_expiry_types(active_expiry_types)
                # Keep the old session's visible results intact during staging.
                # Apply the selected-index filter only after publisher ownership
                # has transferred to this active runner.
                live_box_spread_latest_results = tuple(
                    row for row in live_box_spread_latest_results
                    if str(getattr(getattr(row, "low", None), "underlying", "") or "").strip().upper() in active_symbols
                )

            supervisor = BoxSpreadRunnerSupervisor(
                active_runner=runner,
                active_thread=worker,
                runner_factory=make_staged_runner,
                on_active=set_active_runner,
                readiness_timeout_seconds=90.0,
                readiness_poll_seconds=0.25,
            )
            live_box_spread_supervisor = supervisor

            # Follow the currently active worker. A successful staged handoff
            # swaps this pointer before stopping the old worker, so the loop
            # naturally continues monitoring the replacement thread.
            while True:
                active_runner = supervisor.active_runner
                active_worker = supervisor.active_thread
                if active_runner.stop_event.is_set() or not active_worker.is_alive():
                    break
                await asyncio.sleep(0.25)

            runner = supervisor.active_runner
            if runner.stop_event.is_set():
                if supervisor.is_shutting_down:
                    return
                raise RuntimeError("Active Box Spread runner stopped unexpectedly after handoff")
            if worker_errors:
                raise RuntimeError(f"Box Spread worker failed: {worker_errors[0]}")
            app_logger.warning(
                "Box Spread live runner exited unexpectedly; retrying in {:.1f}s",
                retry_delay,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(
                "Box Spread live runner failed; retrying in {:.1f}s: {}",
                retry_delay, exc,
            )
        finally:
            if supervisor is not None:
                await asyncio.to_thread(supervisor.stop)
            elif runner is not None:
                await asyncio.to_thread(runner.stop)
            if live_box_spread_supervisor is supervisor:
                live_box_spread_supervisor = None
            if live_box_spread_runner is runner or (
                supervisor is not None and live_box_spread_runner is supervisor.active_runner
            ):
                live_box_spread_runner = None
        await asyncio.sleep(retry_delay)
        retry_delay = min(max_retry_delay, retry_delay * 2.0)


def _box_spread_result_key(result) -> tuple:
    low = getattr(result, "low", None)
    high = getattr(result, "high", None)
    if low is None or high is None:
        return ("", "", "", "", "", "")
    try:
        low_strike = format(float(getattr(low, "strike", "")), ".12g")
        high_strike = format(float(getattr(high, "strike", "")), ".12g")
    except (TypeError, ValueError, OverflowError):
        low_strike = str(getattr(low, "strike", "") or "").strip().upper()
        high_strike = str(getattr(high, "strike", "") or "").strip().upper()
    return (
        str(getattr(low, "underlying", "") or "").strip().upper(),
        str(getattr(low, "instrument_class", "") or "").strip().upper(),
        str(getattr(low, "expiry", "") or "").strip().upper(),
        low_strike,
        high_strike,
        str(getattr(result, "direction", "") or "").strip().upper(),
    )


def _fresh_live_box_spread_results(results: tuple, max_age_seconds: float = 5.0) -> tuple:
    now_ns = time_module.time_ns()
    cutoff_ns = now_ns - int(max_age_seconds * 1_000_000_000)
    fresh = []
    for result in results:
        try:
            low_ts = int(result.low.timestamp_ns)
            high_ts = int(result.high.timestamp_ns)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if not (cutoff_ns <= low_ts <= now_ns and cutoff_ns <= high_ts <= now_ns):
            continue
        if abs(low_ts - high_ts) > 3_000_000_000:
            continue
        fresh.append(result)
    return tuple(fresh)


def _update_live_box_spread_results(results: tuple) -> None:
    global live_box_spread_latest_results
    # Each scanner callback is scoped to a single quote bucket. Merge fresh
    # rows across symbols instead of letting one symbol erase every other one.
    selected_symbols = set(_get_box_spread_active_index_symbols())
    selected_results = tuple(
        result for result in (results or ())
        if str(getattr(getattr(result, "low", None), "underlying", "") or "").strip().upper() in selected_symbols
    )
    combined = _fresh_live_box_spread_results(
        tuple(live_box_spread_latest_results) + selected_results
    )
    latest = {}
    for result in combined:
        key = _box_spread_result_key(result)
        try:
            timestamp_ns = max(int(result.low.timestamp_ns), int(result.high.timestamp_ns))
            edge = float(getattr(result, "executable_edge", 0) or 0)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        previous = latest.get(key)
        if previous is None or timestamp_ns > previous[0] or (
            timestamp_ns == previous[0] and edge > previous[1]
        ):
            latest[key] = (timestamp_ns, edge, result)
    live_box_spread_latest_results = tuple(item[2] for item in latest.values())
    if selected_results:
        publish_live_box_spread_opportunities(selected_results)


async def _live_calendar_spread_loop() -> None:
    """Supervise Calendar Spread collector exits with bounded exponential retry."""
    global live_calendar_spread_runner
    retry_delay = 5.0
    max_retry_delay = 60.0
    while True:
        collector = LiveCalendarSpreadOneSecondCollector(
            settings.BACKTEST_DATA_DB,
            auth=AngelOneAuth(),
            instrument_master=instrument_master,
            on_observation=lambda payload: live_calendar_spread_scanner.observe(
                payload, session_factory=SessionLocal
            ),
        )
        live_calendar_spread_runner = collector
        try:
            worker_errors: list[BaseException] = []
            await _run_live_runner_in_daemon_thread(
                collector,
                name="live-calendar-spread-runner",
                error_holder=worker_errors,
                stop_on_exit=False,
            )
            if collector.stop_event.is_set():
                return
            app_logger.warning(
                "Live Calendar Spread runner exited unexpectedly; retrying in {:.1f}s",
                retry_delay,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(
                "Live Calendar Spread runner failed; retrying in {:.1f}s: {}",
                retry_delay, exc,
            )
        finally:
            _stop_live_runner_nonblocking(collector, name="live-calendar-final-stop")
            if live_calendar_spread_runner is collector:
                live_calendar_spread_runner = None
        await asyncio.sleep(retry_delay)
        retry_delay = min(max_retry_delay, retry_delay * 2.0)


def _sync_contract_master_snapshot(database_path: str, snapshot_date):
    catalog = ContractMasterCatalog(database_path)
    try:
        syncer = DailyContractMasterSync(catalog)
        return syncer.sync(snapshot_date=snapshot_date)
    finally:
        catalog.close()


async def _contract_master_sync_loop() -> None:
    interval = max(3600, settings.BACKTEST_CONTRACT_MASTER_SYNC_INTERVAL_SECONDS)
    app_logger.info(f"Contract-master auto-sync started: every {interval}s")
    while True:
        try:
            now = datetime.now(IST)
            result = await asyncio.to_thread(
                _sync_contract_master_snapshot,
                settings.BACKTEST_CONTRACT_DB,
                now.date(),
            )
            if result.skipped:
                app_logger.debug(f"Contract-master snapshot already present for {result.snapshot_date}")
            else:
                app_logger.info(f"Contract-master snapshot saved: {result.snapshot_date} ({result.records} stock futures)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(f"Contract-master auto-sync failed: {exc}")
        await asyncio.sleep(interval)


APP_UPDATE_MANIFEST = Path(__file__).resolve().parent / "app_update_manifest.json"

@app.get("/api/v1/app/update")
def app_update():
    """Public Android update metadata. Release automation writes the manifest after publishing."""
    payload = {
        "platform": "android",
        "version_code": settings.APP_UPDATE_VERSION_CODE,
        "version_name": settings.APP_UPDATE_VERSION_NAME,
        "release_notes": settings.APP_UPDATE_NOTES,
        "apk_url": settings.APP_UPDATE_APK_URL,
        "sha256": settings.APP_UPDATE_SHA256,
        "mandatory": settings.APP_UPDATE_MANDATORY,
    }
    if APP_UPDATE_MANIFEST.exists():
        try:
            payload.update(json.loads(APP_UPDATE_MANIFEST.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            app_logger.warning("Android update manifest could not be read: %s", exc)
    return payload


STRATEGY_WORKSPACES = [
    {
        "id": "cash-future", "name": "Cash–Future", "version": "1", "enabled": True,
        "screen": "cash_future_scanner", "data_mode": "LIVE 1s",
        "execution_mode": "SCANNER ONLY", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "HISTORICAL", "BACKDATE", "BACKTEST", "REPLAY", "RESULTS"],
        "live_route": "/api/v1/scanner/cash-future/live/fast",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "calendar-spread", "name": "Calendar Spread", "version": "1", "enabled": True,
        "screen": "calendar_spread", "data_mode": "LIVE 1s",
        "execution_mode": "SCANNER ONLY", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "RESULTS"],
        "live_route": "/api/v1/scanner/calendar-spread/live",
        "workspace_route": "/api/v1/backtesting/calendar-spread"
    },
    {
        "id": "synthetic-future-cash-carry", "name": "Synthetic Future / Cash Carry", "version": "1", "enabled": True,
        "screen": "synthetic_cash_carry", "data_mode": "LIVE",
        "execution_mode": "SCANNER ONLY", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "RESULTS"],
        "live_route": "/api/v1/scanner/synthetic-cash-carry/live",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "box-spread", "name": "Box Spread", "version": "1", "enabled": True,
        "screen": "box_spread", "data_mode": "LIVE",
        "execution_mode": "SCANNER ONLY", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "RESULTS"],
        "live_route": "/api/v1/scanner/box-spread/live",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "full-fno", "name": "Full F&O Backtest", "version": "1", "enabled": True,
        "screen": "full_fno", "data_mode": "ACCUMULATED LIVE",
        "execution_mode": "BACKTEST ONLY", "live_orders": False,
        "capabilities": ["HISTORICAL", "BACKDATE", "BACKTEST", "REPLAY", "RESULTS"],
        "live_route": None,
        "workspace_route": "/api/v1/backtesting/full-fno/start"
    },
]


@app.get("/api/v1/app/strategies")
def app_strategies():
    """Backend-owned strategy workspace registry used by Web and Android."""
    return {"strategies": STRATEGY_WORKSPACES}


@app.get("/api/v1/app/strategies/{strategy_id}/workspace")
def app_strategy_workspace(strategy_id: str):
    for workspace in STRATEGY_WORKSPACES:
        if workspace["id"] == strategy_id:
            return {"status": "success", "workspace": workspace}
    raise HTTPException(status_code=404, detail=f"Strategy workspace not found: {strategy_id}")


@app.get("/")
def root():
    return {"message": "Algo Trading Platform is running", "environment": settings.environment, "version": "0.1.0"}


class SyntheticUniverseSelection(BaseModel):
    selection: str


class BoxSpreadIndexSelectionRequest(BaseModel):
    symbols: list[str]
    weekly_only_symbols: list[str] | None = None


@app.get("/api/v1/scanner/box-spread/index-selection")
def get_box_spread_index_selection():
    selected = _get_box_spread_index_selection()
    weekly_only = _get_box_spread_weekly_only_symbols()
    runner = live_box_spread_runner
    runtime = runner.snapshot() if runner is not None else {"running": False, "readiness": {"state": "runner_stopped"}}
    runtime["supervisor"] = live_box_spread_supervisor.snapshot() if live_box_spread_supervisor is not None else {"handoff": {"state": "not_running"}}
    return {
        "status": "success",
        "symbols": list(selected),
        "weekly_only_symbols": list(weekly_only),
        "available_indices": sorted(OPTION_STRATEGY_INDEX_SYMBOLS),
        "presets": {key: list(value) for key, value in BOX_SPREAD_INDEX_PRESETS.items()},
        "runtime": runtime,
    }


def _box_spread_open_paper_symbols():
    """Return indices with any non-flat paper position across the single-owner ledger.

    A deselected index must keep receiving quotes until every paper leg is flat.
    Read failures are allowed to propagate so the caller can fail closed.
    """
    open_symbols = set()
    with SessionLocal() as db:
        trades = db.query(BoxSpreadPaperTradeRecord).filter(
            BoxSpreadPaperTradeRecord.status.notin_(("CLOSED", "CANCELLED"))
        ).all()
        for trade in trades:
            entries = db.query(BoxSpreadPaperLedgerEntry).filter(
                BoxSpreadPaperLedgerEntry.account_key == trade.account_key,
                BoxSpreadPaperLedgerEntry.trade_id == trade.id,
            ).all()
            signed_by_role = {}
            for entry in entries:
                role = str(entry.role or "").strip().upper()
                if role not in {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}:
                    continue
                signed_by_role[role] = signed_by_role.get(role, 0) + (
                    int(entry.quantity) if str(entry.action).upper() == "BUY" else -int(entry.quantity)
                )
            if any(quantity != 0 for quantity in signed_by_role.values()):
                open_symbols.add(str(trade.underlying or "").strip().upper())
    return open_symbols


def _set_box_spread_index_selection_locked(payload: BoxSpreadIndexSelectionRequest):
    global live_box_spread_latest_results
    try:
        selected = _normalize_box_spread_index_symbols(payload.symbols)
        weekly_only = tuple(sorted({str(item or "").strip().upper() for item in (payload.weekly_only_symbols or []) if str(item or "").strip()}))
        if set(weekly_only) - {"NIFTY", "SENSEX"} or set(weekly_only) - set(selected):
            raise ValueError("weekly-only mode is valid only for selected NIFTY/SENSEX")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc

    current_selection = set(_get_box_spread_index_selection())
    current_weekly_only = set(_get_box_spread_weekly_only_symbols())
    removed_symbols = current_selection - set(selected)
    if removed_symbols:
        try:
            open_symbols = _box_spread_open_paper_symbols()
        except Exception as exc:
            app_logger.exception("Unable to verify open Box Spread paper positions before index deselection")
            raise HTTPException(
                status_code=503,
                detail={"message": "Could not verify open paper positions. Index selection was not changed; retry after the paper ledger is available."},
            ) from exc
        blocked_symbols = sorted(removed_symbols & open_symbols)
        if blocked_symbols:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Cannot deselect an index while its Box Spread paper position is still open. Close or settle every leg first.",
                    "blocked_indices": blocked_symbols,
                },
            )

    runner = live_box_spread_runner
    supervisor = live_box_spread_supervisor
    if supervisor is not None and supervisor.snapshot().get("handoff_in_progress"):
        raise HTTPException(
            status_code=409,
            detail={"message": "A Box Spread feed handoff is already staging. Wait for it to finish before changing the index selection again."},
        )
    prepared_targets = None
    prepared_expiry_types = None
    previous_expiry_types = None
    if runner is not None and not runner.stop_event.is_set():
        if supervisor is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Safe Box Spread handoff supervisor is not ready. Selection was not changed; retry shortly.",
                },
            )
        try:
            # Validate both selections against the same loaded instrument master.
            prepared_targets, prepared_expiry_types = _box_spread_targets_from_master(runner.master, selected, weekly_only)
            _previous_targets, previous_expiry_types = _box_spread_targets_from_master(runner.master, current_selection, current_weekly_only)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail={"message": f"Index selection validation failed; previous feed remains active: {exc}"}) from exc

    saved = _save_box_spread_index_selection(selected, weekly_only)
    switch = {"status": "SAVED", "restart_requested": False, "symbols": list(saved)}
    if runner is not None and prepared_targets is not None:
        if supervisor is not None:
            try:
                def rollback_failed_handoff():
                    _save_box_spread_index_selection(current_selection, current_weekly_only)
                    if previous_expiry_types is not None:
                        set_live_box_expiry_types(previous_expiry_types)
                        set_live_box_websocket_expiry_types(previous_expiry_types)

                switch = supervisor.request_handoff(
                    prepared_targets,
                    on_failed=rollback_failed_handoff,
                )
            except Exception as exc:
                app_logger.exception("Unable to start staged Box Spread runner; restoring previous selection")
                try:
                    _save_box_spread_index_selection(current_selection, current_weekly_only)
                except Exception:
                    app_logger.exception("CRITICAL: failed to restore Box Spread selection after staging startup failure")
                raise HTTPException(
                    status_code=503,
                    detail={"message": "New Box Spread feed could not be staged. Previous live runner remains active; retry after checking feed capacity."},
                ) from exc
            if switch.get("state") != "STAGING":
                try:
                    _save_box_spread_index_selection(current_selection, current_weekly_only)
                except Exception:
                    app_logger.exception("CRITICAL: failed to restore Box Spread selection after rejected handoff")
                raise HTTPException(
                    status_code=409,
                    detail={"message": "Box Spread handoff was not accepted; previous selection has been restored. Retry after the current handoff finishes."},
                )
            switch["restart_requested"] = True
        # Expiry routing is updated by on_active only after the candidate
        # is fully ready and publisher ownership has transferred.

    return {
        "status": "success",
        "symbols": list(saved),
        "weekly_only_symbols": list(weekly_only),
        "switch": switch,
        "message": "Selection saved. Only selected Box Spread indices will be subscribed after the safe session handoff.",
    }


@app.post("/api/v1/scanner/box-spread/index-selection")
def set_box_spread_index_selection(payload: BoxSpreadIndexSelectionRequest):
    # Serialize validation, persistence and staged-runner reservation so two
    # concurrent UI saves cannot persist a selection that was never staged.
    with BOX_SPREAD_INDEX_SELECTION_LOCK:
        return _set_box_spread_index_selection_locked(payload)


@app.get("/api/v1/scanner/synthetic-cash-carry/universe")
def get_synthetic_universe():
    return {
        "status": "success",
        "selection": _live_synthetic_universe_selection,
        "options": ["INDEX", *sorted(NIFTY50_INDEX_SYMBOLS)],
        "meaning": {
            "INDEX": "All supported index underlyings only; no stock options/futures subscribed",
        },
        "top15_stock_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
        "availability": dict(_live_synthetic_universe_status),
    }


@app.post("/api/v1/scanner/synthetic-cash-carry/universe")
def set_synthetic_universe(payload: SyntheticUniverseSelection):
    global _live_synthetic_universe_selection
    selection = str(payload.selection or "").strip().upper()
    allowed = {"INDEX", *NIFTY50_INDEX_SYMBOLS}
    if selection not in allowed:
        raise HTTPException(status_code=422, detail={"message": "Unsupported synthetic scanner universe", "allowed": sorted(allowed)})
    if selection == _live_synthetic_universe_selection:
        return {
            "status": "success",
            "selection": selection,
            "restart_requested": False,
            "top15_stock_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
            "availability": dict(_live_synthetic_universe_status),
        }
    _live_synthetic_universe_selection = selection
    runner = live_synthetic_runner
    restart_requested = runner is not None and not runner.stop_event.is_set()
    if restart_requested:
        _live_synthetic_universe_restart_requested.set()
        runner.stop()
    return {
        "status": "success",
        "selection": selection,
        "restart_requested": restart_requested,
        "top15_stock_symbols": list(TOP15_LIQUID_STOCK_SYMBOLS_ORDERED),
        "availability": dict(_live_synthetic_universe_status),
    }


def _summarize_primary_exchange_feed(common_feed: dict) -> dict:
    """Summarize NSE/NFO tick freshness without allowing unrelated exchanges to mask gaps."""
    raw_ages = common_feed.get("last_exchange_tick_received_age_seconds_by_exchange_type")
    raw_ages = raw_ages if isinstance(raw_ages, dict) else {}
    exchange_freshness = {}
    for exchange_type, exchange_name in ((1, "NSE"), (2, "NFO")):
        raw_age = raw_ages.get(str(exchange_type), raw_ages.get(exchange_type))
        try:
            age = float(raw_age) if raw_age is not None else None
        except (TypeError, ValueError, OverflowError):
            age = None
        if age is not None and (not math.isfinite(age) or age < 0):
            age = None
        exchange_freshness[exchange_name] = {
            "exchange_type": exchange_type,
            "age_seconds": age,
            "status": "NO_DATA" if age is None else "STALE" if age > 5.0 else "LIVE",
        }

    primary_ages = [
        item["age_seconds"] for item in exchange_freshness.values()
        if item["age_seconds"] is not None
    ]
    feed_age_seconds = max(primary_ages) if primary_ages else None
    unconnected = common_feed.get("unconnected_required_groups") or []
    missing_groups = common_feed.get("missing_socket_groups") or []
    if unconnected or missing_groups:
        status = "DISCONNECTED"
    elif any(item["status"] == "STALE" for item in exchange_freshness.values()):
        status = "STALE"
    elif any(item["status"] == "NO_DATA" for item in exchange_freshness.values()):
        status = (
            "WAITING_FOR_TICKS"
            if int(common_feed.get("ticks_received") or 0) == 0
            else "WAITING_FOR_PRIMARY_EXCHANGE_TICKS"
        )
    else:
        status = "LIVE"
    return {
        "status": status,
        "age_seconds": feed_age_seconds,
        "exchange_freshness": exchange_freshness,
    }


@app.get("/api/v1/market-data/runtime/health")
def market_data_runtime_health():
    """Expose process-local live-feed diagnostics for runtime troubleshooting."""
    manager = shared_common_manager()
    cash_runner = live_cash_future_runner
    calendar_runner = live_calendar_spread_runner
    synthetic_runner = live_synthetic_runner
    box_runner = live_box_spread_runner
    calendar_counts = live_calendar_spread_scanner.counts_snapshot()
    common_feed = manager.snapshot()
    # The synthetic scanner needs both NSE underlying and NFO derivatives.
    # A fresh tick from another exchange must not mask a missing/stale primary feed.
    feed_health = _summarize_primary_exchange_feed(common_feed)
    common_feed["age_seconds"] = feed_health["age_seconds"]
    common_feed["status"] = feed_health["status"]
    common_feed["exchange_freshness"] = feed_health["exchange_freshness"]
    common_feed["required_exchange_types"] = [1, 2]
    return {
        "status": "ok",
        "server_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        "common_feed": common_feed,
        "runners": {
            "cash_future": None if cash_runner is None else cash_runner.snapshot(),
            "calendar_spread": None if calendar_runner is None else calendar_runner.snapshot(),
            "synthetic_arbitrage": None if synthetic_runner is None else synthetic_runner.snapshot(),
            "box_spread": None if box_runner is None else box_runner.snapshot(),
        },
        "scanner": {
            "strategy": "calendar-spread",
            "signals": calendar_counts["signals"],
            "pairs": calendar_counts["pairs"],
            "diagnostics": live_calendar_spread_scanner.diagnostics_snapshot(limit=50)["counters"],
        },
        "cash_future_scanner": live_cash_future_scanner.health(),
    }


@app.get("/api/v1/diagnostics/errors")
def runtime_diagnostic_errors(limit: int = 50):
    """Return real backend/feed/runtime errors captured since process start."""
    return {"status": "ok", "errors": runtime_diagnostics.snapshot(limit=limit), "count": len(runtime_diagnostics.snapshot(limit=1000))}


@app.get("/api/v1/market-data/live-cash-future/health")
def live_cash_future_health_route():
    state = live_cash_future_health()
    scanner = live_cash_future_scanner.health()
    observations = max(1, int(scanner.get("observations", 0)))
    state.update({
        "pair_rate": round(float(scanner.get("pairs", 0)) / observations, 4),
        "scanner_pairs": int(scanner.get("pairs", 0)),
        "scanner_dropped": int(scanner.get("dropped", 0)),
        "scanner_persisted": int(scanner.get("persisted", 0)),
        "coverage": max(int(state.get("coverage", 0)), len({row.get("symbol") for row in live_cash_future_scanner.snapshot(max_age_seconds=30, limit=50) if row.get("symbol")})),
    })
    return state


def _get_iv_scanner_enabled() -> bool:
    with SessionLocal() as db:
        setting = db.get(IVScannerControl, 1)
        return bool(setting.enabled) if setting is not None else False


def _save_iv_scanner_enabled(enabled: bool) -> bool:
    with SessionLocal() as db:
        setting = db.get(IVScannerControl, 1)
        if setting is None:
            setting = IVScannerControl(id=1, enabled=bool(enabled))
            db.add(setting)
        else:
            setting.enabled = bool(enabled)
            setting.updated_at = datetime.now(timezone.utc)
        db.commit()
    return bool(enabled)


def _start_iv_skew_flip_runtime():
    global iv_skew_flip_runtime
    # Serialize start/stop requests so simultaneous UI/API requests cannot
    # create duplicate IV consumers or publish a half-started runtime.
    with _iv_skew_flip_runtime_lock:
        if iv_skew_flip_runtime is not None:
            existing = iv_skew_flip_runtime.snapshot()
            if existing.get("status") == "STOPPING":
                # A timed-out shutdown still owns feed/socket resources. Never
                # report ON or create a second consumer until that worker exits.
                raise RuntimeError(
                    "IV Scanner is still STOPPING; retry ON after shutdown completes"
                )
            if existing.get("status") == "RUNNING":
                return existing
            # A fully stopped runtime may remain registered after an interrupted
            # transition. Clear it before constructing the next isolated runtime.
            iv_skew_flip_runtime = None
        # Install the IV-owned instrument snapshot before validating basket tokens.
        # Previously validation ran before runtime.start() downloaded that snapshot,
        # so a valid basket could be marked unavailable against an empty/stale master.
        # This downloader is isolated and never reads or mutates the Box Spread cache.
        iv_instruments = download_iv_instruments(instrument_master)
        # Runner ON is the user's explicit activation for IV market-data analysis.
        # Load both official index baskets, verify the complete rosters/token mappings,
        # then subscribe only to each index's dynamically ranked top 10 constituents.
        # If basket provenance is missing/stale, keep the IV option scanner running
        # but fail closed for constituent pressure (no partial/guessed basket).
        equity_baskets = {}
        equity_pressure_error = None
        try:
            with SessionLocal() as db:
                basket_records = (
                    db.query(IVConstituentBasketVersion)
                    .filter(IVConstituentBasketVersion.underlying.in_(("NIFTY", "BANKNIFTY")))
                    .all()
                )
            equity_baskets = load_verified_runtime_baskets(
                basket_records,
                as_of=datetime.now(timezone.utc).date(),
                instrument_master=instrument_master,
                underlyings=("NIFTY", "BANKNIFTY"),
                top_n=10,
            )
        except Exception as exc:
            equity_pressure_error = f"{type(exc).__name__}: {exc}"
            app_logger.warning("IV top-10 constituent pressure unavailable: {}", equity_pressure_error)
        runtime = IVSkewFlipRuntime(
            instrument_master=instrument_master,
            equity_pressure_enabled=bool(equity_baskets),
            equity_baskets=equity_baskets,
            equity_pressure_unavailable_reason=equity_pressure_error,
        )
        try:
            snapshot = runtime.start(instruments=iv_instruments)
        except Exception:
            # Keep ownership if rollback could not release a partially
            # registered shared-feed consumer; OFF can retry cleanup.
            cleanup = runtime.stop()
            if cleanup.get("status") != "STOPPED":
                iv_skew_flip_runtime = runtime
            raise
        iv_skew_flip_runtime = runtime
        return snapshot


def _stop_iv_skew_flip_runtime():
    global iv_skew_flip_runtime
    with _iv_skew_flip_runtime_lock:
        if iv_skew_flip_runtime is None:
            return {"status": "STOPPED", "consumer": "iv-skew-flip", "orders_enabled": False}
        runtime = iv_skew_flip_runtime
        snapshot = runtime.stop()
        if iv_skew_flip_runtime is runtime and snapshot.get("status") == "STOPPED":
            iv_skew_flip_runtime = None
        return snapshot


@app.get("/api/v1/scanner/iv-skew-flip/baskets")
def iv_skew_flip_basket_registry():
    """Read-only freshness and provenance status for NIFTY/BANKNIFTY baskets."""
    with SessionLocal() as db:
        records = (
            db.query(IVConstituentBasketVersion)
            .filter(IVConstituentBasketVersion.underlying.in_(("NIFTY", "BANKNIFTY")))
            .order_by(
                IVConstituentBasketVersion.effective_date.desc(),
                IVConstituentBasketVersion.created_at.desc(),
                IVConstituentBasketVersion.id.desc(),
            )
            .all()
        )
    return summarize_basket_registry(records, as_of=datetime.now(timezone.utc).date())


@app.get("/api/v1/scanner/iv-skew-flip/status")
def iv_skew_flip_status():
    enabled = _get_iv_scanner_enabled()
    with _iv_skew_flip_runtime_lock:
        runtime = None if iv_skew_flip_runtime is None else iv_skew_flip_runtime.snapshot()
    return {"status": "ok", "desired_state": "ON" if enabled else "OFF", "saved_enabled": enabled,
            "runner_status": runtime.get("status", "STOPPED") if runtime else "STOPPED",
            "runtime": runtime, "orders_enabled": False}


@app.post("/api/v1/scanner/iv-skew-flip/config")
def save_iv_skew_flip_config(payload: dict):
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled must be a boolean")
    # Keep runtime transition and persisted desired state in one critical
    # section; otherwise a racing OFF/ON request could leave them contradictory.
    with _iv_skew_flip_runtime_lock:
        if enabled:
            # Persist ON first. If persistence fails, the runner is never
            # started, so a database outage cannot leave an unsaved live runner.
            try:
                _save_iv_scanner_enabled(True)
            except Exception as exc:
                app_logger.exception("IV Scanner ON state could not be persisted")
                raise HTTPException(status_code=503, detail={"message": "IV Scanner was not started because ON state could not be saved.", "reason": str(exc)}) from exc
            try:
                snapshot = _start_iv_skew_flip_runtime()
            except Exception as exc:
                # Compensate the desired state if backend startup fails.
                try:
                    _save_iv_scanner_enabled(False)
                except Exception:
                    app_logger.exception("IV Scanner failed to roll back persisted ON state after startup failure")
                app_logger.exception("IV Scanner could not be enabled")
                raise HTTPException(status_code=503, detail={"message": "IV Scanner ON save failed because backend runner did not start.", "reason": str(exc)}) from exc
        else:
            # Persist OFF first. If the database is unavailable, leave the
            # currently running scanner untouched rather than create a
            # STOPPED runtime with a persisted ON state that restarts later.
            try:
                _save_iv_scanner_enabled(False)
            except Exception as exc:
                app_logger.exception("IV Scanner OFF state could not be persisted")
                raise HTTPException(status_code=503, detail={"message": "IV Scanner was not stopped because OFF state could not be saved.", "reason": str(exc)}) from exc
            snapshot = _stop_iv_skew_flip_runtime()
        return {"status": "ok", "desired_state": "ON" if enabled else "OFF", "saved_enabled": enabled,
                "runner_status": snapshot.get("status", "STOPPED"),
                "runtime": snapshot, "orders_enabled": False}


@app.post("/api/v1/scanner/iv-skew-flip/start")
def start_iv_skew_flip_scanner():
    with _iv_skew_flip_runtime_lock:
        try:
            _save_iv_scanner_enabled(True)
            try:
                snapshot = _start_iv_skew_flip_runtime()
            except Exception:
                try:
                    _save_iv_scanner_enabled(False)
                except Exception:
                    app_logger.exception("IV Scanner failed to roll back persisted ON state after startup failure")
                raise
            return snapshot
        except Exception as exc:
            app_logger.exception("IV Skew / IV Flip scanner startup failed")
            raise HTTPException(status_code=503, detail={"message": "IV scanner could not start; verify Angel One session, shared feed and instrument master.", "reason": str(exc)}) from exc


@app.post("/api/v1/scanner/iv-skew-flip/stop")
def stop_iv_skew_flip_scanner():
    with _iv_skew_flip_runtime_lock:
        _save_iv_scanner_enabled(False)
        return _stop_iv_skew_flip_runtime()


@app.get("/api/v1/scanner/iv-skew-flip/validation")
def iv_skew_flip_validation(underlying: str = "NIFTY", expiry_type: str | None = None):
    """Read-only descriptive forward outcomes from the dedicated spot-tick history."""
    symbol = str(underlying or "").strip().upper()
    if symbol not in {"NIFTY", "BANKNIFTY"}:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    expiry = str(expiry_type).strip().upper() if expiry_type else None
    if expiry is not None and expiry not in {"WEEKLY", "MONTHLY"}:
        raise HTTPException(status_code=422, detail="expiry_type must be WEEKLY or MONTHLY")
    db = SessionLocal()
    try:
        rows = load_persisted_forward_outcomes(
            db, underlying=symbol, expiry_type=expiry,
            horizons_minutes=(10, 30, 60),
        )
        event_query = db.query(IVSignalEvent).filter(
            IVSignalEvent.underlying == symbol,
            IVSignalEvent.state.in_(("PRE_MOVE", "CONFIRMED")),
            IVSignalEvent.direction.in_(("BULLISH", "BEARISH")),
        )
        if expiry:
            event_query = event_query.filter(IVSignalEvent.expiry_type == expiry)
        stored_events = event_query.order_by(IVSignalEvent.event_timestamp_ns.asc()).all()
        eligible_events = [
            SignalEvent(
                event_timestamp_ns=item.event_timestamp_ns,
                spot_price=item.spot_price,
                direction=item.direction,
                state=item.state,
                underlying=item.underlying,
                spot_timestamp_ns=item.spot_timestamp_ns,
            )
            for item in stored_events
        ]
        return {
            "status": "ok",
            "underlying": symbol,
            "expiry_type": expiry,
            "source": "iv_spot_observations",
            "eligible_events": len(eligible_events),
            "events_with_measured_outcomes": len({
                (event.event_timestamp_ns, event.direction, event.state, event.underlying)
                for event, _ in rows
            }),
            "summary": summarize_forward_outcomes(eligible_events, rows),
            "date_wise": summarize_walk_forward_by_date(rows, eligible_events),
            "orders_enabled": False,
            "interpretation": "descriptive forward returns only; not accuracy, profitability, or out-of-sample validation",
        }
    finally:
        db.close()


@app.get("/health")
def health_check():
    try:
        check_database()
    except Exception as exc:
        runtime_diagnostics.record(
            component="Database",
            error_type=type(exc).__name__,
            message=str(exc),
            endpoint="/health",
            context={"event": "health_check"},
        )
        app_logger.error(f"Health check database failure: {exc}")
        return {"status": "degraded", "app": settings.app_name, "version": "0.1.0", "database": "Disconnected"}
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0", "database": "Connected"}




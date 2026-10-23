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
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logger import app_logger
from app.core.diagnostics import runtime_diagnostics
from app.core.exceptions import TradingAppException, trading_exception_handler, global_exception_handler
from app.core.database import engine, Base, SessionLocal, check_database
from app.core.schema_migrations import run_schema_migrations
from app.models import User, Instrument, Session, Position, SystemLog
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
from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner, BoxSpreadLiveTarget
from app.market_data.instruments import InstrumentMaster
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS, TOP15_LIQUID_STOCK_SYMBOLS_ORDERED, OPTION_STRATEGY_INDEX_SYMBOLS, NIFTY50_INDEX_SYMBOLS
BSE_BOX_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.market_data.routes import router as market_data_router, configure_live_cash_future_runner_snapshot
from app.scanner.routes import router as scanner_router, full_fno_router, configure_live_cash_future_snapshot
from app.scanner.auto_routes import router as auto_scanner_router, discover_cash_future_symbols
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
from app.scanner.calendar_spread_routes import router as calendar_spread_scanner_router, configure as configure_calendar_spread_scanner, configure_feed_snapshot as configure_calendar_spread_feed_snapshot
from app.scanner.live_synthetic_routes import router as live_synthetic_router, configure as configure_live_synthetic, configure_diagnostics as configure_live_synthetic_diagnostics, configure_pairs as configure_live_synthetic_pairs
from app.scanner.live_synthetic_trace_routes import router as live_synthetic_trace_router
from app.scanner.live_box_spread_routes import router as live_box_spread_router, configure as configure_live_box_spread, set_expiry_types as set_live_box_expiry_types
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

run_schema_migrations()
Base.metadata.create_all(bind=engine)

# Reuse one process-local instrument-master manager for live feed/API resolution.
# Its own lazy cache prevents repeated OpenAPIScripMaster downloads.
instrument_master = InstrumentMaster()
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
configure_live_synthetic(lambda: live_synthetic_latest_results)
configure_live_synthetic_diagnostics(lambda: None if live_synthetic_runner is None else live_synthetic_runner.snapshot())
configure_live_synthetic_pairs(lambda: None if live_synthetic_runner is None else live_synthetic_runner.pair_monitor_snapshot())
configure_live_box_spread(lambda: live_box_spread_latest_results)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task
    app_logger.info(f"{settings.app_name} started successfully in {settings.environment} mode")
    # Recovery is intentionally deferred until application startup so the schema
    # migration module has no dependency on scanner/backtest job modules.
    from app.scanner.backtest_jobs import recover_interrupted_jobs
    recover_interrupted_jobs()
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
    try:
        yield
    finally:
        # Signal blocking worker threads before cancelling their asyncio wrappers.
        # This makes to_thread(run_forever) unwind promptly during service stop.
        _signal_live_runner_shutdown()
        for task in (_history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task):
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
app.include_router(full_fno_router)
app.include_router(auto_scanner_router)
app.include_router(alert_router)
if settings.BACKTESTING_ENABLED:
    app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))
app.include_router(calendar_spread_scanner_router)
app.include_router(live_synthetic_router)
app.include_router(live_synthetic_trace_router)
app.include_router(live_box_spread_router)
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
        master = InstrumentMaster()
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
                allowed_stock_symbols=frozenset(stock_symbols),
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


async def _live_box_spread_loop() -> None:
    """Supervise the Box Spread runner with bounded retry backoff."""
    global live_box_spread_runner, live_box_spread_latest_results
    retry_delay = 5.0
    max_retry_delay = 60.0
    while True:
        master = InstrumentMaster()
        runner = None
        try:
            master.download()
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
                # Build the scanner universe from option contracts, not futures.
                # Futures are not live-subscribed for Box Spread; option-only rows
                # must remain eligible even when a future row is missing or renamed.
                if typ == "OPTSTK" and segment == "NFO" and name in TOP15_LIQUID_STOCK_SYMBOLS:
                    stock_symbols.add(name)
                elif typ == "OPTIDX" and name in OPTION_STRATEGY_INDEX_SYMBOLS:
                    expected_segment = "BFO" if name in BSE_BOX_INDEX_SYMBOLS else "NFO"
                    if segment == expected_segment:
                        index_symbols.add(name)
            stock_symbols = [
                symbol for symbol in TOP15_LIQUID_STOCK_SYMBOLS_ORDERED
                if symbol in stock_symbols
            ]
            # Select option expiries directly from the option master. For indices,
            # subscribe the nearest available weekly AND monthly expiry (when distinct);
            # stocks remain on their nearest listed expiry. No futures rows/ticks are used.
            targets_list = []
            expiry_types = {}

            def listed_option_expiries(symbol, instrument_class):
                expected_segment = "BFO" if instrument_class == "INDEX" and symbol in BSE_BOX_INDEX_SYMBOLS else "NFO"
                expected_type = "OPTSTK" if instrument_class == "STOCK" else "OPTIDX"
                found = {}
                for contract in master.instruments:
                    if str(contract.get("exch_seg", "")).strip().upper() != expected_segment:
                        continue
                    if str(contract.get("instrumenttype", "")).strip().upper() != expected_type:
                        continue
                    if str(contract.get("name", "")).strip().upper() != symbol:
                        continue
                    raw_expiry = str(contract.get("expiry", "")).strip()
                    parsed = None
                    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
                        try:
                            parsed = datetime.strptime(raw_expiry.upper(), fmt).date()
                            break
                        except ValueError:
                            continue
                    if parsed is not None and parsed >= today:
                        found.setdefault(parsed, raw_expiry)
                dates = sorted(found)
                monthly_dates = {
                    max((day for day in dates if day.year == year and day.month == month), default=None)
                    for year, month in {(day.year, day.month) for day in dates}
                }
                monthly_dates.discard(None)
                for day, raw_expiry in found.items():
                    expiry_types[(symbol, raw_expiry.upper())] = "MONTHLY" if day in monthly_dates else "WEEKLY"
                return [(day, found[day], "MONTHLY" if day in monthly_dates else "WEEKLY") for day in dates]

            for symbol in stock_symbols:
                expiries = listed_option_expiries(symbol, "STOCK")
                if expiries:
                    day, expiry, _kind = expiries[0]
                    targets_list.append(BoxSpreadLiveTarget(symbol, "STOCK", expiry=expiry))
            for symbol in sorted(index_symbols):
                expiries = listed_option_expiries(symbol, "INDEX")
                if not expiries:
                    continue
                nearest_weekly = next((item for item in expiries if item[2] == "WEEKLY"), None)
                nearest_monthly = next((item for item in expiries if item[2] == "MONTHLY"), None)
                selected = []
                if nearest_weekly:
                    selected.append(nearest_weekly)
                if nearest_monthly and (not nearest_weekly or nearest_monthly[0] != nearest_weekly[0]):
                    selected.append(nearest_monthly)
                if not selected:
                    selected.append(expiries[0])
                for _day, expiry, _kind in selected:
                    targets_list.append(BoxSpreadLiveTarget(symbol, "INDEX", expiry=expiry))
            set_live_box_expiry_types(expiry_types)
            targets = tuple(targets_list)
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
            await _run_live_runner_in_daemon_thread(
                runner, name="live-box-spread-runner"
            )
            if runner.stop_event.is_set():
                return
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
            if runner is not None:
                _stop_live_runner_nonblocking(
                    runner, name="live-box-spread-final-stop"
                )
            if live_box_spread_runner is runner:
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
    combined = _fresh_live_box_spread_results(
        tuple(live_box_spread_latest_results) + tuple(results or ())
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




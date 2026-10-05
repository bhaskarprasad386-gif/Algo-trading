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
import time as time_module
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logger import app_logger
from app.core.exceptions import TradingAppException, trading_exception_handler, global_exception_handler
from app.core.database import engine, Base, SessionLocal, check_database
from app.core.schema_migrations import run_schema_migrations
from app.models import User, Instrument, Order, Session, Position, SystemLog, TradingAccount
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult
from app.models.live_calendar_spread_paper_position import LiveCalendarSpreadPaperPosition
from app.algo.auth import AngelOneAuth
from app.market_data.websocket import MarketDataWebSocket
from app.market_data.live_cash_future_stream import live_cash_future_health
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
from app.market_data.common_strategy_feed import shared_common_manager
from app.market_data.live_calendar_spread_stream import LiveCalendarSpreadOneSecondCollector
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget, select_nearest_option_expiry
from app.market_data.live_synthetic_underlying import filter_resolvable_index_symbols
from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner, BoxSpreadLiveTarget
from app.market_data.instruments import InstrumentMaster
from app.market_data.nifty50_universe import NIFTY50_STOCK_SYMBOLS, NIFTY50_INDEX_SYMBOLS
BSE_BOX_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.order_engine.routes import router as orders_router
from app.market_data.routes import router as market_data_router
from app.scanner.routes import router as scanner_router, full_fno_router
from app.scanner.auto_routes import router as auto_scanner_router, discover_cash_future_symbols
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
from app.scanner.calendar_spread_routes import router as calendar_spread_scanner_router, configure as configure_calendar_spread_scanner
from app.scanner.live_synthetic_routes import router as live_synthetic_router, configure as configure_live_synthetic
from app.scanner.live_box_spread_routes import router as live_box_spread_router, configure as configure_live_box_spread
from app.intelligence.routes import router as intelligence_router
from app.execution.calendar_spread_paper_routes import router as calendar_spread_paper_router
from app.execution.box_spread_paper_routes import router as box_spread_paper_router, cycle as box_spread_paper_cycle
from app.execution.paper_routes import router as paper_execution_router
from app.execution.live_paper_routes import router as live_paper_execution_router
from app.auto.routes import router as global_auto_router
from app.alert_routes import router as alert_router
from app.live_paper_routes import router as live_paper_router
from app.auto.live_paper import LivePaperTradeService, is_fresh_market_timestamp
from app.models.live_paper_trade import LivePaperTrade
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

# Reuse one process-local instrument-master manager for all dashboard/API WebSockets.
# Its own lazy cache prevents repeated OpenAPIScripMaster downloads.
instrument_master = InstrumentMaster()
live_cash_future_scanner = LiveCashFutureScanner()
live_cash_future_runner: LiveCashFutureCommonRunner | None = None
live_calendar_spread_scanner = LiveCalendarSpreadScanner(
    minimum_gap_points=settings.LIVE_CALENDAR_SPREAD_MIN_GAP_POINTS,
    minimum_gross_profit=settings.LIVE_CALENDAR_SPREAD_MIN_GROSS_PROFIT,
)
configure_calendar_spread_scanner(live_calendar_spread_scanner)
live_synthetic_latest_results: tuple = ()
live_synthetic_runner: LiveSyntheticRunner | None = None
live_box_spread_latest_results: tuple = ()
live_box_spread_runner: LiveBoxSpreadRunner | None = None
configure_live_synthetic(lambda: live_synthetic_latest_results)
configure_live_box_spread(lambda: live_box_spread_latest_results)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task, _paper_box_spread_cycle_task, _live_paper_monitor_task
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
    if settings.PAPER_BOX_SPREAD_AUTO_CYCLE_ENABLED and _paper_box_spread_cycle_task is None:
        _paper_box_spread_cycle_task = asyncio.create_task(_paper_box_spread_cycle_loop())
    if settings.LIVE_PAPER_MONITOR_ENABLED and _live_paper_monitor_task is None:
        _live_paper_monitor_task = asyncio.create_task(_live_paper_monitor_loop())
    try:
        yield
    finally:
        # Signal blocking worker threads before cancelling their asyncio wrappers.
        # This makes to_thread(run_forever) unwind promptly during service stop.
        _signal_live_runner_shutdown()
        for task in (_history_collector_task, _contract_master_sync_task, _live_cash_future_task, _live_calendar_spread_task, _live_synthetic_task, _live_box_spread_task, _paper_box_spread_cycle_task, _live_paper_monitor_task):
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
                _paper_box_spread_cycle_task,
                _live_paper_monitor_task,
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
        _paper_box_spread_cycle_task = None
        _live_paper_monitor_task = None
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
app.include_router(orders_router)
app.include_router(arbitrage_router)
app.include_router(instruments_router)
app.include_router(market_data_router)
app.include_router(scanner_router)
app.include_router(full_fno_router)
app.include_router(auto_scanner_router)
app.include_router(paper_execution_router)
app.include_router(live_paper_execution_router)
app.include_router(global_auto_router)
app.include_router(alert_router)
app.include_router(live_paper_router)
if settings.BACKTESTING_ENABLED:
    app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))
app.include_router(calendar_spread_paper_router)
app.include_router(box_spread_paper_router)
app.include_router(calendar_spread_scanner_router)
app.include_router(live_synthetic_router)
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
      const r=await fetch(`${api}/api/v1/scanner/cash-future/live/fast?limit=50`,{cache:'no-store'});
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


@app.websocket("/ws/market-data/{symbol}")
async def market_data_websocket(websocket: WebSocket, symbol: str):
    await websocket.accept()
    client = MarketDataWebSocket()
    try:
        instrument = instrument_master.get_instrument(symbol.strip().upper(), "NSE")
        if not instrument:
            await websocket.send_json({"status": "error", "detail": f"Instrument not found: NSE {symbol.strip().upper()}"})
            return
        messages = asyncio.Queue()

        def on_data(message):
            try:
                asyncio.get_running_loop().call_soon_threadsafe(messages.put_nowait, message)
            except RuntimeError:
                pass

        client.connect(exchange_type=1, tokens=[str(instrument["token"])], on_data=on_data)
        while True:
            message = await messages.get()
            await websocket.send_json(message if isinstance(message, dict) else {"data": message})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        app_logger.error(f"Market-data WebSocket error for {symbol}: {exc}")
        try:
            await websocket.send_json({"status": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        client.close()


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
            synthetic_snapshot = live_synthetic_latest_results
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
                    "paper_execution": "OFF",
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
_paper_box_spread_cycle_task: asyncio.Task | None = None
_live_paper_monitor_task: asyncio.Task | None = None
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


async def _run_live_runner_in_daemon_thread(runner, *, name: str) -> None:
    """Run a long-lived blocking market runner outside asyncio's default executor.

    Live runners are owned by daemon threads. Their explicit stop events remain
    authoritative, while cleanup is deliberately non-blocking for asyncio
    shutdown.
    """
    worker = threading.Thread(target=runner.run_forever, name=name, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            await asyncio.sleep(0.25)
    finally:
        _stop_live_runner_nonblocking(runner, name=f"{name}-stop")


async def _live_cash_future_loop() -> None:
    """Supervise the live scanner feed and retry transient broker/startup failures."""
    global live_cash_future_runner
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
            await _run_live_runner_in_daemon_thread(
                runner, name="live-cash-future-runner"
            )
            if not runner.stop_event.is_set():
                app_logger.warning("Live Cash-Future runner exited unexpectedly; retrying in 5s")
                await asyncio.sleep(5.0)
            else:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error("Live Cash-Future runner failed; retrying in 5s: %s", exc)
            await asyncio.sleep(5.0)
        finally:
            _stop_live_runner_nonblocking(runner, name="live-cash-future-final-stop")
            if live_cash_future_runner is runner:
                live_cash_future_runner = None

async def _live_synthetic_loop() -> None:
    """Subscribe only to the requested live option/future universe."""
    global live_synthetic_runner, live_synthetic_latest_results
    master = InstrumentMaster()
    try:
        master.download()
        today = datetime.now(IST).date()
        nifty50_stocks: set[str] = set()
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
            if typ == "FUTSTK" and segment == "NFO" and name in NIFTY50_STOCK_SYMBOLS:
                nifty50_stocks.add(name)
            elif typ == "FUTIDX" and segment in {"NFO", "BFO"}:
                index_symbols.add(name)
            elif typ == "FUTCOM" and segment == "MCX":
                commodity_symbols.add(name)

        stock_symbols = sorted(nifty50_stocks)
        raw_index_symbols = sorted(index_symbols)
        index_symbols = list(filter_resolvable_index_symbols(master, raw_index_symbols))
        missing_index_underlyings = sorted(set(raw_index_symbols) - set(index_symbols))
        commodity_symbols = sorted(commodity_symbols)
        if missing_index_underlyings:
            app_logger.warning(
                "Synthetic live index underlyings skipped because no concrete Angel token exists: %s",
                ", ".join(missing_index_underlyings),
            )

        target_specs = (
            [(symbol, "STOCK") for symbol in stock_symbols]
            + [(symbol, "INDEX") for symbol in index_symbols]
            + [(symbol, "COMMODITY") for symbol in commodity_symbols]
        )
        targets = []
        missing_expiry = []
        for symbol, instrument_class in target_specs:
            expiry = select_nearest_option_expiry(
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
        if missing_expiry:
            app_logger.warning(
                "Synthetic live targets skipped because no current option expiry exists: %s",
                ", ".join(sorted(set(missing_expiry))),
            )
        if not targets:
            app_logger.warning("Synthetic live runner found no eligible NIFTY50/index/MCX option targets")
            return

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
    finally:
        runner = live_synthetic_runner
        if runner is not None:
            _stop_live_runner_nonblocking(runner, name="live-synthetic-final-stop")
        live_synthetic_runner = None

def _update_live_synthetic_results(results: tuple) -> None:
    global live_synthetic_latest_results
    live_synthetic_latest_results = tuple(results)


def _run_box_spread_paper_cycle_once() -> int:
    now = datetime.now(IST)
    if not (MARKET_OPEN <= now.time() <= MARKET_CLOSE):
        return 0
    db = SessionLocal()
    try:
        accounts = db.query(TradingAccount).filter(
            TradingAccount.is_active.is_(True),
            TradingAccount.mode == "PAPER",
        ).all()
        processed = 0
        for account in accounts:
            try:
                result = box_spread_paper_cycle(
                    lots=max(1, int(account.box_spread_auto_lots)),
                    min_pnl=settings.PAPER_BOX_SPREAD_AUTO_CYCLE_MIN_PNL,
                    user=account.user_id,
                    db=db,
                )
                processed += 1
                app_logger.debug("Box Spread paper cycle user=%s: %s", account.user_id, result.get("status", "unknown"))
            except Exception as exc:
                db.rollback()
                app_logger.error("Box Spread paper cycle failed for user=%s: %s", account.user_id, exc)
        return processed
    finally:
        db.close()


async def _paper_box_spread_cycle_loop() -> None:
    interval = max(1, settings.PAPER_BOX_SPREAD_AUTO_CYCLE_INTERVAL_SECONDS)
    app_logger.info("Box Spread paper auto-cycle started: every %ss", interval)
    while True:
        try:
            await asyncio.to_thread(_run_box_spread_paper_cycle_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error("Box Spread paper auto-cycle failed: %s", exc)
        await asyncio.sleep(interval)


def _executable_paper_pnl(trade: LivePaperTrade, row) -> float | None:
    """Mark an alert's original legs against current executable exit quotes."""
    # Defense-in-depth: validate every field that participates in executable
    # quote/P&L arithmetic. Do not require the full lifecycle persistence
    # contract here: this helper is also used by monitor callers with a
    # lightweight trade projection, while malformed numeric/leg data must
    # still fail closed.
    try:
        import math
        from collections.abc import Mapping
        lot_size = float(trade.lot_size)
        lots = float(trade.lots)
        if (
            not math.isfinite(lot_size) or not lot_size.is_integer() or lot_size <= 0
            or not math.isfinite(lots) or not lots.is_integer() or lots <= 0
        ):
            return None
        legs = json.loads(trade.legs_json or "[]")
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return None
    if not isinstance(legs, list) or not legs:
        return None

    def q(bid, ask, side):
        try:
            bid, ask = float(bid), float(ask)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            not math.isfinite(bid) or not math.isfinite(ask)
            or bid <= 0 or ask <= 0 or ask < bid
        ):
            return None
        return bid if side == "BUY" else ask

    total = 0.0
    for leg in legs:
        if not isinstance(leg, Mapping):
            return None
        side = str(leg.get("side", "")).upper()
        entry = leg.get("price")
        if side not in {"BUY", "SELL"} or entry is None:
            return None
        try:
            entry = float(entry)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(entry) or entry <= 0:
            return None
        instrument = str(leg.get("instrument", "")).upper()
        contract = str(leg.get("contract", ""))
        bid = ask = None
        if instrument == "CASH":
            bid = row.get("cash_bid") if isinstance(row, dict) else getattr(row, "cash_bid", None)
            ask = row.get("cash_ask") if isinstance(row, dict) else getattr(row, "cash_ask", None)
            if bid is None or ask is None:
                return None
        elif instrument == "FUTURE":
            if hasattr(row, "future"):
                bid, ask = row.future.bid, row.future.ask
            else:
                bid = row.get("future_bid") if isinstance(row, dict) else getattr(row, "future_bid", None)
                ask = row.get("future_ask") if isinstance(row, dict) else getattr(row, "future_ask", None)
        elif instrument == "CALL":
            source = row.option
            bid, ask = source.call_bid, source.call_ask
        elif instrument == "PUT":
            source = row.option
            bid, ask = source.put_bid, source.put_ask
        elif instrument in {"LOW_CALL", "LOW_PUT", "HIGH_CALL", "HIGH_PUT"}:
            source = row.low if instrument.startswith("LOW_") else row.high
            kind = "call" if instrument.endswith("CALL") else "put"
            bid, ask = getattr(source, f"{kind}_bid"), getattr(source, f"{kind}_ask")
        elif contract and contract == getattr(row, "near_contract_month", None):
            bid, ask = row.near_bid, row.near_ask
        elif contract and contract == getattr(row, "far_contract_month", None):
            bid, ask = row.far_bid, row.far_ask
        else:
            return None
        exit_price = q(bid, ask, side)
        if exit_price is None:
            return None
        signed = exit_price - entry if side == "BUY" else entry - exit_price
        total += signed
    return round(total * int(trade.lot_size) * int(trade.lots), 8)

async def _live_paper_monitor_loop() -> None:
    """Continuously mark alert-driven paper trades and close them at expiry."""
    service = LivePaperTradeService()
    while True:
        try:
            db = SessionLocal()
            try:
                active = [
                    trade for trade in db.query(LivePaperTrade).filter(
                        LivePaperTrade.status == "ONGOING",
                    ).all()
                    if __import__("app.auto.live_paper", fromlist=["_valid_persisted_trade"])._valid_persisted_trade(trade)
                ]
                if active:
                    cash = live_cash_future_scanner.snapshot(max_age_seconds=5.0, limit=500)
                    cash_map = {f"{x.get('symbol')}:{x.get('contract_month')}": x for x in cash}
                    now_ns = time_module.time_ns()
                    # Freshness helper rejects both stale and future-dated source timestamps.
                    cal_rows = tuple(x for x in live_calendar_spread_scanner.snapshot(limit=500) if is_fresh_market_timestamp(getattr(x, "timestamp_ns", 0), now_ns))
                    syn_rows = tuple(x for x in live_synthetic_latest_results if is_fresh_market_timestamp(getattr(x.option, "timestamp_ns", 0), now_ns))
                    box_rows = tuple(x for x in live_box_spread_latest_results if is_fresh_market_timestamp(getattr(x.low, "timestamp_ns", 0), now_ns))
                    cal_map = {f"{x.underlying}:{x.near_contract_month}:{x.far_contract_month}:{x.direction}": x for x in cal_rows}
                    syn_map = {f"{x.option.underlying}:{x.option.expiry}:{x.option.strike:g}:{x.direction}": x for x in syn_rows}
                    box_map = {f"{x.low.underlying}:{x.low.expiry}:{x.low.strike:g}:{x.high.strike:g}:{x.direction}": x for x in box_rows}
                    for trade in active:
                        edge = None
                        if trade.strategy_id == "cash-future":
                            row = cash_map.get(trade.event_id); edge = None if row is None else row.get("gap")
                        elif trade.strategy_id == "calendar-spread":
                            row = cal_map.get(trade.event_id); edge = None if row is None else row.gap_points
                        elif trade.strategy_id == "synthetic-future-cash-carry":
                            row = syn_map.get(trade.event_id); edge = None if row is None else row.executable_edge
                        elif trade.strategy_id == "box-spread":
                            row = box_map.get(trade.event_id); edge = None if row is None else row.executable_edge
                        if edge is not None:
                            pnl = _executable_paper_pnl(trade, row)
                            if pnl is not None:
                                service.mark(db, trade, edge=float(edge), pnl_override=pnl)
                            else:
                                # Never replace a real executable P&L mark with an
                                # opportunity-edge delta merely because a live quote
                                # is temporarily unavailable. Preserve the last
                                # executable mark until a fresh executable quote arrives.
                                service.mark(
                                    db,
                                    trade,
                                    edge=float(edge),
                                    pnl_override=float(trade.unrealized_pnl),
                                )
                    db.commit()
                service.close_expired(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error("Live paper monitor failed: %s", exc)
        await asyncio.sleep(2.0)


async def _live_box_spread_loop() -> None:
    global live_box_spread_runner, live_box_spread_latest_results
    master = InstrumentMaster()
    try:
        master.download()
        today = datetime.now(IST).date()
        stock_symbols, index_symbols = set(), set()
        for item in master.instruments:
            segment = str(item.get("exch_seg", "")).upper()
            typ = str(item.get("instrumenttype", "")).upper()
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
            if typ == "FUTSTK" and name in NIFTY50_STOCK_SYMBOLS:
                stock_symbols.add(name)
            elif typ == "FUTIDX" and ((segment == "NFO" and name in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}) or (segment == "BFO" and name in BSE_BOX_INDEX_SYMBOLS)):
                index_symbols.add(name)
        stock_symbols = sorted(stock_symbols)
        targets = tuple(
            [BoxSpreadLiveTarget(x, "STOCK") for x in stock_symbols]
            + [BoxSpreadLiveTarget(x, "INDEX") for x in sorted(index_symbols)]
        )
        if not targets:
            app_logger.warning("Box Spread live runner found no current F&O targets")
            return
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
    finally:
        runner = live_box_spread_runner
        if runner is not None:
            _stop_live_runner_nonblocking(runner, name="live-box-spread-final-stop")
        live_box_spread_runner = None


def _update_live_box_spread_results(results: tuple) -> None:
    global live_box_spread_latest_results
    live_box_spread_latest_results = tuple(results)


async def _live_calendar_spread_loop() -> None:
    global live_calendar_spread_runner
    collector = LiveCalendarSpreadOneSecondCollector(
        settings.BACKTEST_DATA_DB,
        auth=AngelOneAuth(),
        instrument_master=instrument_master,
        on_observation=lambda payload: live_calendar_spread_scanner.observe(payload, session_factory=SessionLocal),
    )
    live_calendar_spread_runner = collector
    try:
        await _run_live_runner_in_daemon_thread(
            collector, name="live-calendar-spread-runner"
        )
    finally:
        _stop_live_runner_nonblocking(collector, name="live-calendar-final-stop")
        live_calendar_spread_runner = None


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
        "execution_mode": "PAPER", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "HISTORICAL", "BACKDATE", "BACKTEST", "REPLAY", "PAPER TRADE", "RESULTS"],
        "live_route": "/api/v1/scanner/cash-future/live/fast",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "calendar-spread", "name": "Calendar Spread", "version": "1", "enabled": True,
        "screen": "calendar_spread", "data_mode": "LIVE 1s",
        "execution_mode": "PAPER", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "PAPER TRADE", "RESULTS"],
        "live_route": "/api/v1/scanner/calendar-spread/live",
        "workspace_route": "/api/v1/backtesting/calendar-spread"
    },
    {
        "id": "synthetic-future-cash-carry", "name": "Synthetic Future / Cash Carry", "version": "1", "enabled": True,
        "screen": "synthetic_cash_carry", "data_mode": "LIVE",
        "execution_mode": "PAPER", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "PAPER TRADE", "RESULTS"],
        "live_route": "/api/v1/scanner/synthetic-cash-carry/live",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "box-spread", "name": "Box Spread", "version": "1", "enabled": True,
        "screen": "box_spread", "data_mode": "LIVE",
        "execution_mode": "PAPER", "live_orders": False,
        "capabilities": ["LIVE DATA", "SCANNER", "BACKTEST", "PAPER TRADE", "RESULTS"],
        "live_route": "/api/v1/scanner/box-spread/live",
        "workspace_route": "/api/v1/backtesting/cash-future/strategy-run"
    },
    {
        "id": "full-fno", "name": "Full F&O Backtest", "version": "1", "enabled": True,
        "screen": "full_fno", "data_mode": "ACCUMULATED LIVE",
        "execution_mode": "PAPER", "live_orders": False,
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


@app.get("/api/v1/market-data/runtime/health")
def market_data_runtime_health():
    """Expose process-local live-feed diagnostics for runtime troubleshooting."""
    manager = shared_common_manager()
    cash_runner = live_cash_future_runner
    calendar_runner = live_calendar_spread_runner
    synthetic_runner = live_synthetic_runner
    box_runner = live_box_spread_runner
    return {
        "status": "ok",
        "common_feed": manager.snapshot(),
        "runners": {
            "cash_future": None if cash_runner is None else cash_runner.snapshot(),
            "calendar_spread": None if calendar_runner is None else calendar_runner.snapshot(),
            "synthetic_arbitrage": None if synthetic_runner is None else synthetic_runner.snapshot(),
            "box_spread": None if box_runner is None else box_runner.snapshot(),
        },
        "scanner": live_cash_future_scanner.health(),
    }


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
        app_logger.error(f"Health check database failure: {exc}")
        return {"status": "degraded", "app": settings.app_name, "version": "0.1.0", "database": "Disconnected"}
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0", "database": "Connected"}





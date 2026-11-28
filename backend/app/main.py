from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.core.config import settings
from app.core.logger import app_logger
from app.core.daemon_worker import run_blocking_daemon
from app.core.diagnostics import runtime_diagnostics
from app.core.exceptions import TradingAppException, trading_exception_handler, global_exception_handler
from app.core.database import engine, Base, SessionLocal, check_database, check_database_bounded, DatabaseHealthProbeBusy, DatabaseHealthProbeTimeout, database_pool_snapshot
from app.core.contention_telemetry import set_database_owner, reset_database_owner
from app.core.event_loop_telemetry import EventLoopLagMonitor
from app.core.dashboard_stream_telemetry import DashboardStreamTelemetry
from app.core.schema_migrations import run_schema_migrations
from app.core.iv_basket_guards import install_iv_basket_immutability_guards
from app.models.broker_replay_eligibility_audit import BrokerReplayEligibilityAuditRecord  # noqa: F401 - register immutable operator review audit table
from app.models.broker_order_event import BrokerOrderEventRecord  # noqa: F401 - register durable broker callback event table
from app.models.execution_intent import ExecutionIntentRecord, ExecutionLegRecord, ExecutionIntentEventRecord, ExecutionFillRecord, ExecutionBrokerOrderRecord  # noqa: F401 - register durable execution and broker order identity tables
from app.models.broker_reconciliation import BrokerReconciliationRecord  # noqa: F401 - register durable reconciliation evidence
from app.models.iv_scanner_control import IVScannerControl, IVScannerControlMigration
from app.models.iv_option_observation import IVOptionObservation
from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown  # noqa: F401 - register durable IV early-entry alert tables
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox  # noqa: F401 - register alert notification outbox
from app.models.iv_constituent_basket_version import IVConstituentBasketVersion  # noqa: F401 - register isolated IV basket history table
from app.market_data.client import MarketDataClient
from app.market_data.dashboard_snapshot_cache import cached_snapshot
from app.market_data.shared_index_market_data_runner import SharedIndexMarketDataRunner
from app.market_data.shared_index_lifecycle_worker import SerializedLifecycleWorker
from app.market_data.instruments import InstrumentMaster
from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore
from app.market_data.provider_quote_parity_routes import router as provider_quote_parity_router, configure_store as configure_provider_quote_parity_store, configure_worker as configure_provider_quote_parity_worker
from app.market_data.provider_quote_parity_worker import BoundedQuoteParityWorker
from app.market_data.provider_readiness_bridge import ProviderReadinessBridge
from app.scanner.early_impulse_shoonya_observer import early_impulse_observer
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.market_data.routes import router as market_data_router, configure_shared_index_runner_snapshot, configure_shared_index_runner_health_snapshot
from app.scanner.weekly_swing_scanner import router as weekly_swing_scanner_router, weekly_swing_daily_refresh_loop, configure_page_update_callback as configure_weekly_swing_page_push, nifty200_scan_status

from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime
from app.scanner.iv_skew_flip.broker_iv import AngelOneBrokerIVCache
from app.scanner.iv_skew_flip.alerts import AlertCooldownStore
from app.scanner.iv_skew_flip.angelone_option_snapshot import capture_angelone_option_snapshot
from app.scanner.iv_skew_flip.basket_registry import summarize_basket_registry
from app.scanner.iv_skew_flip.validation import SignalEvent, load_persisted_forward_outcomes, summarize_forward_outcomes, summarize_walk_forward_by_date
from app.models.iv_signal_event import IVSignalEvent
from app.scanner.page_push_websocket import router as page_push_websocket_router, configure_snapshot_provider as configure_page_push_snapshot, publish_page_update
from app.scanner.early_impulse_routes import router as early_impulse_router
from app.scanner.vpt_x_reversal_routes import router as vpt_x_reversal_router
from app.intelligence.routes import router as intelligence_router
from app.alert_routes import router as alert_router
from app.backtesting.replay_routes import create_replay_router
from app.brokers.routes import router as brokers_router
from app.brokers.session_store import broker_sessions
from app.backtesting.download_status_routes import create_download_status_router
from app.backtesting.historical_download_status import HistoricalDownloadStatusStore
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.contract_master_sync import DailyContractMasterSync
from app.backtesting.universal_factory import create_universal_ledger
from app.backtesting.universal_result_routes import create_universal_result_router
from app.backtesting.universal_result_service import UniversalResultService

run_schema_migrations()
Base.metadata.create_all(bind=engine)
install_iv_basket_immutability_guards(engine)

# Reuse one process-local instrument-master manager for live feed/API resolution.
# Its own lazy cache prevents repeated OpenAPIScripMaster downloads.
instrument_master = InstrumentMaster()
shared_index_market_data_runner = SharedIndexMarketDataRunner(instrument_master=instrument_master)
shared_index_lifecycle_worker = SerializedLifecycleWorker()
configure_shared_index_runner_snapshot(shared_index_market_data_runner.snapshot)
configure_shared_index_runner_health_snapshot(shared_index_market_data_runner.health_snapshot)
provider_quote_parity_store: SQLiteQuoteParityStore | None = None
provider_quote_parity_worker: BoundedQuoteParityWorker | None = None
provider_readiness_bridge = ProviderReadinessBridge()
early_impulse_observer.configure_readiness(provider_readiness_bridge)
iv_skew_flip_runtime: IVSkewFlipRuntime | None = None
_iv_default_on_migration_lock = threading.Lock()
_iv_skew_flip_runtime_lock = threading.RLock()
def _shared_runner_feed_snapshot() -> dict:
    """Read broker feed diagnostics from the sole Shared Runner owner."""
    runner_snapshot = shared_index_market_data_runner.snapshot()
    lifecycle = runner_snapshot.get("lifecycle", {}) if isinstance(runner_snapshot, dict) else {}
    feed = lifecycle.get("shared_websocket", {}) if isinstance(lifecycle, dict) else {}
    return dict(feed) if isinstance(feed, dict) else {}


configure_page_push_snapshot("weekly-swing", nifty200_scan_status)
configure_weekly_swing_page_push(
    lambda payload: publish_page_update("weekly-swing", "PATCH", payload)
)
def _scanner_page_push_runtime_health(runner_name, runner):
    """Read-only feed and runner health included in scanner reconnect/live frames."""
    try:
        common_feed = _shared_runner_feed_snapshot()
        feed_health = _summarize_primary_exchange_feed(common_feed)
        common_feed["age_seconds"] = feed_health["age_seconds"]
        common_feed["status"] = feed_health["status"]
        common_feed["exchange_freshness"] = feed_health["exchange_freshness"]
        return {
            "common_feed": common_feed,
            "runners": {runner_name: None if runner is None else runner.snapshot()},
        }
    except Exception:
        return {"common_feed": {"status": "UNAVAILABLE"}, "runners": {runner_name: None}}



async def _shared_index_market_data_recovery_loop() -> None:
    """Start the data-only shared index runner and retry transient master/feed failures."""
    retry_delay = 5.0
    max_retry_delay = 60.0
    try:
        
        while True:
            # Do not redownload the Angel One master while the runner is
            # already active; only retry after a failed start.
            if shared_index_market_data_runner.snapshot().get("enabled"):
                await asyncio.sleep(15.0)
                continue
            try:
                rows = await run_blocking_daemon(
                    shared_index_market_data_runner.instrument_master.download,
                    thread_name="shared-index-instrument-master-fetch",
                )
                if not rows:
                    raise RuntimeError("Angel One instrument master returned no rows")
                snapshot = await shared_index_lifecycle_worker.run(
                    shared_index_market_data_runner.start, instrument_rows=rows
                )
                if snapshot.get("enabled"):
                    retry_delay = 10.0
                    await asyncio.sleep(15.0)
                    continue
                app_logger.warning("Shared index data runner did not start: {}", snapshot.get("last_error"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                app_logger.warning("Shared index data runner startup/recovery failed: {}", exc)
            await asyncio.sleep(retry_delay)
            retry_delay = min(max_retry_delay, max(5.0, retry_delay * 2.0))
    finally:
        try:
            # FIFO lifecycle ownership: if startup is still inside the broker SDK,
            # this stop is queued behind it and cannot be overtaken by a late ON.
            await shared_index_lifecycle_worker.close(shared_index_market_data_runner.stop)
        except Exception:
            app_logger.exception("Shared index data runner shutdown failed")


async def _iv_scanner_recovery_loop() -> None:
    """Retry a persisted IV Scanner ON state after transient startup failures.

    This loop never relaxes the
    instrument snapshot freshness/integrity checks. An explicitly saved OFF
    state is always respected.
    """
    retry_delay = 0.0
    max_retry_delay = 60.0
    while True:
        if retry_delay:
            await asyncio.sleep(retry_delay)
        try:
            # This persisted control read performs synchronous SQLAlchemy I/O.
            # Keep pool checkout/query latency off the ASGI event loop so a
            # saturated database cannot stall liveness, WebSockets, or feed tasks.
            iv_scanner_enabled = await run_blocking_daemon(
                _get_iv_scanner_enabled,
                thread_name="iv-scanner-control-state-read",
            )
            if not iv_scanner_enabled:
                retry_delay = 5.0
                continue
            with _iv_skew_flip_runtime_lock:
                runtime = iv_skew_flip_runtime
                state = runtime.snapshot() if runtime is not None else None
            if state and state.get("status") == "RUNNING":
                retry_delay = 5.0
                continue
            # IV waits for the shared runner to publish its single instrument
            # master + live option universe; it never downloads a second master.
            runner_health = shared_index_market_data_runner.health_snapshot()
            if not runner_health.get("enabled") or not runner_health.get("initial_option_bootstrap_done"):
                retry_delay = 5.0
                continue
            snapshot = _start_iv_skew_flip_runtime()
            if snapshot.get("status") == "RUNNING":
                app_logger.info("IV Scanner recovered successfully after startup retry")
            retry_delay = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.warning(
                "IV Scanner startup recovery failed; retrying in {:.1f}s: {}",
                retry_delay,
                exc,
            )
            retry_delay = min(max_retry_delay, max(5.0, retry_delay * 2.0))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dashboard_snapshot_task, _event_loop_lag_task
    global _contract_master_sync_task, _weekly_swing_refresh_task, _iv_scanner_recovery_task, iv_skew_flip_runtime, _shared_index_runner_task
    global provider_quote_parity_store, provider_quote_parity_worker
    app_logger.info(f"{settings.app_name} started successfully in {settings.environment} mode")
    if settings.PROVIDER_QUOTE_PARITY_ENABLED:
        parity_retention_days = settings.PROVIDER_QUOTE_PARITY_RETENTION_DAYS
        parity_queue_capacity = settings.PROVIDER_QUOTE_PARITY_QUEUE_CAPACITY
        if isinstance(parity_retention_days, bool) or not isinstance(parity_retention_days, int) or parity_retention_days < 1:
            raise ValueError("PROVIDER_QUOTE_PARITY_RETENTION_DAYS must be a positive integer")
        if isinstance(parity_queue_capacity, bool) or not isinstance(parity_queue_capacity, int) or parity_queue_capacity < 1:
            raise ValueError("PROVIDER_QUOTE_PARITY_QUEUE_CAPACITY must be a positive integer")
        provider_quote_parity_store = SQLiteQuoteParityStore(settings.PROVIDER_QUOTE_PARITY_DB)
        try:
            parity_cutoff_ns = time_module.time_ns() - parity_retention_days * 24 * 60 * 60 * 1_000_000_000
            provider_quote_parity_store.prune_before(cutoff_utc_ns=parity_cutoff_ns)
            provider_quote_parity_worker = BoundedQuoteParityWorker(
                provider_quote_parity_store,
                capacity=parity_queue_capacity,
            )
            provider_quote_parity_worker.start()
        except Exception:
            provider_quote_parity_store.close()
            provider_quote_parity_store = None
            provider_quote_parity_worker = None
            raise
        configure_provider_quote_parity_store(provider_quote_parity_store)
        configure_provider_quote_parity_worker(provider_quote_parity_worker)
    else:
        configure_provider_quote_parity_store(None)
        configure_provider_quote_parity_worker(None)
    # Runtime startup no longer imports or recovers retired legacy strategy jobs.
    from app.brokers.real_owner_state import reconcile_owner_state_on_startup
    # Broker adapter sessions are process-local and are not restored across
    # restarts. Never trust a persisted connected flag after process startup.
    with SessionLocal() as owner_state_db:
        reconcile_owner_state_on_startup(owner_state_db)
    # A single market-data WebSocket owner is permitted in production:
    # SharedIndexMarketDataRunner for NIFTY/BANKNIFTY only. The isolated
    # Shoonya supervisor is deliberately not started by application lifespan.
    # IV Skew Flip may run only as a read-only consumer of records already
    # published by SharedIndexMarketDataRunner. It must never create a broker
    # WebSocket manager or register its own spot, option, or futures subscriptions.
    # Shared Index Market Data is data-only and isolated: no strategy signals or orders.
    if _shared_index_runner_task is None:
        _shared_index_runner_task = asyncio.create_task(_shared_index_market_data_recovery_loop())
    if _iv_scanner_recovery_task is None:
        _iv_scanner_recovery_task = asyncio.create_task(_iv_scanner_recovery_loop())
    if _weekly_swing_refresh_task is None:
        _weekly_swing_refresh_task = asyncio.create_task(weekly_swing_daily_refresh_loop())
    # IV market data is consumed exclusively from Shared Runner records; no
    # independent broker snapshot poller or instrument-master download is started.
    if settings.BACKTESTING_ENABLED and settings.BACKTEST_CONTRACT_MASTER_AUTO_SYNC and _contract_master_sync_task is None:
        _contract_master_sync_task = asyncio.create_task(_contract_master_sync_loop())
    _dashboard_snapshot_task = asyncio.create_task(_dashboard_snapshot_publisher_loop())
    _event_loop_lag_task = asyncio.create_task(event_loop_lag_monitor.run())
    try:
        yield
    finally:
        # Close admission first: recovery must not recreate the IV runtime or
        # shared runner while application shutdown is in progress.
        if _dashboard_snapshot_task is not None:
            _dashboard_snapshot_task.cancel()
        if _event_loop_lag_task is not None:
            _event_loop_lag_task.cancel()
        for task in (_dashboard_snapshot_task, _event_loop_lag_task, _contract_master_sync_task, _weekly_swing_refresh_task, _iv_scanner_recovery_task, _shared_index_runner_task):
            if task is not None:
                task.cancel()
        shutdown_tasks = tuple(
            task
            for task in (
                _dashboard_snapshot_task,
                _event_loop_lag_task,
                _contract_master_sync_task,
                _weekly_swing_refresh_task,
                _iv_scanner_recovery_task,
                _shared_index_runner_task,
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
        # Recovery tasks and the shared-runner loop have now been cancelled
        # and awaited. Stop IV consumers before closing their process-wide source.
        if iv_skew_flip_runtime is not None:
            try:
                iv_stop_state = iv_skew_flip_runtime.stop()
                if isinstance(iv_stop_state, dict) and iv_stop_state.get("status") not in {"STOPPED", None}:
                    app_logger.warning(
                        "IV runtime did not report a clean STOPPED state during shutdown: %s",
                        iv_stop_state.get("status"),
                    )
            except Exception:
                app_logger.exception("IV runtime shutdown failed")
            finally:
                iv_skew_flip_runtime = None
        # SharedIndexMarketDataRunner.stop() runs in the recovery task's finally.
        # It only detaches its three consumers; the application lifespan is the
        # sole owner allowed to close the process-wide broker manager.
        try:
            from app.market_data.common_strategy_feed import close_shared_common_manager
            close_shared_common_manager()
        except Exception:
            app_logger.exception("Shared common WebSocket manager shutdown failed")
        # Broker adapters own authenticated provider sessions in process memory.
        # Drain them before shutdown completes; failures are logged per provider
        # and must not prevent the remaining sessions or market-data runners closing.
        for broker_name, error in broker_sessions.disconnect_all():
            app_logger.error("Broker session shutdown failed for %s: %s", broker_name, error)
        _dashboard_snapshot_task = None
        _event_loop_lag_task = None
        _contract_master_sync_task = None
        _weekly_swing_refresh_task = None
        _iv_scanner_recovery_task = None
        _shared_index_runner_task = None
        parity_worker_stopped = True
        if provider_quote_parity_worker is not None:
            parity_worker_stopped = provider_quote_parity_worker.stop(drain=True, timeout_seconds=2.0)
            if not parity_worker_stopped:
                app_logger.error("Provider quote parity worker did not stop within shutdown budget; leaving SQLite handle open")
            provider_quote_parity_worker = None
            configure_provider_quote_parity_worker(None)
        if provider_quote_parity_store is not None and parity_worker_stopped:
            provider_quote_parity_store.close()
            provider_quote_parity_store = None
            configure_provider_quote_parity_store(None)
        if backtest_download_manager is not None:
            backtest_download_manager.close()
        if backtest_status_store is not None:
            backtest_status_store.close()
        if universal_result_ledger is not None:
            universal_result_ledger.close()

event_loop_lag_monitor = EventLoopLagMonitor(interval_seconds=1.0)

app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug, lifespan=lifespan)


@app.middleware("http")
async def database_checkout_owner_middleware(request: Request, call_next):
    """Attach a bounded API-group label; never retain user-supplied path identifiers."""
    parts = [part for part in request.url.path.split("/") if part]
    safe_parts = []
    for part in parts[:3]:
        if part.isdigit() or len(part) > 32:
            safe_parts.append(":dynamic")
        else:
            safe_parts.append("".join(ch for ch in part if ch.isalnum() or ch in "-_")[:32])
    owner = f"http:{request.method.upper()}:/{'/'.join(safe_parts)}"[:96]
    token = set_database_owner(owner)
    try:
        return await call_next(request)
    finally:
        reset_database_owner(token)
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
app.include_router(provider_quote_parity_router)
app.include_router(weekly_swing_scanner_router)
app.include_router(alert_router)
if settings.BACKTESTING_ENABLED:
    app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))
app.include_router(page_push_websocket_router)
app.include_router(intelligence_router)
app.include_router(early_impulse_router)
app.include_router(vpt_x_reversal_router)
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

    universal_result_ledger = create_universal_ledger(settings.BACKTEST_RESULT_LEDGER_DB)
    universal_result_service = UniversalResultService(universal_result_ledger)
    app.include_router(create_universal_result_router(universal_result_service))

BROKER_SETTINGS_FILE = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "broker.html"


@app.get("/dashboard/broker", include_in_schema=False)
def broker_settings():
    """Serve the authenticated user's broker settings page."""
    html = BROKER_SETTINGS_FILE.read_text(encoding="utf-8")
    return HTMLResponse(content=html, media_type="text/html")


# Browser clients are owned exclusively by the ASGI event loop.
_dashboard_clients: set[WebSocket] = set()
_dashboard_stream_telemetry = DashboardStreamTelemetry()


def _build_dashboard_snapshot_payload() -> dict:
    """Publish the dashboard runtime snapshot."""
    return {
        "type": "dashboard_snapshot",
        "timestamp": datetime.now(IST).isoformat(),
        "integration": {
            "transport": "server-websocket",
            "broker_socket_per_browser": False,
        },
        "live_orders": "OFF",
    }


def _build_dashboard_snapshot_frame() -> tuple[str, float, float, int]:
    """Build and serialize one dashboard frame once for every connected browser."""
    build_started = time_module.monotonic()
    payload = _build_dashboard_snapshot_payload()
    build_seconds = time_module.monotonic() - build_started
    serialization_started = time_module.monotonic()
    # Match Starlette WebSocket.send_json defaults while avoiding one JSON dump
    # per browser connection. Serialization runs in the publisher worker thread.
    serialized = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    serialization_seconds = time_module.monotonic() - serialization_started
    return serialized, build_seconds, serialization_seconds, len(serialized.encode("utf-8"))


async def _send_dashboard_snapshot(client: WebSocket, payload: str) -> bool:
    """Bound each client's send time; a slow browser cannot stall other viewers."""
    started = time_module.monotonic()
    try:
        await asyncio.wait_for(client.send_text(payload), timeout=0.75)
        _dashboard_stream_telemetry.record_client_send(
            success=True, duration_seconds=time_module.monotonic() - started
        )
        return True
    except Exception:
        _dashboard_stream_telemetry.record_client_send(
            success=False, duration_seconds=time_module.monotonic() - started
        )
        _dashboard_clients.discard(client)
        try:
            await client.close(code=1013, reason="dashboard consumer too slow")
        except Exception:
            pass
        return False


async def _dashboard_snapshot_publisher_loop() -> None:
    """Compute once per process, fan out once, and retain bounded health metrics."""
    while True:
        started = time_module.monotonic()
        build_seconds = 0.0
        build_dispatch_wait_seconds = 0.0
        serialization_seconds = 0.0
        payload_bytes = 0
        fanout_seconds = 0.0
        client_count = len(_dashboard_clients)
        sent_count = 0
        failed_count = 0
        try:
            if client_count:
                dispatch_started = time_module.monotonic()
                payload, build_seconds, serialization_seconds, payload_bytes = (
                    await asyncio.to_thread(_build_dashboard_snapshot_frame)
                )
                # The function reports only time spent executing build/JSON work.
                # The residual await time estimates queueing/scheduling delay before
                # the default executor starts the task; this helps distinguish a
                # slow snapshot from thread-pool starvation without private APIs.
                build_dispatch_wait_seconds = max(
                    0.0,
                    time_module.monotonic() - dispatch_started
                    - build_seconds - serialization_seconds,
                )
                clients = tuple(_dashboard_clients)
                client_count = len(clients)
                if clients:
                    fanout_started = time_module.monotonic()
                    results = await asyncio.gather(
                        *(_send_dashboard_snapshot(client, payload) for client in clients),
                        return_exceptions=True,
                    )
                    fanout_seconds = time_module.monotonic() - fanout_started
                    sent_count = sum(result is True for result in results)
                    failed_count = len(results) - sent_count
            cycle_seconds = time_module.monotonic() - started
            _dashboard_stream_telemetry.record_publish(
                client_count=client_count,
                sent_count=sent_count,
                failed_count=failed_count,
                build_seconds=build_seconds,
                build_dispatch_wait_seconds=build_dispatch_wait_seconds,
                fanout_seconds=fanout_seconds,
                serialization_seconds=serialization_seconds,
                payload_bytes=payload_bytes,
                cycle_seconds=cycle_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cycle_seconds = time_module.monotonic() - started
            _dashboard_stream_telemetry.record_publish(
                client_count=client_count,
                sent_count=sent_count,
                failed_count=max(failed_count, client_count),
                build_seconds=build_seconds,
                fanout_seconds=fanout_seconds,
                serialization_seconds=serialization_seconds,
                payload_bytes=payload_bytes,
                cycle_seconds=cycle_seconds,
                error_type=type(exc).__name__,
            )
            app_logger.warning("Dashboard snapshot publish failed: %s", exc)
        elapsed = time_module.monotonic() - started
        await asyncio.sleep(max(0.05, 1.0 - elapsed))


@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """Subscribe a browser to the process-wide dashboard snapshot stream."""
    await websocket.accept()
    _dashboard_clients.add(websocket)
    try:
        # Clients are receive-only. The shared publisher owns all snapshot work
        # and sends one bounded latest snapshot to every connected browser.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        app_logger.debug("Dashboard WebSocket client disconnected: %s", exc)
    finally:
        _dashboard_clients.discard(websocket)
        try:
            await websocket.close()
        except Exception:
            pass


_iv_scanner_recovery_task: asyncio.Task | None = None
_shared_index_runner_task: asyncio.Task | None = None
_weekly_swing_refresh_task: asyncio.Task | None = None
_contract_master_sync_task: asyncio.Task | None = None
IST = ZoneInfo("Asia/Kolkata")
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


STRATEGY_WORKSPACES = []

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
@cached_snapshot(ttl_seconds=5.0, max_stale_seconds=20.0)
def market_data_runtime_health():
    """Expose only the Shared Runner's NIFTY/BANKNIFTY market-data feed."""
    runner_snapshot = shared_index_market_data_runner.snapshot()
    lifecycle = runner_snapshot.get("lifecycle", {}) if isinstance(runner_snapshot, dict) else {}
    common_feed = lifecycle.get("shared_websocket", {}) if isinstance(lifecycle, dict) else {}
    if not isinstance(common_feed, dict):
        common_feed = {}
    common_feed = dict(common_feed)
    common_feed["owner"] = "SharedIndexMarketDataRunner"
    common_feed["universe"] = ["NIFTY", "BANKNIFTY"]
    return {
        "status": "ok",
        "server_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        "common_feed": common_feed,
        "event_loop": event_loop_lag_monitor.snapshot(),
        "dashboard_stream": _dashboard_stream_telemetry.snapshot(),
        "runners": {
            "shared_index_market_data": runner_snapshot,
        },
        "live_orders": "OFF",
    }


@app.get("/api/v1/diagnostics/errors")
def runtime_diagnostic_errors(limit: int = 50):
    """Return real backend/feed/runtime errors captured since process start."""
    return {"status": "ok", "errors": runtime_diagnostics.snapshot(limit=limit), "count": len(runtime_diagnostics.snapshot(limit=1000))}


def _get_iv_scanner_enabled() -> bool:
    """Apply the default-ON rollout once, then preserve subsequent operator ON/OFF choices."""
    with _iv_default_on_migration_lock:
        with SessionLocal() as db:
            marker = db.query(IVScannerControlMigration).filter_by(
                migration_key="iv_scanner_default_on_2026_11"
            ).one_or_none()
            setting = db.get(IVScannerControl, 1)
            if marker is None:
                if setting is None:
                    setting = IVScannerControl(id=1, enabled=True)
                    db.add(setting)
                elif not setting.enabled:
                    # One-time rollout: replace the legacy saved-OFF default.
                    # Once marked, later operator OFF choices remain authoritative.
                    setting.enabled = True
                    setting.updated_at = datetime.now(timezone.utc)
                db.add(IVScannerControlMigration(
                    id=1, migration_key="iv_scanner_default_on_2026_11"
                ))
                db.commit()
            return bool(setting.enabled) if setting is not None else True


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
    runner_health = shared_index_market_data_runner.health_snapshot()
    if not runner_health.get("enabled") or not runner_health.get("initial_option_bootstrap_done"):
        raise RuntimeError("IV Scanner waits for the Shared Index Runner NIFTY/BANKNIFTY spot and option feed to become ready")
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
        # IV uses only the instrument rows already loaded by Shared Runner.
        # It cannot start an independent instrument-master preparation path.
        runner_health = shared_index_market_data_runner.health_snapshot()
        if not runner_health.get("enabled") or not runner_health.get("initial_option_bootstrap_done"):
            raise RuntimeError("SHARED_RUNNER_NOT_READY: IV requires the published shared option universe")
        # Heavyweight pressure is a separately controlled, optional signal family.
        # Keep it OFF here: IV's shared-runner attachment must not add NSE cash
        # subscriptions or expand the locked spot/options/futures token universe.
        equity_baskets = {}
        equity_pressure_error = "DISABLED_BY_SCOPE: heavyweight pressure activation remains OFF"
        # IV consumes runner-published records; it never registers a broker WebSocket consumer.
        iv_feed_manager = None
        runtime = IVSkewFlipRuntime(
            instrument_master=instrument_master,
            # Universe/early-move refresh is bounded at the supported 3s
            # cadence; one-second OI sampler and 60s signal evaluation remain
            # independent and retain their existing semantics.
            refresh_seconds=3.0,
            equity_pressure_enabled=bool(equity_baskets),
            equity_baskets=equity_baskets,
            equity_pressure_unavailable_reason=equity_pressure_error,
            feed_manager=iv_feed_manager,
            shared_runner=shared_index_market_data_runner,
            close_feed_manager=False,
            broker_iv_cache=AngelOneBrokerIVCache(client=MarketDataClient()),
        )
        runtime.set_page_update_callback(
            lambda snapshot: publish_page_update("iv-skew-flip", "PATCH", {"runtime": snapshot})
        )
        try:
            snapshot = runtime.start()
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
        publish_page_update("iv-skew-flip", "PATCH", {"runtime": snapshot})
        return snapshot


def _iv_page_push_snapshot():
    enabled = _get_iv_scanner_enabled()
    with _iv_skew_flip_runtime_lock:
        runtime = iv_skew_flip_runtime
        snapshot = runtime.snapshot() if runtime is not None else None
    return {
        "saved_enabled": enabled,
        "runtime": snapshot,
        "orders_enabled": False,
    }


configure_page_push_snapshot("iv-skew-flip", _iv_page_push_snapshot)


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



@app.get("/api/latest-alert")
def iv_early_entry_latest_alert(
    limit: int = 20,
    before_observed_at_ns: int | None = None,
    before_id: int | None = None,
    underlying: str | None = None,
):
    """Read-only NIFTY Weekly + BANKNIFTY Monthly alert history."""
    symbol = str(underlying or "").strip().upper() or None
    if symbol not in {None, "NIFTY", "BANKNIFTY"}:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    if (before_observed_at_ns is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_observed_at_ns and before_id must be provided together",
        )
    if before_observed_at_ns is not None and (before_observed_at_ns <= 0 or before_id is None or before_id <= 0):
        raise HTTPException(status_code=422, detail="alert history cursor values must be positive")
    page_size = max(1, min(int(limit), 100))
    store = AlertCooldownStore(SessionLocal, cooldown_seconds=600)
    with _iv_skew_flip_runtime_lock:
        runtime = iv_skew_flip_runtime
        live_by_underlying = runtime.snapshot().get("early_entry_alerts_by_underlying", {}) if runtime is not None else {}
        live = live_by_underlying.get(symbol or "NIFTY")
    rows = store.latest(
        limit=page_size + 1,
        before_observed_at_ns=before_observed_at_ns,
        before_id=before_id,
        underlying=symbol,
    )
    has_more = len(rows) > page_size
    alerts = rows[:page_size]
    next_cursor = None
    if has_more and alerts:
        next_cursor = {
            "before_observed_at_ns": str(alerts[-1]["observed_at_ns"]),
            "before_id": alerts[-1]["id"],
        }
    return {
        "status": "ok",
        "strategy_scope": symbol or "NIFTY_WEEKLY+BANKNIFTY_MONTHLY",
        "orders_enabled": False,
        "live": live or {"state": "WATCH", "signals_allowed": False, "reason": "IV_RUNTIME_STOPPED"},
        "live_by_underlying": live_by_underlying,
        "alerts": alerts,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }

@app.get("/api/v1/scanner/iv-skew-flip/status")
def iv_skew_flip_status():
    enabled = _get_iv_scanner_enabled()
    with _iv_skew_flip_runtime_lock:
        runtime = None if iv_skew_flip_runtime is None else iv_skew_flip_runtime.snapshot()
    shared_runner = shared_index_market_data_runner.readiness_snapshot()
    return {"status": "ok", "desired_state": "ON" if enabled else "OFF", "saved_enabled": enabled,
            "runner_status": runtime.get("status", "STOPPED") if runtime else "STOPPED",
            "shared_runner": shared_runner,
            "shared_runner_connected": bool(shared_runner.get("enabled")),
            "shared_runner_ready": bool(shared_runner.get("enabled") and shared_runner.get("initial_option_bootstrap_done")),
            "runtime": runtime, "orders_enabled": False}


def _iv_runtime_selected_expiry(symbol: str) -> str | None:
    """Return the active IV chain expiry, normalized to the DB ISO date.

    Durable history must follow the live scanner's selected universe; choosing
    the nearest expiry that happens to have saved rows can silently show a
    different weekly/monthly contract.
    """
    stream_id = "NIFTY_WEEKLY" if symbol == "NIFTY" else "BANKNIFTY_MONTHLY"
    with _iv_skew_flip_runtime_lock:
        runtime = None if iv_skew_flip_runtime is None else iv_skew_flip_runtime.snapshot()
    if not runtime or runtime.get("status") != "RUNNING":
        return None
    chain = (runtime.get("option_chains") or {}).get(stream_id) or {}
    raw_expiry = (chain.get("expiry_selection") or {}).get("selected_expiry")
    if not raw_expiry:
        return None
    value = str(raw_expiry).strip()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(value.upper(), fmt).date().isoformat()
        except ValueError:
            continue
    app_logger.warning("IV runtime selected an unparseable expiry for {}: {}", symbol, value)
    return None


@app.get("/api/v1/scanner/iv-skew-flip/timeseries")
def iv_skew_flip_option_timeseries(
    underlying: str = "NIFTY",
    expiry: str | None = None,
    window_seconds: int = 600,
    limit: int = 20000,
):
    """Return source-timestamped IV and paired spot observations for live charts."""
    symbol = str(underlying or "").strip().upper()
    if symbol not in {"NIFTY", "BANKNIFTY"}:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    window_seconds = max(60, min(int(window_seconds), 1800))
    limit = max(100, min(int(limit), 30000))
    now_ns = time_module.time_ns()
    cutoff_ns = now_ns - window_seconds * 1_000_000_000
    try:
        with SessionLocal() as db:
            selected_expiry = str(expiry or "").strip()
            if not selected_expiry:
                # Prefer the active runtime's exact weekly/monthly universe.
                # Only use the historical nearest-expiry fallback when the
                # scanner is stopped and therefore has no active selection.
                selected_expiry = _iv_runtime_selected_expiry(symbol) or ""
            if not selected_expiry:
                today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
                expiry_dates = (
                    db.query(IVOptionObservation.expiry)
                    .filter(IVOptionObservation.underlying == symbol)
                    .distinct()
                    .all()
                )
                future_expiries = []
                for (raw_expiry,) in expiry_dates:
                    try:
                        parsed_expiry = date.fromisoformat(str(raw_expiry))
                    except (TypeError, ValueError):
                        continue
                    if parsed_expiry >= today_ist:
                        future_expiries.append((parsed_expiry, str(raw_expiry)))
                selected_expiry = min(future_expiries)[1] if future_expiries else ""
            if not selected_expiry:
                return {"status": "ok", "underlying": symbol, "expiry": None,
                        "window_seconds": window_seconds, "count": 0, "observations": []}
            base_query = db.query(IVOptionObservation).filter(
                IVOptionObservation.underlying == symbol,
                IVOptionObservation.expiry == selected_expiry,
                IVOptionObservation.iv_source == "ANGEL_ONE",
                IVOptionObservation.source_timestamp_ns <= now_ns,
            )
            rows = (
                base_query.filter(IVOptionObservation.source_timestamp_ns >= cutoff_ns)
                .order_by(IVOptionObservation.source_timestamp_ns.desc())
                .limit(limit)
                .all()
            )
            # Option Greeks has no exchange event timestamp; source_timestamp_ns
            # is our response-capture time, so it must never be advertised as a
            # fresh exchange tick even when fetched moments ago.
            data_status = "LAST"
            # After the live window expires (including overnight and across
            # non-trading days), retain the newest durable observations for this
            # exact expiry. The query remains bounded by limit and never mixes
            # another expiry or manufactures samples.
            if not rows:
                rows = (
                    base_query
                    .order_by(IVOptionObservation.source_timestamp_ns.desc())
                    .limit(limit)
                    .all()
                )
                data_status = "LAST"
    except Exception as exc:
        app_logger.exception("IV option time-series read failed")
        raise HTTPException(
            status_code=503,
            detail={"message": "IV option time-series is temporarily unavailable.",
                    "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    rows.reverse()  # keep the newest bounded window in chronological order
    observations = [
        {
            "token": row.token,
            "strike": row.strike,
            "option_type": row.option_type,
            "timestamp_ns": row.source_timestamp_ns,
            "spot_timestamp_ns": row.spot_timestamp_ns,
            "spot_price": row.spot_price,
            "iv": row.iv,
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "underlying": symbol,
        "expiry": selected_expiry,
        "window_seconds": window_seconds,
        "count": len(observations),
        "observations": observations,
        "data_status": data_status if observations else "EMPTY",
        "fallback": bool(observations and data_status == "LAST"),
        "source": "iv_option_observations",
    }


def _capture_iv_broker_option_snapshot(underlying: str = "NIFTY"):
    runtime = iv_skew_flip_runtime
    if runtime is None or runtime.snapshot().get("status") != "RUNNING":
        raise RuntimeError("IV Scanner runner must be ON before broker option snapshot capture")
    symbol = str(underlying or "").strip().upper()
    stream_id = {"NIFTY": "NIFTY_WEEKLY", "BANKNIFTY": "BANKNIFTY_MONTHLY"}.get(symbol)
    if stream_id is None:
        raise ValueError("underlying must be NIFTY or BANKNIFTY")
    universe, spot = runtime.broker_snapshot_context(stream_id)
    return capture_angelone_option_snapshot(
        universe=universe,
        spot_snapshot=spot,
        client=MarketDataClient(),
        session_factory=SessionLocal,
    )


def _iv_snapshot_saved_today(day, underlying: str = "NIFTY", expected_contracts: int = 22):
    # An earlier manual fetch must not suppress the scheduled near-close snapshot.
    start = datetime.combine(day, time(15, 27), tzinfo=IST).timestamp() * 1_000_000_000
    end = datetime.combine(day, time.max, tzinfo=IST).timestamp() * 1_000_000_000
    symbol = str(underlying or "").strip().upper()
    with SessionLocal() as db:
        received_iv_contracts = db.query(
            func.count(func.distinct(IVOptionObservation.token))
        ).filter(
            IVOptionObservation.underlying == symbol,
            IVOptionObservation.iv_source == "ANGEL_ONE",
            IVOptionObservation.iv.isnot(None),
            IVOptionObservation.sampled_at_ns >= int(start),
            IVOptionObservation.sampled_at_ns <= int(end),
        ).scalar() or 0
        return received_iv_contracts >= int(expected_contracts)


async def _iv_option_snapshot_daily_loop():
    """Persist bounded broker-IV snapshots independently for each active index chain."""
    last_attempt = {"NIFTY": 0.0, "BANKNIFTY": 0.0}
    next_delay = {"NIFTY": 0.0, "BANKNIFTY": 0.0}
    expected_by_symbol = {"NIFTY": 22, "BANKNIFTY": 22}
    while True:
        now = datetime.now(IST)
        current_time = now.time()
        runtime = iv_skew_flip_runtime
        runner_on = runtime is not None and runtime.snapshot().get("status") == "RUNNING"
        capture_window = time(9, 20) <= current_time <= time(15, 30, 20)
        if runner_on and capture_window:
            near_close = time(15, 28) <= current_time <= time(15, 30, 20)
            for symbol in ("NIFTY", "BANKNIFTY"):
                already_saved = False
                if near_close:
                    try:
                        already_saved = await asyncio.to_thread(
                            _iv_snapshot_saved_today, now.date(), symbol, expected_by_symbol[symbol]
                        )
                    except Exception:
                        app_logger.exception("IV %s EOD snapshot completion check failed", symbol)
                if already_saved:
                    continue
                stream_id = {"NIFTY": "NIFTY_WEEKLY", "BANKNIFTY": "BANKNIFTY_MONTHLY"}[symbol]
                # RUNNING is published before each chain's first ATM/expiry
                # refresh attaches its option feed. Skip quietly during bootstrap
                # instead of emitting a false failure and consuming retry time.
                if not runtime.broker_snapshot_ready(stream_id):
                    continue
                monotonic_now = time_module.monotonic()
                retry_delay = 15.0 if near_close else (next_delay[symbol] or 300.0)
                if monotonic_now - last_attempt[symbol] < retry_delay:
                    continue
                last_attempt[symbol] = monotonic_now
                try:
                    result = await asyncio.to_thread(_capture_iv_broker_option_snapshot, symbol)
                    received = int(result.get("iv_received_contracts") or 0)
                    expected = int(result.get("expected_contracts") or expected_by_symbol[symbol])
                    if received >= expected:
                        next_delay[symbol] = 300.0
                        app_logger.info(
                            "IV Angel One %s option snapshot persisted: expiry=%s contracts=%s IV=%s",
                            symbol, result.get("expiry"), result.get("saved_contracts"), received,
                        )
                    else:
                        next_delay[symbol] = 15.0 if near_close else (60.0 if received > 0 else 300.0)
                        app_logger.warning(
                            "IV Angel One %s snapshot incomplete; preserving last valid IV and retrying: expiry=%s IV=%s/%s",
                            symbol, result.get("expiry"), received, expected,
                        )
                except Exception:
                    next_delay[symbol] = 15.0 if near_close else 300.0
                    app_logger.exception("IV Angel One %s snapshot failed; next retry in %.0fs", symbol, next_delay[symbol])
        else:
            # OFF pauses all broker calls; re-enable starts each stream promptly.
            for symbol in last_attempt:
                last_attempt[symbol] = 0.0
                next_delay[symbol] = 0.0
        await asyncio.sleep(15)


@app.post("/api/v1/scanner/iv-skew-flip/snapshot")
def capture_iv_skew_flip_broker_snapshot(underlying: str = "NIFTY"):
    """Manually fetch Angel One Greeks + FULL quote for the selected active IV chain."""
    try:
        return _capture_iv_broker_option_snapshot(underlying)
    except Exception as exc:
        app_logger.exception("Manual IV broker option snapshot failed")
        raise HTTPException(
            status_code=503,
            detail={"message": "Angel One option-chain snapshot could not be captured.", "reason": str(exc)},
        ) from exc


@app.get("/api/v1/scanner/iv-skew-flip/history")
def iv_skew_flip_option_history(underlying: str = "NIFTY", expiry: str | None = None, limit: int = 1000):
    """Return the newest durable option observation per contract for display as LAST."""
    symbol = str(underlying or "").strip().upper()
    if symbol not in {"NIFTY", "BANKNIFTY"}:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    limit = max(1, min(int(limit), 2000))
    try:
        with SessionLocal() as db:
            selected_expiry = str(expiry or "").strip()
            if not selected_expiry:
                # Prefer the active runtime's exact weekly/monthly universe.
                # Only use the historical nearest-expiry fallback when the
                # scanner is stopped and therefore has no active selection.
                selected_expiry = _iv_runtime_selected_expiry(symbol) or ""
            if not selected_expiry:
                today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
                expiry_dates = (
                    db.query(IVOptionObservation.expiry)
                    .filter(IVOptionObservation.underlying == symbol)
                    .distinct()
                    .all()
                )
                future_expiries = []
                for (raw_expiry,) in expiry_dates:
                    try:
                        parsed_expiry = date.fromisoformat(str(raw_expiry))
                    except (TypeError, ValueError):
                        continue
                    if parsed_expiry >= today_ist:
                        future_expiries.append((parsed_expiry, str(raw_expiry)))
                selected_expiry = min(future_expiries)[1] if future_expiries else ""
            if not selected_expiry:
                return {"status": "ok", "underlying": symbol, "expiry": None, "count": 0, "metrics": []}
            latest_per_token = (
                db.query(
                    IVOptionObservation.token.label("token"),
                    func.max(IVOptionObservation.source_timestamp_ns).label("latest_tick"),
                )
                .filter(
                    IVOptionObservation.underlying == symbol,
                    IVOptionObservation.expiry == selected_expiry,
                    IVOptionObservation.iv_source == "ANGEL_ONE",
                )
                .group_by(IVOptionObservation.token)
                .subquery()
            )
            rows = (
                db.query(IVOptionObservation)
                .join(
                    latest_per_token,
                    (IVOptionObservation.token == latest_per_token.c.token)
                    & (IVOptionObservation.source_timestamp_ns == latest_per_token.c.latest_tick),
                )
                .filter(
                    IVOptionObservation.underlying == symbol,
                    IVOptionObservation.expiry == selected_expiry,
                    IVOptionObservation.iv_source == "ANGEL_ONE",
                )
                .order_by(IVOptionObservation.strike.asc(), IVOptionObservation.option_type.asc())
                .limit(limit)
                .all()
            )
            # A later quote-only/partial Greeks response must not erase the
            # last valid broker IV shown for that contract.
            latest_iv_per_token = (
                db.query(
                    IVOptionObservation.token.label("token"),
                    func.max(IVOptionObservation.source_timestamp_ns).label("latest_iv_tick"),
                )
                .filter(
                    IVOptionObservation.underlying == symbol,
                    IVOptionObservation.expiry == selected_expiry,
                    IVOptionObservation.iv_source == "ANGEL_ONE",
                    IVOptionObservation.iv.isnot(None),
                )
                .group_by(IVOptionObservation.token)
                .subquery()
            )
            latest_iv_rows = (
                db.query(IVOptionObservation)
                .join(
                    latest_iv_per_token,
                    (IVOptionObservation.token == latest_iv_per_token.c.token)
                    & (IVOptionObservation.source_timestamp_ns == latest_iv_per_token.c.latest_iv_tick),
                )
                .filter(
                    IVOptionObservation.underlying == symbol,
                    IVOptionObservation.expiry == selected_expiry,
                    IVOptionObservation.iv_source == "ANGEL_ONE",
                )
                .all()
            )
            last_valid_iv_by_token = {row.token: row for row in latest_iv_rows}
            # Broker IV delta is measured between actual Angel One snapshots,
            # never copied from the local-model 1-second sampler.
            ranked_iv = (
                db.query(
                    IVOptionObservation.id.label("id"),
                    func.row_number().over(
                        partition_by=IVOptionObservation.token,
                        order_by=IVOptionObservation.source_timestamp_ns.desc(),
                    ).label("row_number"),
                )
                .filter(
                    IVOptionObservation.underlying == symbol,
                    IVOptionObservation.expiry == selected_expiry,
                    IVOptionObservation.iv_source == "ANGEL_ONE",
                    IVOptionObservation.iv.isnot(None),
                )
                .subquery()
            )
            prior_iv_rows = (
                db.query(IVOptionObservation)
                .join(ranked_iv, IVOptionObservation.id == ranked_iv.c.id)
                .filter(ranked_iv.c.row_number == 2)
                .all()
            )
            prior_iv_by_token = {row.token: row for row in prior_iv_rows}
    except Exception as exc:
        app_logger.exception("IV option history read failed")
        raise HTTPException(
            status_code=503,
            detail={"message": "Durable IV option history is temporarily unavailable.", "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    metrics = []
    for row in rows:
        iv_row = last_valid_iv_by_token.get(row.token)
        prior_iv_row = prior_iv_by_token.get(row.token)
        metrics.append({
            "token": row.token,
            "underlying": row.underlying,
            "expiry": row.expiry,
            "strike": row.strike,
            "option_type": row.option_type,
            "oi": row.oi,
            "volume": row.volume,
            "oi_live": False,
            "oi_timestamp_ns": row.source_timestamp_ns if row.oi is not None else None,
            "oi_stale": row.oi is not None,
            "iv": iv_row.iv if iv_row is not None else None,
            "iv_source": "ANGEL_ONE" if iv_row is not None else None,
            "iv_unit": "PERCENT",
            "iv_change_since_prev": (
                round(iv_row.iv - prior_iv_row.iv, 4)
                if iv_row is not None and prior_iv_row is not None
                and iv_row.iv is not None and prior_iv_row.iv is not None else None
            ),
            "iv_interval_seconds": (
                round((iv_row.source_timestamp_ns - prior_iv_row.source_timestamp_ns) / 1_000_000_000, 2)
                if iv_row is not None and prior_iv_row is not None else None
            ),
            "iv_live": False,
            "iv_status": "LAST" if iv_row is not None else "UNAVAILABLE",
            "iv_source_timestamp_ns": iv_row.source_timestamp_ns if iv_row is not None else None,
            "iv_input": (
                "BID_ASK_MIDPOINT" if iv_row is not None and iv_row.bid is not None and iv_row.ask is not None
                else ("LTP" if iv_row is not None and iv_row.ltp is not None else None)
            ),
            "mark": row.mark,
            "ltp": row.ltp,
            "source_timestamp_ns": row.source_timestamp_ns,
            "spot_timestamp_ns": row.spot_timestamp_ns,
            "spot_price": row.spot_price,
            "sampled_at_ns": row.sampled_at_ns,
            "volume_change_1s": None,
            "volume_change_pct_1s": None,
            "oi_change_1s": None,
            "oi_change_pct_1s": None,
            "iv_change_1s": None,
            "ltp_change_1s": None,
            "ltp_change_pct_1s": None,
        })
    return {
        "status": "ok",
        "underlying": symbol,
        "expiry": selected_expiry,
        "expiry_type": "WEEKLY" if symbol == "NIFTY" else "MONTHLY",
        "count": len(metrics),
        "metrics": metrics,
        "data_status": "LAST" if metrics else "EMPTY",
        "snapshot_at_ns": max((row.sampled_at_ns for row in rows), default=None),
        "source": "ANGEL_ONE",

    }


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
                raise HTTPException(status_code=503, detail={"message": "IV Scanner was not started because ON state could not be saved.", "reason": type(exc).__name__}) from exc
            try:
                snapshot = _start_iv_skew_flip_runtime()
            except Exception as exc:
                # Compensate the desired state if backend startup fails.
                try:
                    _save_iv_scanner_enabled(False)
                except Exception:
                    app_logger.exception("IV Scanner failed to roll back persisted ON state after startup failure")
                app_logger.exception("IV Scanner could not be enabled")
                raise HTTPException(status_code=503, detail={"message": "IV Scanner ON save failed because backend runner did not start.", "reason": type(exc).__name__}) from exc
        else:
            # Persist OFF first. If the database is unavailable, leave the
            # currently running scanner untouched rather than create a
            # STOPPED runtime with a persisted ON state that restarts later.
            try:
                _save_iv_scanner_enabled(False)
            except Exception as exc:
                app_logger.exception("IV Scanner OFF state could not be persisted")
                raise HTTPException(status_code=503, detail={"message": "IV Scanner was not stopped because OFF state could not be saved.", "reason": type(exc).__name__}) from exc
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


def _database_health_failure_class(exc: Exception) -> str:
    """Classify readiness failures without exposing database driver details."""
    if isinstance(exc, DatabaseHealthProbeTimeout) or isinstance(exc, asyncio.TimeoutError):
        return "DATABASE_PROBE_TIMEOUT"
    if isinstance(exc, DatabaseHealthProbeBusy):
        return "DATABASE_PROBE_BUSY"
    dialect_name = getattr(getattr(engine, "dialect", None), "name", None)
    message = str(exc).lower()
    if dialect_name == "sqlite" and any(
        marker in message
        for marker in ("database is locked", "database table is locked", "database is busy")
    ):
        return "SQLITE_CONTENTION"
    return "DATABASE_CHECK_FAILURE"


@app.get("/health/live")
async def health_liveness_check():
    """Fast process liveness probe; deliberately performs no DB or broker I/O."""
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


@app.get("/health")
async def health_check():
    # Fail immediately when a finite pool is already saturated. A healthy
    # response still requires a real SELECT 1 below.
    pool_snapshot = database_pool_snapshot()
    if pool_snapshot.get("saturated") is True:
        runtime_diagnostics.record(
            component="Database",
            error_type="PoolSaturated",
            message="Database connection pool is at its configured checkout capacity",
            endpoint="/health",
            context={"event": "health_check", "database_pool": pool_snapshot},
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "app": settings.app_name,
                "version": "0.1.0",
                "database": "Pool saturated",
                "database_pool": pool_snapshot,
            },
        )


    try:
        # Use the daemon-thread, single-flight helper: a timed-out DB checkout
        # must not hold the ASGI default executor open during request shutdown.
        check_database_bounded(probe=check_database, timeout_seconds=0.75)
    except Exception as exc:
        failure_class = (
            "DATABASE_PROBE_TIMEOUT"
            if isinstance(exc, asyncio.TimeoutError)
            else _database_health_failure_class(exc)
        )
        exception_type = (
            "DatabaseProbeTimeout"
            if failure_class == "DATABASE_PROBE_TIMEOUT"
            else type(exc).__name__
        )
        failure_pool_snapshot = database_pool_snapshot()
        runtime_diagnostics.record(
            component="Database",
            error_type=exception_type if failure_class == "DATABASE_PROBE_TIMEOUT" else failure_class,
            message=f"Database readiness probe failed ({exception_type})",
            endpoint="/health",
            context={
                "event": "health_check",
                "database_failure_class": failure_class,
                "database_exception_type": exception_type,
                "database_pool": failure_pool_snapshot,
            },
        )
        app_logger.error(
            "Health check database failure ({}; exception={})",
            failure_class,
            exception_type,
        )
        database_status = (
            "Contended"
            if failure_class == "SQLITE_CONTENTION"
            else "Probe busy"
            if failure_class == "DATABASE_PROBE_BUSY"
            else "Probe timed out"
            if failure_class == "DATABASE_PROBE_TIMEOUT"
            else "Disconnected"
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "app": settings.app_name,
                "version": "0.1.0",
                "database": database_status,
                "database_error_class": failure_class,
                "database_pool": failure_pool_snapshot,
            },
        )

    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
        "database": "Connected",
        "database_pool": database_pool_snapshot(),
    }

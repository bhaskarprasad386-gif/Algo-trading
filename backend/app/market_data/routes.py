from datetime import date, datetime, time, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger
from .shoonya_owner_auth import ShoonyaOwnerLogin, login_shoonya_owner, require_shoonya_owner_access
from .registry import InstrumentDescriptor
from .contracts import InstrumentKey
from .dashboard_snapshot_cache import cached_snapshot
from .persisted_summary_cache import BoundedPersistedSummaryCache

router = APIRouter(
    prefix="/api/v1/market-data",
    tags=["Market Data"],
)

_CLOSED_OVERVIEW_CACHE: dict[str, dict] = {}
_CLOSED_OVERVIEW_CACHE_LOCK = Lock()
_OVERVIEW_HISTORICAL_CACHE: dict[tuple[str, str, str], tuple[float, float | None, float | None]] = {}
_OVERVIEW_HISTORICAL_CACHE_LOCK = Lock()
_OVERVIEW_QUOTE_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, dict[str, dict]]] = {}
_OVERVIEW_QUOTE_CACHE_LOCK = Lock()
_OVERVIEW_QUOTE_CACHE_TTL_SECONDS = 15.0
_OVERVIEW_QUOTE_CACHE_MAX_STALE_SECONDS = 60.0
_OVERVIEW_QUOTE_FAILURE_BACKOFF_SECONDS = 10.0
_OVERVIEW_QUOTE_FAILURES: dict[tuple[str, tuple[str, ...]], tuple[float, str]] = {}
_OVERVIEW_INSTRUMENT_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}
_OVERVIEW_INSTRUMENT_CACHE_LOCK = Lock()
_OVERVIEW_INSTRUMENT_CACHE_TTL_SECONDS = 900.0
_live_cash_future_runner_snapshot_provider = None
_shoonya_snapshot_provider = None
_shoonya_supervisor_provider = None
_shared_index_runner_snapshot_provider = None
_shared_index_runner_health_provider = None

# Historical shard counts are coverage metadata, not live-feed freshness.
_LIVE_PERSISTED_SUMMARY_CACHE = BoundedPersistedSummaryCache(
    ttl_seconds=60.0, max_stale_seconds=300.0, max_entries=4
)


def configure_live_cash_future_runner_snapshot(provider) -> None:
    """Configure the process-local Cash-Future common runner snapshot provider."""
    global _live_cash_future_runner_snapshot_provider
    _live_cash_future_runner_snapshot_provider = provider


def configure_shoonya_snapshot(provider) -> None:
    """Configure the isolated Shoonya shadow-feed diagnostics provider."""
    global _shoonya_snapshot_provider
    _shoonya_snapshot_provider = provider


def configure_shoonya_supervisor(provider) -> None:
    """Configure the process-local Shoonya supervisor control reference."""
    global _shoonya_supervisor_provider
    _shoonya_supervisor_provider = provider


def configure_shared_index_runner_snapshot(provider) -> None:
    """Configure read-only health snapshot for isolated shared index runner."""
    global _shared_index_runner_snapshot_provider
    _shared_index_runner_snapshot_provider = provider

def configure_shared_index_runner_health_snapshot(provider) -> None:
    """Configure lightweight health-only snapshot for mobile/dashboard polling."""
    global _shared_index_runner_health_provider
    _shared_index_runner_health_provider = provider


class ShoonyaInstrumentInput(BaseModel):
    exchange: str = Field(min_length=1, max_length=16)
    segment: str = Field(min_length=1, max_length=16)
    token: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=128)
    instrument_type: str = Field(min_length=1, max_length=16)
    expiry: str | None = Field(default=None, max_length=32)
    strike: float | None = Field(default=None, gt=0)
    option_type: str | None = Field(default=None, pattern="^(CE|PE)$")
    lot_size: int | None = Field(default=None, gt=0)
    tick_size: float | None = Field(default=None, gt=0)
    underlying: str | None = Field(default=None, max_length=64)

    def to_descriptor(self) -> InstrumentDescriptor:
        exchange = self.exchange.strip().upper()
        segment = self.segment.strip().upper()
        instrument_type = self.instrument_type.strip().lower()
        if exchange not in {"NSE", "NFO", "BSE", "BFO", "MCX", "NCDEX"}:
            raise ValueError("unsupported Shoonya exchange")
        if instrument_type not in {"equity", "index", "future", "option", "commodity"}:
            raise ValueError("unsupported instrument_type")
        if instrument_type == "option":
            if self.strike is None or self.option_type is None or not self.expiry:
                raise ValueError("option subscriptions require expiry, strike and option_type")
        elif self.option_type is not None or self.strike is not None:
            raise ValueError("strike and option_type are only valid for options")
        return InstrumentDescriptor(
            key=InstrumentKey(exchange, segment, self.token.strip()),
            symbol=self.symbol.strip(),
            instrument_type=instrument_type,
            exchange=exchange,
            segment=segment,
            expiry=self.expiry,
            strike=self.strike,
            option_type=self.option_type,
            lot_size=self.lot_size,
            tick_size=self.tick_size,
            underlying=self.underlying.strip().upper() if self.underlying else None,
        )


class ShoonyaSubscriptionRequest(BaseModel):
    instruments: list[ShoonyaInstrumentInput] = Field(min_length=1, max_length=500)


def _shoonyadescriptors(payload: ShoonyaSubscriptionRequest) -> list[InstrumentDescriptor]:
    """Convert a batch and reject duplicate broker-token identities before mutation."""
    descriptors = [item.to_descriptor() for item in payload.instruments]
    seen: dict[tuple[str, str], InstrumentDescriptor] = {}
    for descriptor in descriptors:
        adapter_key = (descriptor.key.exchange.strip().upper(), descriptor.key.token.strip())
        previous = seen.get(adapter_key)
        if previous is not None:
            if previous == descriptor:
                raise ValueError(f"duplicate Shoonya instrument in request: {adapter_key}")
            raise ValueError(f"conflicting Shoonya token descriptors in request: {adapter_key}")
        seen[adapter_key] = descriptor
    return descriptors


@router.post("/shoonya/owner-auth/login")
def shoonya_owner_login(payload: ShoonyaOwnerLogin, request: Request):
    """Issue a short-lived token scoped only to Shoonya shadow-feed controls."""
    return login_shoonya_owner(payload, request)


@router.get("/shoonya-health")
def get_shoonya_health():
    """Expose sanitized Shoonya shadow-feed state without credentials or control actions."""
    from app.core.config import settings

    if not settings.SHOONYA_MARKET_DATA_ENABLED:
        return {
            "broker": "shoonya",
            "enabled": False,
            "mode": "shadow_only",
            "state": "DISABLED",
            "order_routing_enabled": False,
            "strategy_fanout_enabled": False,
        }
    provider = _shoonya_snapshot_provider
    if provider is None:
        return {
            "broker": "shoonya",
            "enabled": True,
            "mode": "shadow_only",
            "state": "NOT_INITIALIZED",
            "order_routing_enabled": False,
            "strategy_fanout_enabled": False,
        }
    return provider()


@router.post("/shoonya/subscriptions")
def add_shoonya_subscriptions(
    payload: ShoonyaSubscriptionRequest,
    _owner_id: int = Depends(require_shoonya_owner_access),
):
    """Subscribe explicit instruments on the isolated Shoonya shadow feed."""
    from app.core.config import settings

    if not settings.SHOONYA_MARKET_DATA_ENABLED:
        raise HTTPException(status_code=409, detail="Shoonya market data is disabled by server configuration")
    supervisor = _shoonya_supervisor_provider() if callable(_shoonya_supervisor_provider) else _shoonya_supervisor_provider
    if supervisor is None:
        raise HTTPException(status_code=503, detail="Shoonya supervisor is not initialized")
    try:
        descriptors = _shoonyadescriptors(payload)
        count = supervisor.subscribe(descriptors)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Shoonya supervisor is not available") from exc
    return {"broker": "shoonya", "mode": "shadow_only", "subscribed": count,
            "order_routing_enabled": False, "strategy_fanout_enabled": False}


@router.delete("/shoonya/subscriptions")
def remove_shoonya_subscriptions(
    payload: ShoonyaSubscriptionRequest,
    _owner_id: int = Depends(require_shoonya_owner_access),
):
    """Unsubscribe explicit instruments from the isolated Shoonya shadow feed."""
    supervisor = _shoonya_supervisor_provider() if callable(_shoonya_supervisor_provider) else _shoonya_supervisor_provider
    if supervisor is None:
        raise HTTPException(status_code=503, detail="Shoonya supervisor is not initialized")
    try:
        descriptors = _shoonyadescriptors(payload)
        count = supervisor.unsubscribe(descriptors)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"broker": "shoonya", "mode": "shadow_only", "unsubscribed": count,
            "order_routing_enabled": False, "strategy_fanout_enabled": False}



def _nse_market_is_open(now: datetime) -> bool:
    """Return NSE session state using the versioned holiday calendar."""
    from app.backtesting.nse_2026_holidays import NSE_EQUITY_TRADING_HOLIDAYS_2026
    current = now.astimezone(ZoneInfo("Asia/Kolkata")) if now.tzinfo else now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    if current.date() in NSE_EQUITY_TRADING_HOLIDAYS_2026 or current.weekday() >= 5:
        return False
    return time(9, 15) <= current.time() < time(15, 30)



def _mcx_market_is_open(now: datetime) -> bool:
    """Return scheduled MCX non-agri hours in India time; quote health is separate."""
    current = now.astimezone(ZoneInfo("Asia/Kolkata")) if now.tzinfo else now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    if current.weekday() >= 5:
        return False
    return time(9, 0) <= current.time() < time(23, 30)

@router.get("/ltp")
def get_ltp(
    exchange: str = Query(...),
    tradingsymbol: str = Query(...),
    symboltoken: str = Query(...),
):
    """Get latest traded price for an instrument."""
    from app.market_data.client import MarketDataClient

    try:
        app_logger.info(f"LTP request: {exchange} {tradingsymbol} {symboltoken}")
        return MarketDataClient().ltp(
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
        )
    except TradingAppException:
        raise
    except Exception as e:
        app_logger.error(f"LTP error: {e}")
        raise HTTPException(status_code=502, detail="Market data provider request failed") from e


@router.get("/ltp-by-symbol")
def get_ltp_by_symbol(
    tradingsymbol: str = Query(..., min_length=1),
    exchange: str = Query("NSE"),
):
    """Resolve an NSE/BSE symbol through the Angel One master and fetch its LTP."""
    from app.market_data.client import MarketDataClient
    from app.market_data.instruments import InstrumentMaster

    symbol = tradingsymbol.strip().upper()
    segment = exchange.strip().upper()
    try:
        instrument = InstrumentMaster().get_instrument(symbol, segment)
        if not instrument:
            raise HTTPException(
                status_code=404,
                detail=f"Instrument not found: {segment} {symbol}",
            )

        token = str(instrument.get("token", ""))
        if not token:
            raise HTTPException(status_code=502, detail="Instrument token is missing")

        response = MarketDataClient().ltp(
            exchange=segment,
            tradingsymbol=symbol,
            symboltoken=token,
        )
        data = response.get("data") or {}
        return {
            "status": True,
            "exchange": segment,
            "tradingsymbol": symbol,
            "symboltoken": token,
            "ltp": data.get("ltp"),
            "raw": response,
        }
    except HTTPException:
        raise
    except TradingAppException:
        raise
    except Exception as e:
        app_logger.error(f"Symbol LTP error for {segment} {symbol}: {e}")
        raise HTTPException(status_code=502, detail="Market data provider request failed") from e


@router.get("/historical")
def get_historical(
    exchange: str = Query(...),
    symboltoken: str = Query(...),
    interval: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
):
    """Get historical candle data."""
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient

    try:
        app_logger.info(f"Historical request: {exchange} {symboltoken} {interval}")
        market_client = MarketDataClient()
        historical_client = HistoricalDataClient(market_client)
        return historical_client.get_candles(
            exchange=exchange,
            symboltoken=symboltoken,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
    except TradingAppException:
        raise
    except Exception as e:
        app_logger.error(f"Historical error: {e}")
        raise HTTPException(status_code=502, detail="Historical market data provider request failed") from e


@router.get("/live-health")
@cached_snapshot(ttl_seconds=5.0, max_stale_seconds=20.0)
def get_live_data_health():
    """Return live tick freshness plus separately aged persisted coverage metadata."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog
    from app.core.config import settings
    from app.market_data.common_strategy_feed import shared_common_manager_snapshot
    import sqlite3

    def load_persisted_summaries():
        # This read-only catalog enumeration is intentionally decoupled from
        # the 5-second runtime feed-health cadence. The result is valid for
        # 60 seconds; feed tick freshness below is still recalculated per
        # endpoint snapshot and is never inferred from persisted history.
        return {
            "angelone-live-1s": DailyMarketDataShardCatalog.read_source_summary(
                settings.BACKTEST_DATA_DB,
                source="angelone-live-1s", timeframe="1s",
            ),
            "angelone-live": DailyMarketDataShardCatalog.read_source_summary(
                settings.BACKTEST_DATA_DB,
                source="angelone-live", timeframe="event",
            ),
        }

    try:
        persisted_summaries, persisted_summary_meta = _LIVE_PERSISTED_SUMMARY_CACHE.get(
            str(settings.BACKTEST_DATA_DB), load_persisted_summaries
        )
    except (TimeoutError, RuntimeError, OSError, sqlite3.Error) as exc:
        # Historical coverage is optional diagnostics. A cold/locked/corrupt
        # shard must not make the live tick-health endpoint unavailable.
        persisted_summaries = {
            "angelone-live-1s": {
                "record_count": None, "instruments": None,
                "latest_timestamp_ns": None,
            },
            "angelone-live": {
                "record_count": None, "instruments": None,
                "latest_timestamp_ns": None,
            },
        }
        persisted_summary_meta = {
            "status": "UNAVAILABLE", "age_seconds": None,
            "ttl_seconds": _LIVE_PERSISTED_SUMMARY_CACHE.ttl_seconds,
            "max_stale_seconds": _LIVE_PERSISTED_SUMMARY_CACHE.max_stale_seconds,
            "error_type": type(exc).__name__,
        }

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    # Use the same holiday-aware NSE session calculation as the market overview.
    market_open = _nse_market_is_open(now)
    sources = []
    for source, timeframe in (("angelone-live-1s", "1s"), ("angelone-live", "event")):
        summary = persisted_summaries[source]
        latest_ns = summary["latest_timestamp_ns"]
        raw_age_seconds = None if latest_ns is None else (
            now.timestamp() * 1_000_000_000 - latest_ns
        ) / 1_000_000_000
        # A timestamp materially in the future is invalid, not a zero-age live quote.
        age_seconds = (
            None if raw_age_seconds is None or raw_age_seconds < -5.0
            else max(0.0, raw_age_seconds)
        )
        status = (
            "UNAVAILABLE" if persisted_summary_meta["status"] == "UNAVAILABLE"
            else "LIVE" if market_open and age_seconds is not None and age_seconds <= 5
            else "STALE" if market_open and age_seconds is not None
            else "NO_DATA"
        )
        sources.append({
            "source": source, "timeframe": timeframe,
            "records": summary["record_count"],
            "instruments": summary["instruments"],
            "latest_timestamp_ns": latest_ns,
            "age_seconds": age_seconds, "status": status,
        })
    one_second = next(x for x in sources if x["source"] == "angelone-live-1s")
    common = shared_common_manager_snapshot()
    # NSE cash (Angel exchange type 1) and NFO derivatives (type 2) are
    # the critical feeds for the cash-future and synthetic scanners. Do not
    # let a fresh BFO/MCX tick mask stale or absent NSE/NFO observations.
    now_ns = int(now.timestamp() * 1_000_000_000)
    timestamps_by_exchange = (
        common.get("latest_tick_timestamp_ns_by_exchange_type", {}) if common else {}
    )
    exchange_freshness: dict[str, dict[str, object]] = {}
    for exchange_type, exchange_name in ((1, "NSE"), (2, "NFO")):
        raw_timestamp = timestamps_by_exchange.get(str(exchange_type))
        if raw_timestamp is None:
            raw_timestamp = timestamps_by_exchange.get(exchange_type)
        try:
            timestamp_ns = int(raw_timestamp) if raw_timestamp is not None else None
        except (TypeError, ValueError, OverflowError):
            timestamp_ns = None
        raw_age = (
            (now_ns - timestamp_ns) / 1_000_000_000
            if timestamp_ns is not None else None
        )
        age = None if raw_age is None or raw_age < -5.0 else max(0.0, raw_age)
        exchange_freshness[exchange_name] = {
            "exchange_type": exchange_type,
            "latest_tick_timestamp_ns": timestamp_ns,
            "age_seconds": age,
            "status": (
                "LIVE" if market_open and age is not None and age <= 5.0
                else "STALE" if market_open and (
                    age is not None or (common or {}).get("ticks_received", 0) > 0
                )
                else "NO_DATA" if market_open
                else "CLOSED"
            ),
        }
    known_primary_ages = [
        item["age_seconds"] for item in exchange_freshness.values()
        if item["age_seconds"] is not None
    ]
    runtime_age = max(known_primary_ages) if known_primary_ages else None
    runtime_live = market_open and all(
        item["status"] == "LIVE" for item in exchange_freshness.values()
    )
    primary_any_data = any(
        item["latest_tick_timestamp_ns"] is not None
        for item in exchange_freshness.values()
    )
    # Runtime tick freshness and persisted-source freshness are different signals.
    # Never report persisted rows or an unrelated exchange's ticks as NSE/NFO health.
    runtime_status = (
        "CLOSED" if not market_open else
        "LIVE" if runtime_live else
        "STALE" if primary_any_data or (common or {}).get("ticks_received", 0) > 0 else
        "NO_DATA"
    )
    return {
        "status": "success", "market_session": "OPEN" if market_open else "CLOSED",
        "checked_at": now.isoformat(),
        "persisted": (
            None if one_second["records"] is None else one_second["records"] > 0
        ),
        "raw_market_data_persistence": bool(settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED),
        "source": one_second["source"], "timeframe": one_second["timeframe"],
        "records": one_second["records"], "instruments": one_second["instruments"],
        "latest_timestamp_ns": one_second["latest_timestamp_ns"],
        "age_seconds": one_second["age_seconds"], "feed_status": runtime_status,
        "persisted_summary": persisted_summary_meta,
        "runtime_feed": {
            "initialized": common is not None,
            "ticks_received": common.get("ticks_received", 0) if common else 0,
            "active_instruments": common.get("active_instruments", 0) if common else 0,
            "socket_groups": common.get("socket_groups", 0) if common else 0,
            "connected_groups": common.get("connected_groups", []) if common else [],
            "disconnected_groups": common.get("disconnected_groups", []) if common else [],
            "connect_failures": common.get("connect_failures", 0) if common else 0,
            "last_connect_failure": common.get("last_connect_failure") if common else None,
            "recovery_attempts": common.get("recovery_attempts", 0) if common else 0,
            "last_recovery_at": common.get("last_recovery_at") if common else None,
            "delivery_errors": common.get("delivery_errors", 0) if common else 0,
            "status": runtime_status,
            "age_seconds": runtime_age,
            "exchange_freshness": exchange_freshness,
            "required_exchange_types": [1, 2],
        },
        "sources": sources, "live_orders": "OFF",
    }


@router.get("/common-feed-health")
@cached_snapshot(ttl_seconds=5.0, max_stale_seconds=20.0)
def get_common_feed_health():
    """Return process-local Angel common WebSocket telemetry; raw ticks are never persisted."""
    from app.core.config import settings
    from app.market_data.common_strategy_feed import shared_common_manager_snapshot

    snapshot = shared_common_manager_snapshot()
    runner_snapshot = (
        _live_cash_future_runner_snapshot_provider()
        if _live_cash_future_runner_snapshot_provider is not None
        else None
    )
    return {
        "status": "success",
        "initialized": snapshot is not None,
        "raw_market_data_persistence": bool(settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED),
        "live_orders": "OFF",
        "cash_future_runner": runner_snapshot,
        "feed": snapshot or {
            "subscriptions": 0,
            "active_instruments": 0,
            "socket_groups": 0,
            "max_socket_sessions": 3,
            "consumers": [],
            "delivery_errors": 0,
            "ticks_received": 0,
            "ticks_by_exchange_type": {},
            "last_tick": None,
            "connected_groups": [],
            "disconnected_groups": [],
            "connect_failures": 0,
            "last_connect_failure": None,
            "recovery_attempts": 0,
            "last_recovery_at": None,
        },
    }


def _resolve_overview_instrument(master, exchange: str, symbol: str, today_key: str):
    """Reuse successful daily instrument resolution instead of rescanning master rows."""
    key = (today_key, str(exchange).strip().upper(), str(symbol).strip().upper())
    now = __import__("time").monotonic()
    with _OVERVIEW_INSTRUMENT_CACHE_LOCK:
        cached = _OVERVIEW_INSTRUMENT_CACHE.get(key)
        if cached is not None and now - cached[0] <= _OVERVIEW_INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]

    instrument = (
        master.resolve_index_instrument(symbol, exchange)
        if exchange in {"NSE", "BSE"}
        else master.get_instrument(symbol, exchange)
    )
    if not instrument:
        return None
    token = str(instrument.get("token", "")).strip()
    if not token:
        return None
    with _OVERVIEW_INSTRUMENT_CACHE_LOCK:
        _OVERVIEW_INSTRUMENT_CACHE[key] = (now, token)
        if len(_OVERVIEW_INSTRUMENT_CACHE) > 32:
            oldest = sorted(
                _OVERVIEW_INSTRUMENT_CACHE,
                key=lambda item: _OVERVIEW_INSTRUMENT_CACHE[item][0],
            )[: len(_OVERVIEW_INSTRUMENT_CACHE) - 32]
            for old_key in oldest:
                _OVERVIEW_INSTRUMENT_CACHE.pop(old_key, None)
    return token


def _overview_quote_batch(client, exchange: str, instruments: list[dict[str, str]]):
    """Read a shared per-process quote batch; throttle broker REST refreshes.

    The dashboard polls more frequently than the broker quote API should be
    called. Keep a bounded exchange/token-set snapshot, and only serve a
    failed-refresh fallback for a short, explicit stale window.
    """
    tokens = tuple(sorted(str(item.get("symboltoken", "")).strip() for item in instruments))
    key = (str(exchange).strip().upper(), tokens)
    now = __import__("time").monotonic()
    with _OVERVIEW_QUOTE_CACHE_LOCK:
        cached = _OVERVIEW_QUOTE_CACHE.get(key)
        if cached is not None:
            age = max(0.0, now - cached[0])
            if age <= _OVERVIEW_QUOTE_CACHE_TTL_SECONDS:
                return cached[1], age, False, None
        failure = _OVERVIEW_QUOTE_FAILURES.get(key)
        if failure is not None and now - failure[0] <= _OVERVIEW_QUOTE_FAILURE_BACKOFF_SECONDS:
            if cached is not None:
                age = max(0.0, now - cached[0])
                if age <= _OVERVIEW_QUOTE_CACHE_MAX_STALE_SECONDS:
                    return cached[1], age, True, "quote refresh cooldown; stale quote snapshot used"
            raise RuntimeError("quote refresh cooldown active after recent broker failure")

    try:
        payload = client.quote_many(exchange, instruments).get("data") or {}
        fetched = payload.get("fetched") or payload.get("data") or []
        if isinstance(fetched, dict):
            fetched = [fetched]
        by_token = {
            str(row.get("symbolToken", row.get("token", ""))): row
            for row in fetched
            if isinstance(row, dict)
        }
        refreshed_at = __import__("time").monotonic()
        with _OVERVIEW_QUOTE_CACHE_LOCK:
            _OVERVIEW_QUOTE_CACHE[key] = (refreshed_at, by_token)
            _OVERVIEW_QUOTE_FAILURES.pop(key, None)
            # This endpoint has a fixed three-exchange universe. Keep cache
            # cardinality bounded if instrument-master tokens roll over.
            if len(_OVERVIEW_QUOTE_CACHE) > 12:
                oldest = sorted(
                    _OVERVIEW_QUOTE_CACHE,
                    key=lambda item: _OVERVIEW_QUOTE_CACHE[item][0],
                )[: len(_OVERVIEW_QUOTE_CACHE) - 12]
                for old_key in oldest:
                    _OVERVIEW_QUOTE_CACHE.pop(old_key, None)
        return by_token, 0.0, False, None
    except Exception as exc:
        failed_at = __import__("time").monotonic()
        with _OVERVIEW_QUOTE_CACHE_LOCK:
            _OVERVIEW_QUOTE_FAILURES[key] = (failed_at, type(exc).__name__)
            if len(_OVERVIEW_QUOTE_FAILURES) > 12:
                oldest_failure = min(
                    _OVERVIEW_QUOTE_FAILURES,
                    key=lambda item: _OVERVIEW_QUOTE_FAILURES[item][0],
                )
                _OVERVIEW_QUOTE_FAILURES.pop(oldest_failure, None)
            cached = _OVERVIEW_QUOTE_CACHE.get(key)
        if cached is not None:
            age = max(0.0, __import__("time").monotonic() - cached[0])
            if age <= _OVERVIEW_QUOTE_CACHE_MAX_STALE_SECONDS:
                return cached[1], age, True, f"{type(exc).__name__}: stale quote snapshot used"
        raise


@router.get("/overview")
@cached_snapshot(ttl_seconds=1.0, max_stale_seconds=5.0)
def get_market_overview():
    """Return NIFTY/BANKNIFTY spot quotes from the sole Shared Runner; never poll broker REST."""
    provider = _shared_index_runner_snapshot_provider
    snapshot = None
    if callable(provider):
        try:
            candidate = provider()
            snapshot = candidate if isinstance(candidate, dict) else None
        except Exception:
            snapshot = None

    latest_spots = snapshot.get("latest_spots", {}) if snapshot else {}
    records = snapshot.get("records", ()) if snapshot else ()
    records_by_symbol = {
        str(row.get("underlying") or "").strip().upper(): row
        for row in records
        if isinstance(row, dict)
        and str(row.get("instrument_type") or "").strip().lower() == "index"
    }
    indices = []
    for symbol in ("NIFTY", "BANKNIFTY"):
        spot = latest_spots.get(symbol) if isinstance(latest_spots, dict) else None
        row = records_by_symbol.get(symbol, {})
        spot = spot if isinstance(spot, dict) else {}
        ltp = spot.get("price") if spot.get("price") is not None else row.get("ltp")
        close = row.get("close")
        age = spot.get("age_seconds")
        if age is None:
            age = row.get("feed_age_seconds")
        try:
            age = max(0.0, float(age)) if age is not None else None
        except (TypeError, ValueError):
            age = None
        fresh = bool(snapshot and snapshot.get("enabled") and row.get("fresh") and age is not None and age <= 5.0)
        try:
            change_percent = (
                (float(ltp) - float(close)) / float(close) * 100.0
                if ltp is not None and close is not None and float(close) > 0
                else None
            )
        except (TypeError, ValueError, ZeroDivisionError):
            change_percent = None
        indices.append({
            "exchange": "NSE",
            "symbol": symbol,
            "token": None,
            "ltp": ltp,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": close,
            "change_percent": change_percent,
            "volume": row.get("volume"),
            "oi": row.get("oi"),
            "bid": row.get("bid"),
            "ask": row.get("ask"),
            "quote_cache_age_seconds": age,
            "status": "LIVE" if fresh and ltp is not None else "STALE" if ltp is not None else "NO_DATA",
        })

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    market_open = _nse_market_is_open(now)
    return {
        "status": "success",
        "mode": "shared-runner-only",
        "market_session": "OPEN" if market_open else "CLOSED",
        "nse_market_session": "OPEN" if market_open else "CLOSED",
        "mcx_market_session": "DISABLED",
        "indices": indices,
        "commodities": [],
        "errors": [] if snapshot is not None else [{"source": "shared-index-market-data", "error": "runner_snapshot_unavailable"}],
    }


@router.get("/shared-index-runner/health")
def get_shared_index_runner_health():
    """Read-only health/status snapshot of the strategy-unconnected live runner."""
    provider = _shared_index_runner_snapshot_provider
    if provider is None:
        return {"runner": "shared-index-market-data", "enabled": False, "state": "NOT_INITIALIZED"}
    try:
        snapshot = provider()
        return snapshot if isinstance(snapshot, dict) else {"runner": "shared-index-market-data", "state": "UNAVAILABLE"}
    except Exception:
        return {"runner": "shared-index-market-data", "state": "UNAVAILABLE"}

@router.get("/shared-index-runner/health-lite")
def get_shared_index_runner_health_lite():
    """Bounded per-feed/socket health; excludes decorated 52-contract quote records."""
    provider = _shared_index_runner_health_provider
    if provider is None:
        return {"runner": "shared-index-market-data", "enabled": False, "state": "NOT_INITIALIZED"}
    try:
        snapshot = provider()
        return snapshot if isinstance(snapshot, dict) else {"runner": "shared-index-market-data", "state": "UNAVAILABLE"}
    except Exception:
        return {"runner": "shared-index-market-data", "state": "UNAVAILABLE"}

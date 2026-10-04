from fastapi import APIRouter, Query, HTTPException

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger

router = APIRouter(
    prefix="/api/v1/market-data",
    tags=["Market Data"],
)


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
def get_live_data_health():
    """Return bounded live-data persistence and freshness health for the control center."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    from app.market_data.daily_shard_catalog import DailyMarketDataShardCatalog
    from app.core.config import settings

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    market_open = now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 30)
    catalog = DailyMarketDataShardCatalog(settings.BACKTEST_DATA_DB)
    try:
        sources = []
        for source, timeframe in (("angelone-live-1s", "1s"), ("angelone-live", "event")):
            count = catalog.count(source=source, timeframe=timeframe)
            instruments = catalog.instruments(source=source, timeframe=timeframe)
            watermarks = [catalog.watermark(source=source, instrument=i, timeframe=timeframe) for i in instruments]
            latest_ns = max((v for v in watermarks if v is not None), default=None)
            age_seconds = None if latest_ns is None else max(0.0, (now.timestamp() * 1_000_000_000 - latest_ns) / 1_000_000_000)
            status = "LIVE" if market_open and age_seconds is not None and age_seconds <= 5 else (
                "STALE" if market_open and age_seconds is not None else "NO_DATA"
            )
            sources.append({
                "source": source, "timeframe": timeframe, "records": count,
                "instruments": len(instruments), "latest_timestamp_ns": latest_ns,
                "age_seconds": age_seconds, "status": status,
            })
        one_second = next(x for x in sources if x["source"] == "angelone-live-1s")
        return {
            "status": "success", "market_session": "OPEN" if market_open else "CLOSED",
            "checked_at": now.isoformat(), "persisted": one_second["records"] > 0,
            "source": one_second["source"], "timeframe": one_second["timeframe"],
            "records": one_second["records"], "instruments": one_second["instruments"],
            "latest_timestamp_ns": one_second["latest_timestamp_ns"],
            "age_seconds": one_second["age_seconds"], "feed_status": one_second["status"],
            "sources": sources, "live_orders": "OFF",
        }
    finally:
        catalog.close()


@router.get("/common-feed-health")
def get_common_feed_health():
    """Return process-local Angel common WebSocket telemetry; raw ticks are never persisted."""
    from app.core.config import settings
    from app.market_data.common_strategy_feed import shared_common_manager_snapshot

    snapshot = shared_common_manager_snapshot()
    return {
        "status": "success",
        "initialized": snapshot is not None,
        "raw_market_data_persistence": bool(settings.LIVE_MARKET_DATA_PERSISTENCE_ENABLED),
        "live_orders": "OFF",
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
        },
    }


@router.get("/overview")
def get_market_overview():
    """Return a bounded live overview for configured NSE/BSE indices and MCX commodities."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    from app.market_data.instruments import InstrumentMaster

    index_specs = [
        *(("NSE", symbol) for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")),
        *(("BSE", symbol) for symbol in ("SENSEX", "BANKEX")),
    ]
    commodity_specs = [("MCX", symbol) for symbol in ("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS")]
    master = InstrumentMaster()
    client = MarketDataClient()
    historical_client = HistoricalDataClient(client)
    ist = ZoneInfo("Asia/Kolkata")

    def last_trading_closes(exchange, token):
        """Return latest and prior trading-day closes for closed-market fallback."""
        now = datetime.now(ist)
        from_date = (now - timedelta(days=10)).strftime("%Y-%m-%d 00:00")
        to_date = now.strftime("%Y-%m-%d %H:%M")
        response = historical_client.get_candles(
            exchange=exchange,
            symboltoken=token,
            interval="ONE_DAY",
            from_date=from_date,
            to_date=to_date,
        )
        candles = response.get("data") or []
        if not isinstance(candles, list):
            return None, None
        valid = [
            row for row in candles
            if isinstance(row, (list, tuple)) and len(row) >= 5 and row[4] is not None
        ]
        if not valid:
            return None, None
        latest = float(valid[-1][4])
        prior = float(valid[-2][4]) if len(valid) >= 2 else latest
        return latest, prior

    def resolve(specs):
        resolved = []
        errors = []
        for exchange, symbol in specs:
            try:
                instrument = master.resolve_index_instrument(symbol, exchange) if exchange in {"NSE", "BSE"} else master.get_instrument(symbol, exchange)
                if not instrument:
                    errors.append({"exchange": exchange, "symbol": symbol, "error": "instrument_not_found"})
                    continue
                resolved.append((exchange, symbol, str(instrument.get("token", ""))))
            except Exception as exc:
                errors.append({"exchange": exchange, "symbol": symbol, "error": str(exc)})
        return resolved, errors

    def fetch(specs):
        resolved, errors = resolve(specs)
        by_exchange = {}
        for exchange, symbol, token in resolved:
            if token:
                by_exchange.setdefault(exchange, []).append({"symbol": symbol, "symboltoken": token})
        rows = []
        for exchange, instruments in by_exchange.items():
            try:
                payload = client.quote_many(exchange, instruments).get("data") or {}
                fetched = payload.get("fetched") or payload.get("data") or []
                if isinstance(fetched, dict):
                    fetched = [fetched]
                by_token = {str(row.get("symbolToken", row.get("token", ""))): row for row in fetched if isinstance(row, dict)}
                for item in instruments:
                    quote = by_token.get(item["symboltoken"], {})
                    fallback_ltp = None
                    fallback_previous = None
                    status = "LIVE" if quote else "NO_QUOTE"
                    if not quote or quote.get("ltp") is None or quote.get("close") is None:
                        try:
                            fallback_ltp, fallback_previous = last_trading_closes(exchange, item["symboltoken"])
                            if fallback_ltp is not None:
                                status = "CLOSED_LAST_CLOSE"
                        except Exception as fallback_exc:
                            errors.append({
                                "exchange": exchange,
                                "symbol": item["symbol"],
                                "error": f"last_close_fallback_failed: {fallback_exc}",
                            })
                    ltp = quote.get("ltp") if quote.get("ltp") is not None else fallback_ltp
                    close = quote.get("close") if quote.get("close") is not None else fallback_previous
                    rows.append({"exchange": exchange, "symbol": item["symbol"], "token": item["symboltoken"],
                                 "ltp": ltp, "open": quote.get("open"), "high": quote.get("high"),
                                 "low": quote.get("low"), "close": close, "change_percent": quote.get("percentChange"),
                                 "volume": quote.get("tradeVolume"), "oi": quote.get("opnInterest"),
                                 "bid": (quote.get("depth", {}).get("buy", [{}])[0].get("price") if quote.get("depth") else None),
                                 "ask": (quote.get("depth", {}).get("sell", [{}])[0].get("price") if quote.get("depth") else None),
                                 "status": status})
            except Exception as exc:
                errors.extend({"exchange": exchange, "symbol": item["symbol"], "error": str(exc)} for item in instruments)
        return rows, errors

    indices, index_errors = fetch(index_specs)
    commodities, commodity_errors = fetch(commodity_specs)
    return {"status": "success", "mode": "live", "indices": indices, "commodities": commodities,
            "errors": index_errors + commodity_errors}

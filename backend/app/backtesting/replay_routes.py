"""Common saved-live-data replay API.

Only persisted live market data is replayed. No historical downloader is used.
The selected range is streamed through MarketDataReplay.
"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from app.backtesting.daily_market_data_shard_catalog import DailyMarketDataShardCatalog
from app.backtesting.market_data_replay import MarketDataReplay

def create_replay_router(base_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/backtesting/replay", tags=["Market Data Replay"])

    @router.get("/instruments")
    def instruments(timeframe: str = Query("1s", min_length=1), source: str = Query("angelone-live-1s", min_length=1), start_ns: int | None = None, end_ns: int | None = None):
        catalog = DailyMarketDataShardCatalog(base_path)
        try:
            return {"status": "success", "source": source, "timeframe": timeframe, "instruments": catalog.instruments(source=source, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns)}
        finally:
            catalog.close()

    @router.get("/stream")
    def stream(instrument: str, start_ns: int | None = None, end_ns: int | None = None, timeframe: str = Query("1s", min_length=1), source: str = Query("angelone-live-1s", min_length=1), limit: int = Query(5000, ge=1, le=10000)):
        if start_ns is not None and start_ns < 0:
            raise HTTPException(422, detail="start_ns must be non-negative")
        if end_ns is not None and start_ns is not None and end_ns < start_ns:
            raise HTTPException(422, detail="invalid timestamp range")
        catalog = DailyMarketDataShardCatalog(base_path)
        try:
            replay = MarketDataReplay(catalog.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns))
            events=[]
            count=0
            def collect(event):
                nonlocal count
                if count < limit:
                    events.append({
                        "timestamp_ns": event.record.timestamp_ns,
                        "symbol": event.record.symbol,
                        "instrument_type": event.record.instrument_type.value,
                        "ltp": event.record.ltp,
                        "bid": event.record.bid,
                        "ask": event.record.ask,
                        "volume": event.record.volume,
                        "oi": event.record.oi,
                        "expiry": event.record.expiry,
                        "strike": event.record.strike,
                        "option_type": event.record.option_type.value if event.record.option_type else None,
                    })
                count += 1
            replay.stream(collect)
            return {"status": "success", "source": source, "timeframe": timeframe, "instrument": instrument, "count": count, "data": events}
        finally:
            catalog.close()
    return router

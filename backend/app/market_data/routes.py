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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ltp-by-symbol")
def get_ltp_by_symbol(
    tradingsymbol: str = Query(..., min_length=1),
    exchange: str = Query("NSE"),
):
    """Resolve an NSE/BSE symbol through the Angel One master and fetch its LTP."""
    from app.market_data.client import MarketDataClient
    from app.market_data.instruments import InstrumentMaster

    try:
        symbol = tradingsymbol.strip().upper()
        segment = exchange.strip().upper()
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger
from app.strategy_engine.arbitrage import ArbitrageEngine

router = APIRouter(
    prefix="/api/v1/strategy/arbitrage",
    tags=["Arbitrage Strategy"],
)

@router.get("/evaluate")
def evaluate_arbitrage(
    symbol: str = Query(..., description="Trading symbol e.g. RELIANCE"),
    price_a: float = Query(..., description="Price on Exchange A (e.g. NSE)"),
    price_b: float = Query(..., description="Price on Exchange B (e.g. BSE)"),
    threshold: float = Query(0.5, description="Min threshold percentage to trigger opportunity"),
):
    """Evaluate price difference and detect arbitrage opportunities between two prices."""
    try:
        app_logger.info(f"Evaluating arbitrage request for {symbol}: {price_a} vs {price_b}")
        engine = ArbitrageEngine(threshold_percent=threshold)
        result = engine.evaluate_opportunity(symbol=symbol, exchange_a_price=price_a, exchange_b_price=price_b)
        return {"status": "success", "data": result}
    except Exception as e:
        app_logger.error(f"Arbitrage evaluation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

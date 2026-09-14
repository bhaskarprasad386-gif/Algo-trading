from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger
from app.strategy_engine.arbitrage import ArbitrageEngine

router = APIRouter(
    prefix="/api/v1/strategy/arbitrage",
    tags=["Arbitrage Strategy"],
)


@router.get("/evaluate")
def evaluate_arbitrage(
    symbol: str = Query(..., min_length=1),
    price_a: float = Query(..., gt=0),
    price_b: float = Query(..., gt=0),
    threshold: float = Query(0.5, ge=0),
):
    """Evaluate price difference and detect arbitrage opportunities between two prices."""
    try:
        app_logger.info(f"Evaluating arbitrage request for {symbol}: {price_a} vs {price_b}")
        engine = ArbitrageEngine(threshold_percent=threshold)
        result = engine.evaluate_opportunity(symbol=symbol, exchange_a_price=price_a, exchange_b_price=price_b)
        return {"status": "success", "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        app_logger.error(f"Arbitrage evaluation error: {exc}")
        raise HTTPException(status_code=502, detail="Arbitrage evaluation failed") from exc

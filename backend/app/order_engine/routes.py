from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger
from app.core.config import settings
from app.order_engine.client import OrderExecutionClient
from app.risk.engine import RiskEngine, RiskLimits

router = APIRouter(prefix="/api/v1/orders", tags=["Order Execution"])
risk_engine = RiskEngine(RiskLimits(
    max_orders_per_day=settings.MAX_ORDERS_PER_DAY,
    max_quantity_per_order=settings.MAX_QUANTITY_PER_ORDER,
    max_position_quantity=settings.MAX_POSITION_QUANTITY,
    max_loss=settings.MAX_LOSS,
))


@router.post("/place")
def place_manual_order(
    symbol: str = Query(..., min_length=1),
    exchange: str = Query("NSE", min_length=1),
    transaction_type: str = Query("BUY", pattern="^(BUY|SELL)$"),
    quantity: int = Query(10, ge=1),
    price: float = Query(..., gt=0),
    mode: str = Query("paper", pattern="^paper$", description="Only paper mode is enabled."),
):
    """Atomically risk-check and simulate a paper order."""
    try:
        normalized_symbol = symbol.strip().upper()
        risk_engine.check_and_reserve(normalized_symbol, transaction_type, quantity, price)
        result = OrderExecutionClient(mode="paper").place_order(
            symbol=normalized_symbol, exchange=exchange, transaction_type=transaction_type,
            quantity=quantity, price=price,
        )
        app_logger.info(f"Paper order accepted for {normalized_symbol} ({quantity})")
        return {"status": "success", "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        app_logger.error(f"Order placement error: {exc}")
        raise HTTPException(status_code=500, detail="Order processing failed")

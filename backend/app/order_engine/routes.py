from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger
from app.core.config import settings
from app.order_engine.client import OrderExecutionClient
from app.risk.engine import RiskEngine, RiskLimits

router = APIRouter(prefix="/api/v1/orders", tags=["Order Execution"])


def _risk_engine() -> RiskEngine:
    return RiskEngine(RiskLimits(
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
    price: float = Query(0.0, ge=0.0),
    mode: str = Query("paper", pattern="^paper$", description="Only paper mode is enabled until live trading is explicitly implemented and approved."),
):
    """Risk-check and simulate an order. Live execution is intentionally unavailable."""
    try:
        risk = _risk_engine()
        allowed, reason = risk.check(quantity=quantity)
        if not allowed:
            raise HTTPException(status_code=409, detail=reason)

        result = OrderExecutionClient(mode="paper").place_order(
            symbol=symbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            price=price,
        )
        risk.reserve_order()
        app_logger.info(f"Paper order accepted for {symbol} ({quantity})")
        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        app_logger.error(f"Order placement error: {exc}")
        raise HTTPException(status_code=500, detail="Order processing failed")

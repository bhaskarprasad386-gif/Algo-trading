from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger
from app.order_engine.client import OrderExecutionClient

router = APIRouter(
    prefix="/api/v1/orders",
    tags=["Order Execution"],
)

@router.post("/place")
def place_manual_order(
    symbol: str = Query(..., description="Trading symbol e.g. RELIANCE"),
    exchange: str = Query("NSE", description="Exchange name e.g. NSE/BSE"),
    transaction_type: str = Query("BUY", description="BUY or SELL"),
    quantity: int = Query(10, description="Order quantity"),
    price: float = Query(0.0, description="Estimated price"),
    mode: str = Query("paper", description="paper or live"),
):
    """Manually place or simulate an order via OrderExecutionClient."""
    try:
        app_logger.info(f"Manual order request for {symbol} on {exchange}")
        client = OrderExecutionClient(mode=mode)
        result = client.place_order(symbol=symbol, exchange=exchange, transaction_type=transaction_type, quantity=quantity, price=price)
        return {"status": "success", "data": result}
    except Exception as e:
        app_logger.error(f"Order placement error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

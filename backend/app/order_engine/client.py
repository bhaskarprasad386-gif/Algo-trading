from app.core.logger import app_logger


class OrderExecutionClient:
    """Order execution foundation with an explicit paper/live boundary."""

    VALID_MODES = {"paper", "live"}
    VALID_TRANSACTION_TYPES = {"BUY", "SELL"}

    def __init__(self, mode: str = "paper"):
        normalized_mode = mode.strip().lower()
        if normalized_mode not in self.VALID_MODES:
            raise ValueError("mode must be 'paper' or 'live'")
        self.mode = normalized_mode
        app_logger.info(
            f"OrderExecutionClient initialized in {self.mode.upper()} trading mode."
        )

    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float = 0.0,
    ):
        """Validate and place a paper order; live execution remains disabled."""
        symbol = symbol.strip().upper()
        exchange = exchange.strip().upper()
        transaction_type = transaction_type.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")
        if not exchange:
            raise ValueError("exchange is required")
        if transaction_type not in self.VALID_TRANSACTION_TYPES:
            raise ValueError("transaction_type must be BUY or SELL")
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if price < 0:
            raise ValueError("price cannot be negative")

        app_logger.info(
            f"[{self.mode.upper()}] Placing {transaction_type} order for "
            f"{quantity} shares of {symbol} on {exchange} at approx {price}"
        )

        if self.mode == "paper":
            return {
                "status": "success",
                "mode": "paper",
                "order_id": "PAPER_ORD_987654321",
                "symbol": symbol,
                "exchange": exchange,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "estimated_price": price,
                "message": "Paper order simulated successfully.",
            }

        return {
            "status": "pending_live_integration",
            "mode": "live",
            "message": "Live broker execution is intentionally disabled until broker order integration is completed.",
        }

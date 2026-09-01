from app.core.logger import app_logger


class ArbitrageEngine:
    """Pure signal engine. It never places orders directly."""

    def __init__(self, threshold_percent: float = 0.5):
        if threshold_percent < 0:
            raise ValueError("threshold_percent cannot be negative")
        self.threshold_percent = threshold_percent
        app_logger.info(f"ArbitrageEngine initialized with threshold: {self.threshold_percent}%")

    def calculate_spread(self, price_a: float, price_b: float) -> float:
        if price_a <= 0 or price_b <= 0:
            return 0.0
        diff = abs(price_a - price_b)
        avg_price = (price_a + price_b) / 2
        return round((diff / avg_price) * 100, 4)

    def evaluate_opportunity(self, symbol: str, exchange_a_price: float, exchange_b_price: float, quantity: int = 10):
        """Return a signal only; execution belongs behind RiskEngine and Order Manager."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")

        spread = self.calculate_spread(exchange_a_price, exchange_b_price)
        if spread < self.threshold_percent:
            return {"status": "no_opportunity", "symbol": symbol, "spread_percent": spread}

        if exchange_a_price < exchange_b_price:
            buy_exchange, sell_exchange = "EXCHANGE_A", "EXCHANGE_B"
            buy_price, sell_price = exchange_a_price, exchange_b_price
        else:
            buy_exchange, sell_exchange = "EXCHANGE_B", "EXCHANGE_A"
            buy_price, sell_price = exchange_b_price, exchange_a_price

        return {
            "status": "opportunity",
            "symbol": symbol,
            "spread_percent": spread,
            "quantity": quantity,
            "buy": {"exchange": buy_exchange, "price": buy_price},
            "sell": {"exchange": sell_exchange, "price": sell_price},
            "execution_required": False,
        }

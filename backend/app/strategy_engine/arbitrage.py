from app.core.logger import app_logger
from app.order_engine.client import OrderExecutionClient

class ArbitrageEngine:
    """Base engine to monitor arbitrage opportunities and execute orders via OrderExecutionClient."""

    def __init__(self, threshold_percent: float = 0.5, mode: str = "paper"):
        self.threshold_percent = threshold_percent
        self.order_client = OrderExecutionClient(mode=mode)
        app_logger.info(f"ArbitrageEngine initialized with threshold: {self.threshold_percent}%")

    def calculate_spread(self, price_a: float, price_b: float) -> float:
        """Calculate percentage difference between two prices."""
        if price_a <= 0 or price_b <= 0:
            return 0.0
        diff = abs(price_a - price_b)
        avg_price = (price_a + price_b) / 2
        spread_percent = (diff / avg_price) * 100
        return round(spread_percent, 4)

    def evaluate_opportunity(self, symbol: str, exchange_a_price: float, exchange_b_price: float, quantity: int = 10):
        """Evaluate price spread and trigger orders if threshold is crossed."""
        spread = self.calculate_spread(exchange_a_price, exchange_b_price)
        
        app_logger.info(f"Evaluating {symbol}: Price A={exchange_a_price}, Price B={exchange_b_price}, Spread={spread}%")

        if spread >= self.threshold_percent:
            app_logger.warning(f"Arbitrage Opportunity Detected for {symbol}! Spread: {spread}%")
            
            # Determine buy and sell exchanges based on prices
            if exchange_a_price < exchange_b_price:
                buy_ex, sell_ex = "EXCHANGE_A", "EXCHANGE_B"
                buy_price, sell_price = exchange_a_price, exchange_b_price
            else:
                buy_ex, sell_ex = "EXCHANGE_B", "EXCHANGE_A"
                buy_price, sell_price = exchange_b_price, exchange_a_price

            # Automatically trigger orders using OrderExecutionClient
            buy_order = self.order_client.place_order(symbol, buy_ex, "BUY", quantity, buy_price)
            sell_order = self.order_client.place_order(symbol, sell_ex, "SELL", quantity, sell_price)

            return {
                "status": "opportunity_executed",
                "symbol": symbol,
                "spread_percent": spread,
                "buy_execution": buy_order,
                "sell_execution": sell_order
            }
        
        return {"status": "no_opportunity", "symbol": symbol, "spread_percent": spread}

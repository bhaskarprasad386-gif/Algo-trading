from app.core.logger import app_logger

class ArbitrageEngine:
    """Base engine to monitor and execute arbitrage opportunities between exchanges or segments."""

    def __init__(self, threshold_percent: float = 0.5):
        self.threshold_percent = threshold_percent
        app_logger.info(f"ArbitrageEngine initialized with threshold: {self.threshold_percent}%")

    def calculate_spread(self, price_a: float, price_b: float) -> float:
        """Calculate percentage difference between two prices."""
        if price_a <= 0 or price_b <= 0:
            return 0.0
        diff = abs(price_a - price_b)
        avg_price = (price_a + price_b) / 2
        spread_percent = (diff / avg_price) * 100
        return round(spread_percent, 4)

    def evaluate_opportunity(self, symbol: str, exchange_a_price: float, exchange_b_price: float):
        """Evaluate if the price spread crosses the profitable threshold."""
        spread = self.calculate_spread(exchange_a_price, exchange_b_price)
        
        app_logger.info(f"Evaluating {symbol}: Price A={exchange_a_price}, Price B={exchange_b_price}, Spread={spread}%")

        if spread >= self.threshold_percent:
            app_logger.warning(f"Arbitrage Opportunity Detected for {symbol}! Spread: {spread}%")
            return {
                "status": "opportunity_found",
                "symbol": symbol,
                "spread_percent": spread,
                "buy_exchange": "Exchange A" if exchange_a_price < exchange_b_price else "Exchange B",
                "sell_exchange": "Exchange B" if exchange_a_price < exchange_b_price else "Exchange A"
            }
        
        return {"status": "no_opportunity", "symbol": symbol, "spread_percent": spread}

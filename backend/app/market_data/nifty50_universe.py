"""Locked Box Spread universe: Nifty 50 stocks plus NSE/BSE F&O indices."""

NIFTY50_STOCK_SYMBOLS = frozenset({
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "INDIGO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
})

NIFTY50_INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"})

# Explicit ranked allowlist for the Synthetic scanner. This is intentionally
# separate from the full NIFTY-50 universe used by other scanners. Keep this
# list updated when the intended top-15 coverage changes.
NIFTY50_SYNTHETIC_TOP15_STOCK_SYMBOLS = (
    "HDFCBANK", "RELIANCE", "ICICIBANK", "BHARTIARTL", "INFY",
    "LT", "SBIN", "ITC", "AXISBANK", "KOTAKBANK",
    "M&M", "TCS", "BAJFINANCE", "HINDUNILVR", "SUNPHARMA",
)

def select_synthetic_stock_symbols(available_symbols):
    """Return available synthetic stock targets in the configured top-15 rank order."""
    available = {
        str(symbol).strip().upper()
        for symbol in available_symbols
        if str(symbol).strip()
    }
    return tuple(
        symbol for symbol in NIFTY50_SYNTHETIC_TOP15_STOCK_SYMBOLS
        if symbol in available
    )


__all__ = [
    "NIFTY50_STOCK_SYMBOLS",
    "NIFTY50_INDEX_SYMBOLS",
    "NIFTY50_SYNTHETIC_TOP15_STOCK_SYMBOLS",
    "select_synthetic_stock_symbols",
]

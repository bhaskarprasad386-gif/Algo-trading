"""Exchange-aware weekly/monthly classification for Box Spread index options."""
from datetime import date
from typing import Iterable

# SEBI permits one weekly index-derivatives product per exchange. NSE's weekly
# index option is NIFTY; BSE's is SENSEX. Other supported indices are monthly.
WEEKLY_EXPIRY_INDEX_SYMBOLS = frozenset({"NIFTY", "SENSEX"})


def classify_index_expiry_dates(symbol: str, dates: Iterable[date]) -> dict[date, str]:
    """Classify listed expiries without inventing weekly cycles for monthly-only indices.

    For NIFTY and SENSEX, the final listed expiry in each calendar month is
    the monthly contract; earlier listed expiries in that month are weekly.
    All listed expiries for other supported indices are monthly.
    """
    normalized_symbol = str(symbol or "").strip().upper()
    unique_dates = sorted(set(dates))
    if normalized_symbol not in WEEKLY_EXPIRY_INDEX_SYMBOLS:
        return {expiry: "MONTHLY" for expiry in unique_dates}

    monthly_dates = {
        max(
            (expiry for expiry in unique_dates if expiry.year == year and expiry.month == month),
            default=None,
        )
        for year, month in {(expiry.year, expiry.month) for expiry in unique_dates}
    }
    monthly_dates.discard(None)
    return {
        expiry: "MONTHLY" if expiry in monthly_dates else "WEEKLY"
        for expiry in unique_dates
    }

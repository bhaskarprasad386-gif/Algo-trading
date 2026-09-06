"""NSE 2025 segment-aware trading holidays.

Sources:
- NSE/CMTR/65587 (13-Dec-2024), Capital Market Segment.
- NSE/FAOP/65588 (13-Dec-2024), Futures & Options Segment.

Weekend sessions are never inferred. Muhurat dates are tracked separately
until the exchange publishes exact session timings.
"""

from datetime import date

NSE_EQUITY_TRADING_HOLIDAYS_2025 = frozenset(
    {
        date(2025, 2, 26),
        date(2025, 3, 14),
        date(2025, 3, 31),
        date(2025, 4, 10),
        date(2025, 4, 14),
        date(2025, 4, 18),
        date(2025, 5, 1),
        date(2025, 8, 15),
        date(2025, 8, 27),
        date(2025, 10, 2),
        date(2025, 10, 21),
        date(2025, 10, 22),
        date(2025, 11, 5),
        date(2025, 12, 25),
    }
)

NSE_FNO_TRADING_HOLIDAYS_2025 = frozenset(
    {
        date(2025, 2, 26),
        date(2025, 3, 14),
        date(2025, 3, 31),
        date(2025, 4, 10),
        date(2025, 4, 14),
        date(2025, 4, 18),
        date(2025, 5, 1),
        date(2025, 8, 15),
        date(2025, 8, 27),
        date(2025, 10, 2),
        date(2025, 10, 21),
        date(2025, 10, 22),
        date(2025, 11, 5),
        date(2025, 12, 25),
    }
)

NSE_MUHURAT_TRADING_DATES_2025 = frozenset({date(2025, 10, 21)})

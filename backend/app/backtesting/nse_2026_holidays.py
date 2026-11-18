"""NSE 2026 segment-aware trading calendars.

Sources:
- NSE Market Timings & Holidays (2026) for equities.
- NSE/FAOP/71777 dated 2025-12-12 for F&O trading holidays.

Holiday data is explicit and versioned. Weekend sessions are never inferred;
Muhurat Trading is represented separately because its 2026 timing was to be
notified by NSE later.
"""

from datetime import date

NSE_EQUITY_TRADING_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 15),
        date(2026, 1, 26),
        date(2026, 2, 19),
        date(2026, 3, 3),
        date(2026, 3, 19),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 8, 26),
        date(2026, 9, 14),
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 12, 25),
    }
)

NSE_FNO_TRADING_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 26),
        date(2026, 3, 3),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 9, 14),
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 12, 25),
    }
)

# NSE has announced Muhurat Trading on Sunday 2026-11-08, but the exact
# session timing is to be notified separately. Do not invent a completeness
# window until that circular is available.
NSE_MUHURAT_TRADING_DATES_2026 = frozenset({date(2026, 11, 8)})

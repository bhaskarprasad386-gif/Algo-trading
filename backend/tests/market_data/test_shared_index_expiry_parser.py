from datetime import date

import pytest

from app.market_data import shared_index_option_selection as selection
from app.market_data.shared_index_market_data_runner import choose_locked_expiry


def test_expiry_parser_avoids_process_global_strptime_lock(monkeypatch):
    class ForbiddenDateTime:
        @staticmethod
        def strptime(*args, **kwargs):
            raise AssertionError("shared-index expiry parsing must not use datetime.strptime")

    # The old parser reached CPython's process-global _strptime._cache_lock from
    # the broker callback. Keep that path forbidden as a deterministic regression.
    monkeypatch.setattr(selection, "datetime", ForbiddenDateTime, raising=False)
    selection._parse_expiry_text.cache_clear()

    assert selection._parse_expiry("26NOV2026") == date(2026, 11, 26)
    assert selection._parse_expiry(" 26nov2026 ") == date(2026, 11, 26)
    assert selection._parse_expiry("26NOV26") == date(2026, 11, 26)
    assert selection._parse_expiry("2026-11-26") == date(2026, 11, 26)
    assert selection._parse_expiry("01JAN68") == date(2068, 1, 1)
    assert selection._parse_expiry("01JAN69") == date(1969, 1, 1)

    cache = selection._parse_expiry_text.cache_info()
    assert cache.misses == 5
    assert cache.hits == 1
    with pytest.raises(ValueError, match="invalid expiry"):
        selection._parse_expiry("not-an-expiry")


def test_locked_expiry_selection_parses_distinct_master_expiries_not_each_contract():
    selection._parse_expiry_text.cache_clear()
    rows = [
        {
            "exch_seg": "NFO",
            "name": "NIFTY",
            "instrumenttype": "OPTIDX",
            "expiry": expiry,
        }
        for expiry in ("19NOV2026", "26NOV2026", "03DEC2026", "10DEC2026")
        for _ in range(40)
    ]

    assert choose_locked_expiry(rows, "NIFTY", today=date(2026, 11, 20)) == "2026-12-03"
    assert selection._parse_expiry_text.cache_info().misses == 4

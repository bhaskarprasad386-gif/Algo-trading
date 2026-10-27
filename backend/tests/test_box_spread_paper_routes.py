from fastapi import HTTPException
import pytest

from app.scanner import box_spread_paper_routes as routes


def test_paper_quote_selector_uses_ask_for_buys_and_bid_for_sells():
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40, "low_put_ask": 41,
    }
    assert routes._reference_prices_for_direction(row, "LONG") == {
        "LOW_CE": 11, "HIGH_CE": 20, "HIGH_PE": 31, "LOW_PE": 40,
    }
    assert routes._reference_prices_for_direction(row, "SHORT") == {
        "LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41,
    }


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-10-29", "2026-10-29"),
    ("29OCT2026", "2026-10-29"),
    ("29-Oct-26", "2026-10-29"),
    ("20261029", "2026-10-29"),
])
def test_paper_expiry_comparison_canonicalizes_instrument_master_formats(raw, expected):
    assert routes._canonical_expiry(raw) == expected


def test_paper_error_handler_preserves_http_status():
    with pytest.raises(HTTPException) as raised:
        routes._error(HTTPException(status_code=409, detail="stale quotes"))
    assert raised.value.status_code == 409

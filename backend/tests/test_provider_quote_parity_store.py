from datetime import date
from decimal import Decimal

import pytest

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_parity import analyze_quote_parity
from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore
from app.market_data.provider_quote_quality import ProviderQuoteObservation

NOW_MONOTONIC = 90_000_000_000
NOW_EXCHANGE = 1_800_000_000_000_000_000
MAX_AGE = 5_000_000_000


def _contract():
    return CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal("22000"), option_type="CE",
    )


def _quote(provider, *, bid="100.00", ask="100.10", contract=None):
    return ProviderQuoteObservation(
        provider=provider, contract=contract or _contract(), generation=1,
        authenticated=True, connection_current=True,
        exchange_timestamp_ns=NOW_EXCHANGE - 1_000_000_000,
        received_monotonic_ns=NOW_MONOTONIC - 1_000_000_000,
        bid=bid, ask=ask, bid_size=25, ask_size=30, depth_complete=True,
    )


def _metrics(left=None, right=None):
    return analyze_quote_parity(
        left or _quote("angel"), right or _quote("shoonya", bid="100.05", ask="100.15"),
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=MAX_AGE,
    )


def _key():
    return SQLiteQuoteParityStore._contract_key(_metrics())


def test_sqlite_store_persists_decimal_metrics_across_reopen(tmp_path):
    path = tmp_path / "parity.sqlite3"
    store = SQLiteQuoteParityStore(path)
    row_id = store.record(_metrics(), observed_at_utc_ns=1_800_000_000_000_000_000)
    store.close()

    reopened = SQLiteQuoteParityStore(path)
    rows = reopened.latest(contract_key=_key())
    reopened.close()

    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["quality_state"] == "COMPARABLE"
    assert rows[0]["metrics"]["bid_abs_diff"] == "0.05"
    assert rows[0]["metrics"]["mid_abs_diff"] == "0.05"


def test_identity_mismatch_persists_quality_but_no_price_metrics(tmp_path):
    path = tmp_path / "parity.sqlite3"
    store = SQLiteQuoteParityStore(path)
    mismatch = _metrics(
        _quote("angel"),
        _quote("shoonya", contract=CanonicalContractKey(
            exchange="NFO", segment="OPT", underlying="NIFTY",
            instrument_type="OPTION", expiry=date(2026, 11, 19),
            strike=Decimal("22100"), option_type="CE",
        )),
    )
    store.record(mismatch, observed_at_utc_ns=1_800_000_000_000_000_000)
    rows = store.latest(contract_key=SQLiteQuoteParityStore._contract_key(mismatch))
    store.close()

    assert rows[0]["quality_state"] == "IDENTITY_MISMATCH"
    assert rows[0]["metrics"]["bid_abs_diff"] is None
    assert rows[0]["identity_match"] is False


def test_prune_uses_utc_wall_clock_cutoff_and_returns_deleted_count(tmp_path):
    store = SQLiteQuoteParityStore(tmp_path / "parity.sqlite3")
    store.record(_metrics(), observed_at_utc_ns=100)
    store.record(_metrics(), observed_at_utc_ns=200)
    store.record(_metrics(), observed_at_utc_ns=300)
    deleted = store.prune_before(cutoff_utc_ns=250)
    remaining = store.latest(contract_key=_key(), limit=10)
    store.close()

    assert deleted == 2
    assert [row["observed_at_utc_ns"] for row in remaining] == [300]


def test_latest_limit_is_bounded_and_rejects_boolean(tmp_path):
    store = SQLiteQuoteParityStore(tmp_path / "parity.sqlite3")
    with pytest.raises(ValueError, match="between 1 and 1000"):
        store.latest(contract_key=_key(), limit=True)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        store.latest(contract_key=_key(), limit=1001)
    store.close()


def test_record_rejects_invalid_timestamp_and_wrong_payload(tmp_path):
    store = SQLiteQuoteParityStore(tmp_path / "parity.sqlite3")
    with pytest.raises(ValueError, match="positive integer"):
        store.record(_metrics(), observed_at_utc_ns=0)
    with pytest.raises(TypeError, match="QuoteParityMetrics"):
        store.record(object(), observed_at_utc_ns=100)
    store.close()

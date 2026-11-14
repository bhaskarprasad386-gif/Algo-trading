from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_parity import analyze_quote_parity
from app.market_data.provider_quote_parity_routes import (
    configure_store, read_provider_quote_parity,
)
from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore
from app.market_data.provider_quote_quality import ProviderQuoteObservation

NOW_MONOTONIC = 90_000_000_000
NOW_EXCHANGE = 1_800_000_000_000_000_000


def _metrics():
    contract = CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal("22000"), option_type="CE",
    )
    def quote(provider, bid):
        return ProviderQuoteObservation(
            provider=provider, contract=contract, generation=1,
            authenticated=True, connection_current=True,
            exchange_timestamp_ns=NOW_EXCHANGE - 1_000_000_000,
            received_monotonic_ns=NOW_MONOTONIC - 1_000_000_000,
            bid=bid, ask="100.10", bid_size=25, ask_size=30,
            depth_complete=True,
        )
    return analyze_quote_parity(
        quote("angel", "100.00"), quote("shoonya", "100.05"),
        now_monotonic_ns=NOW_MONOTONIC,
        now_exchange_timestamp_ns=NOW_EXCHANGE,
        max_age_ns=5_000_000_000,
    )


def test_read_only_route_returns_only_persisted_diagnostic_rows(tmp_path):
    store = SQLiteQuoteParityStore(tmp_path / "api.sqlite3")
    metric = _metrics()
    store.record(metric, observed_at_utc_ns=NOW_EXCHANGE)
    configure_store(store)
    try:
        response = read_provider_quote_parity(
            contract_key=store._contract_key(metric), limit=10
        )
    finally:
        configure_store(None)
        store.close()

    assert len(response["items"]) == 1
    assert response["items"][0]["metrics"]["bid_abs_diff"] == "0.05"
    assert "bid" not in response["items"][0]
    assert "ask" not in response["items"][0]


def test_route_fails_closed_when_diagnostics_disabled():
    configure_store(None)
    with pytest.raises(HTTPException) as exc:
        read_provider_quote_parity(contract_key="{}", limit=10)
    assert exc.value.status_code == 503

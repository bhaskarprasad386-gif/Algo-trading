from datetime import date
from decimal import Decimal

import pytest

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore
from app.market_data.provider_quote_parity_worker import BoundedQuoteParityWorker
from app.market_data.provider_quote_pairer import ProviderQuotePairer
from app.market_data.provider_quote_quality import ProviderQuoteObservation

NOW = 1_800_000_000_000_000_000


def _quote(provider, generation=1, *, received=100, exchange_timestamp=NOW - 100):
    contract = CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal("22000"), option_type="CE",
    )
    return ProviderQuoteObservation(
        provider=provider, contract=contract, generation=generation,
        authenticated=True, connection_current=True,
        exchange_timestamp_ns=exchange_timestamp,
        received_monotonic_ns=received,
        bid="100.00", ask="100.10", bid_size=10, ask_size=12,
        depth_complete=True,
    )


def _pairer(tmp_path, capacity=8):
    store = SQLiteQuoteParityStore(tmp_path / "pairer.sqlite3")
    worker = BoundedQuoteParityWorker(store, capacity=capacity)
    pairer = ProviderQuotePairer(worker, max_contracts=8, clock_ns=lambda: NOW)
    pairer.activate_generation("angel_one", 1)
    pairer.activate_generation("shoonya", 1)
    return store, worker, pairer


def test_pairer_waits_for_both_providers_and_submits_one_pair(tmp_path):
    store, worker, pairer = _pairer(tmp_path)
    worker.start()
    assert pairer.submit(_quote("angel_one"), received_monotonic_ns=110, max_age_ns=1_000_000)
    assert worker.snapshot()["submitted"] == 0
    assert pairer.submit(_quote("shoonya", received=120), received_monotonic_ns=120, max_age_ns=1_000_000)
    assert worker.snapshot()["submitted"] == 1
    assert pairer.snapshot()["pairs_submitted"] == 1
    assert worker.stop(drain=True)
    store.close()


def test_pairer_rejects_untrusted_and_wrong_generation_observations(tmp_path):
    store, worker, pairer = _pairer(tmp_path)
    bad = _quote("angel_one")
    bad = ProviderQuoteObservation(**{**bad.__dict__, "authenticated": False})
    assert pairer.submit(bad, received_monotonic_ns=110, max_age_ns=1_000_000) is False
    assert pairer.submit(_quote("shoonya", generation=2), received_monotonic_ns=120, max_age_ns=1_000_000) is False
    snapshot = pairer.snapshot()
    assert snapshot["rejected_untrusted"] == 1
    assert snapshot["rejected_generation"] == 1
    worker.stop(drain=False)
    store.close()


def test_generation_activation_discards_old_pending_provider_quote(tmp_path):
    store, worker, pairer = _pairer(tmp_path)
    assert pairer.submit(_quote("angel_one"), received_monotonic_ns=110, max_age_ns=1_000_000)
    pairer.activate_generation("angel_one", 2)
    assert pairer.submit(_quote("shoonya"), received_monotonic_ns=120, max_age_ns=1_000_000)
    assert worker.snapshot()["submitted"] == 0
    assert pairer.snapshot()["pending_quotes"] == 1
    worker.stop(drain=False)
    store.close()


def test_pairer_bounds_pending_and_emitted_identity_memory(tmp_path):
    store, worker, pairer = _pairer(tmp_path)
    for index in range(30):
        contract = CanonicalContractKey(
            exchange="NFO", segment="OPT", underlying=f"NIFTY{index}",
            instrument_type="OPTION", expiry=date(2026, 11, 19),
            strike=Decimal("22000"), option_type="CE",
        )
        quote = ProviderQuoteObservation(
            provider="angel_one", contract=contract, generation=1,
            authenticated=True, connection_current=True,
            exchange_timestamp_ns=NOW-index-1, received_monotonic_ns=100+index,
            bid="100", ask="100.1", bid_size=10, ask_size=10, depth_complete=True,
        )
        assert pairer.submit(quote, received_monotonic_ns=100+index, max_age_ns=1_000_000)
    snapshot = pairer.snapshot()
    assert snapshot["pending_quotes"] <= snapshot["pending_capacity"]
    assert snapshot["pending_capacity"] == 16
    worker.stop(drain=False)
    store.close()


def test_pairer_does_not_mark_pair_emitted_when_worker_rejects(tmp_path):
    store, worker, pairer = _pairer(tmp_path, capacity=1)
    worker.submit = lambda envelope: False
    assert pairer.submit(_quote("angel_one"), received_monotonic_ns=110, max_age_ns=1_000_000)
    assert pairer.submit(_quote("shoonya"), received_monotonic_ns=120, max_age_ns=1_000_000) is False
    assert pairer.snapshot()["worker_rejected_pairs"] == 1
    assert pairer.snapshot()["pairs_submitted"] == 0
    worker.stop(drain=False)
    store.close()

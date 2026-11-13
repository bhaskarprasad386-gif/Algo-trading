from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data.provider_contracts import (
    BrokerContractToken, CanonicalContractKey, ContractMapping,
    ProviderContractRegistry,
)


def _key(strike="25000.00"):
    return CanonicalContractKey(
        exchange="NFO", segment="NFO", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 12, 31),
        strike=Decimal(str(strike)), option_type="CE",
    )


def _mapping(broker, token, *, strike="25000", lot=65, tick="0.05", tradable=True):
    key = _key(strike)
    return ContractMapping(
        key=key,
        provider=BrokerContractToken(broker, "NFO", "NFO", token),
        trading_symbol="NIFTY31DEC26C25000",
        lot_size=lot, tick_size=Decimal(tick), tradable=tradable,
    )


def _publish(registry, rows, when=None):
    return registry.replace_snapshot(
        rows, source_version="fixture-v1",
        fetched_at=when or datetime.now(timezone.utc),
    )


def test_same_contract_keeps_independent_broker_tokens_and_normalizes_strike():
    registry = ProviderContractRegistry()
    _publish(registry, [_mapping("ANGELONE", "101"), _mapping("SHOONYA", "NFO|202")])
    key = _key("25000.000")
    assert registry.resolve(key, "angelone").provider.token == "101"
    assert registry.resolve(key, "shoonya").provider.token == "NFO|202"


def test_duplicate_canonical_contract_for_same_broker_is_rejected_atomically():
    registry = ProviderContractRegistry()
    original = _publish(registry, [_mapping("ANGELONE", "101")])
    with pytest.raises(ValueError, match="duplicate broker mapping"):
        _publish(registry, [_mapping("ANGELONE", "101"), _mapping("ANGELONE", "102")],
                 original.fetched_at + timedelta(seconds=1))
    assert registry.snapshot() == original


def test_one_provider_token_cannot_point_to_two_contracts():
    registry = ProviderContractRegistry()
    with pytest.raises(ValueError, match="multiple canonical contracts"):
        _publish(registry, [
            _mapping("ANGELONE", "101"),
            _mapping("ANGELONE", "101", strike="25100"),
        ])
    assert registry.snapshot() is None


@pytest.mark.parametrize("lot,tick", [(75, "0.05"), (65, "0.10")])
def test_cross_broker_lot_or_tick_mismatch_is_rejected(lot, tick):
    registry = ProviderContractRegistry()
    with pytest.raises(ValueError, match="lot/tick metadata mismatch"):
        _publish(registry, [
            _mapping("ANGELONE", "101"),
            _mapping("SHOONYA", "202", lot=lot, tick=tick),
        ])
    assert registry.snapshot() is None


def test_inactive_contract_is_not_resolved_as_tradable():
    registry = ProviderContractRegistry()
    _publish(registry, [_mapping("SHOONYA", "202", tradable=False)])
    with pytest.raises(LookupError, match="no unique"):
        registry.resolve(_key(), "SHOONYA")
    assert registry.resolve(_key(), "SHOONYA", tradable_only=False).provider.token == "202"


def test_empty_or_stale_snapshot_never_replaces_last_known_good():
    registry = ProviderContractRegistry()
    original = _publish(registry, [_mapping("ANGELONE", "101")])
    with pytest.raises(ValueError, match="cannot be empty"):
        _publish(registry, [], original.fetched_at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="stale contract master"):
        _publish(registry, [_mapping("ANGELONE", "999")], original.fetched_at - timedelta(seconds=1))
    assert registry.snapshot() == original


def test_option_identity_requires_expiry_strike_and_side():
    with pytest.raises(ValueError, match="options require"):
        CanonicalContractKey("NFO", "NFO", "NIFTY", "OPTION")


def test_snapshot_sorting_is_stable_for_spot_and_expiring_derivative_contracts():
    from app.market_data.provider_contracts import BrokerContractToken, ContractMapping

    equity_key = CanonicalContractKey("NSE", "NSE", "RELIANCE", "EQUITY")
    equity = ContractMapping(
        equity_key, BrokerContractToken("ANGELONE", "NSE", "NSE", "eq-Case"),
        "RELIANCE", 1, Decimal("0.05"),
    )
    option = _mapping("ANGELONE", "opt-Case")
    registry = ProviderContractRegistry()
    snapshot = _publish(registry, [option, equity])
    assert len(snapshot.mappings) == 2
    assert registry.resolve(equity_key, "ANGELONE").provider.token == "eq-Case"

def test_futures_canonical_identity_requires_expiry():
    with pytest.raises(ValueError, match="futures require expiry"):
        CanonicalContractKey(
            exchange="NFO", segment="NFO", underlying="NIFTY",
            instrument_type="FUTURE",
        )

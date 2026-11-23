from datetime import date
import pytest

from app.market_data.registry import InstrumentDescriptor
from app.market_data.contracts import InstrumentKey
from app.market_data.shared_index_contract_identity import canonical_identity, match_contracts


def desc(broker, token, *, underlying="NIFTY", kind="option", expiry="26NOV2026", strike=25000, right="CE"):
    # Broker-specific token/exchange/segment deliberately differ; canonical fields should match.
    exch = "NFO" if broker == "angel" else "NFO"
    segment = exch
    return InstrumentDescriptor(
        key=InstrumentKey(exch, segment, token), symbol=f"{underlying}-{expiry}-{strike}-{right}",
        instrument_type=kind, exchange=exch, segment=segment, expiry=expiry if kind != "index" else None,
        strike=strike if kind == "option" else None, option_type=right if kind == "option" else None,
        underlying=underlying,
    )


def test_cross_broker_matching_ignores_tokens_but_matches_contract_fields():
    angel = desc("angel", "123456")
    shoonya = desc("shoonya", "991234")
    identity = canonical_identity(angel)
    assert identity.expiry == date(2026, 11, 26)
    assert match_contracts([angel], [shoonya]) == {identity: (angel, shoonya)}


def test_matching_rejects_duplicate_identity_even_with_different_tokens():
    a, b = desc("angel", "1"), desc("angel", "2")
    with pytest.raises(ValueError, match="duplicate canonical"):
        match_contracts([a, b], [desc("shoonya", "3")])


def test_identity_fails_closed_for_unlocked_underlying_and_missing_expiry():
    with pytest.raises(ValueError, match="unlocked"):
        canonical_identity(desc("angel", "1", underlying="FINNIFTY"))
    with pytest.raises(ValueError, match="expiry is required"):
        canonical_identity(desc("angel", "2", expiry=None))


def test_option_right_and_strike_are_part_of_identity():
    call = desc("angel", "1")
    put = desc("shoonya", "2", right="PE")
    high = desc("shoonya", "3", strike=25100)
    assert canonical_identity(call) != canonical_identity(put)
    assert canonical_identity(call) != canonical_identity(high)
    assert match_contracts([call], [put, high]) == {}


def test_futures_match_on_underlying_and_expiry_only():
    f1 = desc("angel", "11", kind="future", expiry="26NOV2026")
    f2 = desc("shoonya", "22", kind="future", expiry="2026-11-26")
    assert match_contracts([f1], [f2]) == {canonical_identity(f1): (f1, f2)}


def test_index_identity_does_not_require_expiry():
    i1 = desc("angel", "111", kind="index", expiry=None)
    i2 = desc("shoonya", "222", kind="index", expiry=None)
    assert match_contracts([i1], [i2]) == {canonical_identity(i1): (i1, i2)}


def test_matching_rejects_duplicate_identity_on_secondary_side():
    primary = [desc("angel", "10")]
    secondary = [desc("shoonya", "20"), desc("shoonya", "21")]
    with pytest.raises(ValueError, match="duplicate canonical"):
        match_contracts(primary, secondary)


def test_contract_pairing_order_is_deterministic():
    primary = [desc("angel", "1", strike=25100), desc("angel", "2", strike=25000)]
    secondary = [desc("shoonya", "3", strike=25000), desc("shoonya", "4", strike=25100)]
    matched = match_contracts(primary, secondary)
    assert list(matched) == sorted(matched)

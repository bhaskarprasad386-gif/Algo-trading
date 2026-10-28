from dataclasses import replace

import pytest

from app.market_data.live_synthetic_stream import SyntheticSubscription
from app.scanner.box_spread_contract_resolver import (
    BoxSpreadContractResolutionError,
    resolve_box_spread_contracts,
)


EXPIRY = "29OCT2026"


def _chain(*, underlying="NIFTY", instrument_class="INDEX", exchange_type=2, lot_size=25):
    return [
        SyntheticSubscription(
            exchange_type, "1001", f"{underlying}{EXPIRY}25000CE",
            underlying, instrument_class, EXPIRY, "CE", 25000, lot_size,
        ),
        SyntheticSubscription(
            exchange_type, "1002", f"{underlying}{EXPIRY}25100CE",
            underlying, instrument_class, EXPIRY, "CE", 25100, lot_size,
        ),
        SyntheticSubscription(
            exchange_type, "1003", f"{underlying}{EXPIRY}25100PE",
            underlying, instrument_class, EXPIRY, "PE", 25100, lot_size,
        ),
        SyntheticSubscription(
            exchange_type, "1004", f"{underlying}{EXPIRY}25000PE",
            underlying, instrument_class, EXPIRY, "PE", 25000, lot_size,
        ),
    ]


def _resolve(subscriptions=None, **overrides):
    args = {
        "subscriptions": _chain() if subscriptions is None else subscriptions,
        "underlying": "NIFTY",
        "instrument_class": "INDEX",
        "expiry": "2026-10-29",
        "low_strike": 25000,
        "high_strike": 25100,
        "direction": "LONG",
        "expected_lot_size": 25,
    }
    args.update(overrides)
    return resolve_box_spread_contracts(**args)


def test_resolves_exact_four_leg_contracts_and_normalises_expiry():
    resolved = _resolve()

    assert resolved.expiry == "2026-10-29"
    assert resolved.exchange_segment == "NFO"
    assert resolved.direction == "LONG"
    assert resolved.lot_size == 25
    assert [leg.role for leg in resolved.legs] == [
        "LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"
    ]
    assert [leg.token for leg in resolved.legs] == ["1001", "1002", "1003", "1004"]
    assert [leg.symbol[-2:] for leg in resolved.legs] == ["CE", "CE", "PE", "PE"]


def test_accepts_short_direction_without_changing_contract_identity():
    resolved = _resolve(direction="short")
    assert resolved.direction == "SHORT"
    assert [leg.token for leg in resolved.legs] == ["1001", "1002", "1003", "1004"]


def test_ignores_unrelated_future_subscription_without_option_expiry():
    subscriptions = _chain()
    subscriptions.append(
        SyntheticSubscription(2, "future-token", "NIFTYFUT", "NIFTY", "INDEX")
    )
    assert _resolve(subscriptions).lot_size == 25


def test_rejects_missing_option_leg():
    with pytest.raises(BoxSpreadContractResolutionError, match="missing exact contract"):
        _resolve(_chain()[:-1])


def test_rejects_duplicate_subscription_for_required_leg():
    subscriptions = _chain()
    subscriptions.append(subscriptions[0])
    with pytest.raises(BoxSpreadContractResolutionError, match="ambiguous duplicate"):
        _resolve(subscriptions)


def test_rejects_wrong_exchange_segment():
    subscriptions = _chain()
    subscriptions[2] = replace(subscriptions[2], exchange_type=4)
    with pytest.raises(BoxSpreadContractResolutionError, match="exchange mismatch"):
        _resolve(subscriptions)


def test_rejects_duplicate_broker_token_identity():
    subscriptions = _chain()
    subscriptions[3] = replace(subscriptions[3], token=subscriptions[0].token)
    with pytest.raises(BoxSpreadContractResolutionError, match="duplicate broker contract identity"):
        _resolve(subscriptions)


def test_rejects_duplicate_tradingsymbol():
    subscriptions = _chain()
    subscriptions[3] = replace(subscriptions[3], symbol=subscriptions[0].symbol)
    with pytest.raises(BoxSpreadContractResolutionError, match="duplicate tradingsymbol"):
        _resolve(subscriptions)


def test_rejects_missing_token_and_never_guesses_it():
    subscriptions = _chain()
    subscriptions[0] = replace(subscriptions[0], token="")
    with pytest.raises(BoxSpreadContractResolutionError, match="no exact broker token"):
        _resolve(subscriptions)


def test_rejects_symbol_option_type_mismatch():
    subscriptions = _chain()
    subscriptions[0] = replace(subscriptions[0], symbol="NIFTY29OCT202625000PE")
    with pytest.raises(BoxSpreadContractResolutionError, match="does not match option type"):
        _resolve(subscriptions)


def test_rejects_different_lot_sizes_across_legs():
    subscriptions = _chain()
    subscriptions[2] = replace(subscriptions[2], lot_size=50)
    with pytest.raises(BoxSpreadContractResolutionError, match="same lot size"):
        _resolve(subscriptions, expected_lot_size=None)


def test_rejects_opportunity_lot_size_mismatch():
    with pytest.raises(BoxSpreadContractResolutionError, match="opportunity lot size mismatch"):
        _resolve(expected_lot_size=50)


def test_rejects_invalid_direction_and_strike_order():
    with pytest.raises(BoxSpreadContractResolutionError, match="direction must be"):
        _resolve(direction="BUY")
    with pytest.raises(BoxSpreadContractResolutionError, match="low_strike must be below"):
        _resolve(low_strike=25100, high_strike=25000)


def test_uses_bfo_for_sensex_index_contracts():
    subscriptions = [
        replace(item, exchange_type=4, underlying="SENSEX",
                symbol=item.symbol.replace("NIFTY", "SENSEX"))
        for item in _chain()
    ]
    resolved = _resolve(
        subscriptions,
        underlying="SENSEX",
        low_strike=25000,
        high_strike=25100,
    )
    assert resolved.exchange_segment == "BFO"
    assert all(leg.exchange_type == 4 for leg in resolved.legs)



@pytest.mark.parametrize("exchange_type", [2.5, 2.0, True, None, "2.5", ""])
def test_rejects_malformed_exchange_type_without_coercion(exchange_type):
    subscriptions = _chain()
    subscriptions[0] = replace(subscriptions[0], exchange_type=exchange_type)
    with pytest.raises(BoxSpreadContractResolutionError, match="invalid exchange type"):
        _resolve(subscriptions)


@pytest.mark.parametrize("exchange_type", ["2", " 2 "])
def test_accepts_canonical_integer_string_exchange_type(exchange_type):
    subscriptions = _chain()
    subscriptions[0] = replace(subscriptions[0], exchange_type=exchange_type)
    resolved = _resolve(subscriptions)
    assert resolved.low_ce.exchange_type == 2

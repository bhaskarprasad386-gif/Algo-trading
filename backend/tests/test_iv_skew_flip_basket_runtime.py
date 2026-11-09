import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.scanner.iv_skew_flip.basket_runtime import load_verified_runtime_baskets


class FakeMaster:
    def resolve_cash_instrument(self, symbol, exchange):
        assert exchange == "NSE"
        return {"token": {"AAA": "101", "BBB": "102", "CCC": "201", "DDD": "202"}.get(symbol, "")}


def record(symbol, components, tokens, *, effective=date(2026, 11, 7), record_id=1):
    return SimpleNamespace(
        id=record_id,
        underlying=symbol,
        effective_date=datetime.combine(effective, datetime.min.time(), tzinfo=timezone.utc),
        created_at=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc),
        source_name="NSE Indices",
        source_url="https://www.niftyindices.com/index-constituent",
        source_sha256="a" * 64,
        file_format="CSV",
        weight_sum=sum(item["weight"] for item in components),
        components_json=json.dumps(components),
        token_mapping_json=json.dumps(tokens),
        roster_source_name="NSE Indices",
        roster_source_url="https://www.niftyindices.com/index-roster",
        roster_sha256="b" * 64,
        status="VALID",
    )


def test_loads_both_current_verified_index_baskets():
    rows = [
        record("NIFTY", [{"symbol": "AAA", "weight": 60.0}, {"symbol": "BBB", "weight": 40.0}],
               {"AAA": "101", "BBB": "102"}, record_id=1),
        record("BANKNIFTY", [{"symbol": "CCC", "weight": 70.0}, {"symbol": "DDD", "weight": 30.0}],
               {"CCC": "201", "DDD": "202"}, record_id=2),
    ]

    baskets = load_verified_runtime_baskets(rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster())

    assert set(baskets) == {"NIFTY", "BANKNIFTY"}
    assert [(item.symbol, item.weight) for item in baskets["NIFTY"]] == [("AAA", 60.0), ("BBB", 40.0)]
    assert [(item.symbol, item.weight) for item in baskets["BANKNIFTY"]] == [("CCC", 70.0), ("DDD", 30.0)]


def test_missing_one_index_fails_closed():
    rows = [
        record("NIFTY", [{"symbol": "AAA", "weight": 100.0}], {"AAA": "101"}),
    ]

    with pytest.raises(RuntimeError, match="verified index baskets are required"):
        load_verified_runtime_baskets(rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster())


def test_stale_or_incomplete_registry_fails_closed():
    rows = [
        record("NIFTY", [{"symbol": "AAA", "weight": 100.0}], {"AAA": "101"},
               effective=date(2026, 9, 1)),
        record("BANKNIFTY", [{"symbol": "BBB", "weight": 100.0}], {"BBB": "201"}),
    ]

    with pytest.raises(RuntimeError, match="NIFTY"):
        load_verified_runtime_baskets(rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster())


def test_invalid_token_mapping_is_rejected_even_after_registry_lookup():
    rows = [
        record("NIFTY", [{"symbol": "AAA", "weight": 100.0}], {"AAA": "not-a-token"}),
        record("BANKNIFTY", [{"symbol": "BBB", "weight": 100.0}], {"BBB": "201"}),
    ]

    with pytest.raises(RuntimeError, match="NIFTY"):
        load_verified_runtime_baskets(rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster())



def test_current_instrument_master_token_mismatch_fails_closed():
    rows = [
        record("NIFTY", [{"symbol": "AAA", "weight": 100.0}], {"AAA": "999"}),
        record("BANKNIFTY", [{"symbol": "BBB", "weight": 100.0}], {"BBB": "102"}),
    ]

    with pytest.raises(RuntimeError, match="stale for AAA"):
        load_verified_runtime_baskets(rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster())



def test_nifty_pressure_can_select_dynamic_top_two_after_full_basket_verification():
    components = [
        {"symbol": "AAA", "weight": 10.0},
        {"symbol": "BBB", "weight": 35.0},
        {"symbol": "CCC", "weight": 25.0},
        {"symbol": "DDD", "weight": 30.0},
    ]
    rows = [record("NIFTY", components, {
        "AAA": "101", "BBB": "102", "CCC": "201", "DDD": "202",
    })]

    baskets = load_verified_runtime_baskets(
        rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster(),
        underlyings=("NIFTY",), top_n=2,
    )

    assert set(baskets) == {"NIFTY"}
    assert [(item.symbol, item.weight) for item in baskets["NIFTY"]] == [
        ("BBB", 35.0), ("DDD", 30.0),
    ]


def test_top_n_rejects_invalid_values():
    rows = [record("NIFTY", [{"symbol": "AAA", "weight": 100.0}], {"AAA": "101"})]
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="top_n"):
            load_verified_runtime_baskets(
                rows, as_of=date(2026, 11, 8), instrument_master=FakeMaster(),
                underlyings=("NIFTY",), top_n=invalid,
            )



def test_top_ten_selection_applies_independently_to_nifty_and_banknifty():
    nifty_symbols = [f"N{i:02d}" for i in range(12)]
    bank_symbols = [f"B{i:02d}" for i in range(12)]
    # Weights sum to 100; the two smallest constituents fall outside top ten.
    weights = [18, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 4]
    tokens = {
        symbol: str(1000 + index)
        for index, symbol in enumerate(nifty_symbols + bank_symbols)
    }

    class Master:
        def resolve_cash_instrument(self, symbol, exchange):
            assert exchange == "NSE"
            return {"token": tokens[symbol]}

    records = [
        record("NIFTY", [
            {"symbol": symbol, "weight": weight}
            for symbol, weight in zip(nifty_symbols, weights)
        ], {symbol: tokens[symbol] for symbol in nifty_symbols}),
        record("BANKNIFTY", [
            {"symbol": symbol, "weight": weight}
            for symbol, weight in zip(bank_symbols, weights)
        ], {symbol: tokens[symbol] for symbol in bank_symbols}, record_id=2),
    ]

    baskets = load_verified_runtime_baskets(
        records,
        as_of=date(2026, 11, 8),
        instrument_master=Master(),
        underlyings=("NIFTY", "BANKNIFTY"),
        top_n=10,
    )

    assert set(baskets) == {"NIFTY", "BANKNIFTY"}
    assert len(baskets["NIFTY"]) == 10
    assert len(baskets["BANKNIFTY"]) == 10
    assert [item.symbol for item in baskets["NIFTY"]] == nifty_symbols[:10]
    assert [item.symbol for item in baskets["BANKNIFTY"]] == bank_symbols[:10]

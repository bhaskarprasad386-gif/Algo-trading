from app.market_data.shared_index_derived_iv import implied_volatility, SharedIndexDerivedIV


def test_local_iv_inversion_is_disabled():
    assert implied_volatility(220, 22000, 22000, "2026-11-26", "CE") is None
    assert implied_volatility(215, 22000, 22000, "2026-11-26", "PE") is None


def test_shared_index_snapshot_keeps_iv_and_velocity_fields_null():
    calc = SharedIndexDerivedIV()
    row = {
        "exchange": "NFO", "segment": "NFO", "token": "123",
        "instrument_type": "option", "underlying": "NIFTY",
        "strike": 22000, "expiry": "2026-11-27", "option_type": "CE",
        "ltp": 220, "fresh": True, "timestamp_ns": 1_800_000_000_000_000_000,
    }
    first = calc.decorate([row], {"NIFTY": 22000})[0]
    assert first["iv_percent"] is None
    assert first["iv_source"] is None
    assert first["iv_delta_1s"] is None
    later = dict(row, timestamp_ns=row["timestamp_ns"] + 1_100_000_000, ltp=225)
    second = calc.decorate([later], {"NIFTY": 22000})[0]
    assert second["iv_percent"] is None
    assert second["iv_delta_1s"] is None

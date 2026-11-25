from app.market_data.shared_index_spot_resolver import resolve_locked_index_spot_descriptors


class FakeMaster:
    def __init__(self, rows):
        self.rows = rows

    def resolve_index_instrument(self, symbol, exchange):
        assert exchange == "NSE"
        return self.rows[symbol]


def test_locked_spot_resolver_returns_only_two_nse_indices():
    master = FakeMaster({
        "NIFTY": {"token": "9991001", "symbol": "NIFTY"},
        "BANKNIFTY": {"token": "9991002", "symbol": "BANKNIFTY"},
    })
    descriptors = resolve_locked_index_spot_descriptors(master)
    assert len(descriptors) == 2
    assert [d.underlying for d in descriptors] == ["NIFTY", "BANKNIFTY"]
    assert [d.symbol for d in descriptors] == ["NIFTY", "BANKNIFTY"]
    assert [d.key.exchange for d in descriptors] == ["NSE", "NSE"]
    assert [d.key.segment for d in descriptors] == ["NSE", "NSE"]
    assert [d.key.token for d in descriptors] == ["9991001", "9991002"]
    assert all(d.instrument_type == "index" for d in descriptors)


def test_locked_spot_resolver_canonicalizes_empty_or_provider_specific_symbols():
    master = FakeMaster({
        "NIFTY": {"token": "9991001", "symbol": ""},
        "BANKNIFTY": {"token": "9991002", "symbol": "BANK NIFTY", "name": "BANKNIFTY"},
    })
    descriptors = resolve_locked_index_spot_descriptors(master)
    assert [d.symbol for d in descriptors] == ["NIFTY", "BANKNIFTY"]
    assert [d.underlying for d in descriptors] == ["NIFTY", "BANKNIFTY"]


def test_locked_spot_resolver_rejects_duplicate_tokens():
    master = FakeMaster({
        "NIFTY": {"token": "9991001", "symbol": "NIFTY"},
        "BANKNIFTY": {"token": "9991001", "symbol": "BANKNIFTY"},
    })
    try:
        resolve_locked_index_spot_descriptors(master)
    except LookupError as exc:
        assert "duplicate index spot token" in str(exc)
    else:
        raise AssertionError("duplicate spot token should fail closed")


def test_locked_spot_resolver_rejects_missing_token():
    master = FakeMaster({
        "NIFTY": {"token": "9991001", "symbol": "NIFTY"},
        "BANKNIFTY": {"token": "", "symbol": "BANKNIFTY"},
    })
    try:
        resolve_locked_index_spot_descriptors(master)
    except LookupError as exc:
        assert "invalid or duplicate index spot token" in str(exc)
    else:
        raise AssertionError("missing index token must fail closed")

def test_real_instrument_master_rows_resolve_canonical_shared_spot_descriptors():
    # Exercise the production InstrumentMaster resolver against provider-shaped
    # Angel One rows, without invoking network download or process-wide cache.
    from app.market_data.instruments import InstrumentMaster

    master = InstrumentMaster()
    master.instruments = [
        {
            "exch_seg": "NSE",
            "instrumenttype": "AMXIDX",
            "token": "9991001",
            "symbol": "",
            "name": "NIFTY",
        },
        {
            "exch_seg": "NSE",
            "instrumenttype": "AMXIDX",
            "token": "9991002",
            "symbol": "BANK NIFTY",
            "name": "BANKNIFTY",
        },
    ]
    master._loaded = True

    descriptors = resolve_locked_index_spot_descriptors(master)

    assert len(descriptors) == 2
    assert [(d.underlying, d.symbol) for d in descriptors] == [
        ("NIFTY", "NIFTY"),
        ("BANKNIFTY", "BANKNIFTY"),
    ]
    assert [d.key.token for d in descriptors] == ["9991001", "9991002"]
    assert all(d.key.exchange == "NSE" for d in descriptors)
    assert all(d.key.segment == "NSE" for d in descriptors)
    assert all(d.instrument_type == "index" for d in descriptors)


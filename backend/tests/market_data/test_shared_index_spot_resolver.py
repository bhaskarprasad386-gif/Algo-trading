from app.market_data.shared_index_spot_resolver import resolve_locked_index_spot_descriptors


class FakeMaster:
    def __init__(self, rows):
        self.rows = rows

    def resolve_index_instrument(self, symbol, exchange):
        return self.rows[symbol]


def test_locked_spot_resolver_returns_only_two_nse_indices():
    master = FakeMaster({
        "NIFTY": {"token": "9991001", "symbol": "NIFTY"},
        "BANKNIFTY": {"token": "9991002", "symbol": "BANKNIFTY"},
    })
    descriptors = resolve_locked_index_spot_descriptors(master)
    assert len(descriptors) == 2
    assert [d.underlying for d in descriptors] == ["NIFTY", "BANKNIFTY"]
    assert [d.key.exchange for d in descriptors] == ["NSE", "NSE"]
    assert [d.key.token for d in descriptors] == ["9991001", "9991002"]
    assert all(d.instrument_type == "index" for d in descriptors)


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

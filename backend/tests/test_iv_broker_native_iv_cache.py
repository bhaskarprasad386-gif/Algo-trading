from types import SimpleNamespace

from app.scanner.iv_skew_flip.broker_iv import AngelOneBrokerIVCache
from app.scanner.iv_skew_flip.universe import ResolvedOptionContract, ResolvedOptionUniverse


class FakeGreeksClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def option_greeks(self, underlying, expiry):
        self.calls.append((underlying, expiry))
        return {"status": True, "data": self.rows}


def make_universe():
    contracts = tuple(
        ResolvedOptionContract(
            symbol=f"NIFTY-{strike}-{side}", token=f"{strike}-{side}",
            exchange="NFO", expiry="2026-11-26", strike=float(strike),
            option_type=side, lot_size=75,
        )
        for strike in (26000, 26050, 26100)
        for side in ("CE", "PE")
    )
    return ResolvedOptionUniverse(
        underlying="NIFTY", expiry_type="WEEKLY", expiry="2026-11-26",
        spot_exchange="NSE", option_exchange="NFO", strike_step=50.0,
        atm_strike=26050.0, strikes=(26000.0, 26050.0, 26100.0),
        contracts=contracts,
    )


def greek_rows(universe):
    return [
        {
            "expiry": "26NOV2026", "strikePrice": str(item.strike),
            "optionType": item.option_type, "impliedVolatility": "14.25",
        }
        for item in universe.contracts
    ]


def live_quotes(universe, timestamp):
    from app.scanner.iv_skew_flip.engine import OptionQuote
    return tuple(
        OptionQuote(
            underlying=universe.underlying, expiry=universe.expiry,
            strike=item.strike, option_type=item.option_type,
            timestamp_ns=timestamp, ltp=100.0, token=item.token,
        )
        for item in universe.contracts
    )


def test_broker_iv_cache_joins_exact_expiry_strike_side_and_token():
    now = 1_000_000_000_000
    universe = make_universe()
    client = FakeGreeksClient(greek_rows(universe))
    cache = AngelOneBrokerIVCache(client=client, clock_ns=lambda: now)
    cache.set_universe("NIFTY_WEEKLY", universe)
    cache._fetch("NIFTY_WEEKLY", universe)

    enriched = cache.enrich("NIFTY_WEEKLY", live_quotes(universe, now), now)
    assert enriched is not None
    assert all(item.broker_iv == 0.1425 for item in enriched)
    assert all(item.iv_source == "ANGEL_ONE" for item in enriched)
    assert all(item.iv_timestamp_ns == now for item in enriched)
    assert client.calls == [("NIFTY", "26NOV2026")]


def test_broker_iv_cache_rejects_wrong_expiry_duplicate_and_missing_iv():
    now = 1_000_000_000_000
    universe = make_universe()
    rows = greek_rows(universe)
    rows[0]["expiry"] = "03DEC2026"
    rows[1]["impliedVolatility"] = "0"
    rows.append(dict(greek_rows(universe)[2]))
    cache = AngelOneBrokerIVCache(client=FakeGreeksClient(rows), clock_ns=lambda: now)
    cache.set_universe("NIFTY_WEEKLY", universe)
    cache._fetch("NIFTY_WEEKLY", universe)
    assert cache.enrich("NIFTY_WEEKLY", live_quotes(universe, now), now) is None
    assert cache.snapshot()["chains"]["NIFTY_WEEKLY"]["status"] == "PARTIAL"


def test_broker_iv_cache_never_uses_stale_or_future_capture():
    now = 1_000_000_000_000
    universe = make_universe()
    cache = AngelOneBrokerIVCache(
        client=FakeGreeksClient(greek_rows(universe)),
        clock_ns=lambda: now, max_age_seconds=60.0,
    )
    cache.set_universe("NIFTY_WEEKLY", universe)
    cache._fetch("NIFTY_WEEKLY", universe)
    quotes = live_quotes(universe, now)
    assert cache.enrich("NIFTY_WEEKLY", quotes, now + 61_000_000_000) is None
    assert cache.enrich("NIFTY_WEEKLY", quotes, now - 1) is None


def test_broker_iv_cache_expiry_rotation_drops_old_contracts():
    now = 1_000_000_000_000
    old = make_universe()
    cache = AngelOneBrokerIVCache(client=FakeGreeksClient(greek_rows(old)), clock_ns=lambda: now)
    cache.set_universe("NIFTY_WEEKLY", old)
    cache._fetch("NIFTY_WEEKLY", old)
    new = ResolvedOptionUniverse(
        underlying="NIFTY", expiry_type="WEEKLY", expiry="2026-12-03",
        spot_exchange="NSE", option_exchange="NFO", strike_step=50.0,
        atm_strike=26050.0, strikes=old.strikes,
        contracts=tuple(
            ResolvedOptionContract(
                symbol=item.symbol, token=item.token, exchange="NFO",
                expiry="2026-12-03", strike=item.strike,
                option_type=item.option_type, lot_size=item.lot_size,
            ) for item in old.contracts
        ),
    )
    cache.set_universe("NIFTY_WEEKLY", new)
    assert cache.enrich("NIFTY_WEEKLY", live_quotes(old, now), now) is None

from dataclasses import dataclass

from app.scanner.iv_skew_flip.alerts.aggregation import SourceMinuteAggregator, NS_PER_MINUTE


@dataclass
class Sample:
    token: str = "123"
    underlying: str = "NIFTY"
    expiry: str = "2026-11-19"
    strike: float = 25000
    option_type: str = "CE"
    sampled_at_ns: int = 0
    source_timestamp_ns: int = 0
    spot_timestamp_ns: int = 0
    spot_price: float = 25000
    iv: float = 12
    iv_input: str = "BID_ASK_MIDPOINT"
    bid: float = 99
    ask: float = 101
    ltp: float = 100
    oi: int = 1000
    oi_timestamp_ns: int = 0
    volume: int = 100


@dataclass
class Spot:
    price: float
    timestamp_ns: int


def test_only_distinct_source_ticks_contribute_and_partial_minute_is_hidden():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    sample = Sample(sampled_at_ns=base, source_timestamp_ns=base+1,
                    spot_timestamp_ns=base+1, oi_timestamp_ns=base+1)
    agg.ingest([sample, sample], Spot(25000, base+1), now_ns=base+30_000_000_000)
    assert agg.option_history("123", now_ns=base+30_000_000_000) == ()
    assert agg.spot_bars(now_ns=base+30_000_000_000) == ()


def test_completed_minute_bucket_emits_one_source_observation():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    sample = Sample(sampled_at_ns=base, source_timestamp_ns=base+1,
                    spot_timestamp_ns=base+1, oi_timestamp_ns=base+1)
    agg.ingest([sample], Spot(25000, base+1), now_ns=base+NS_PER_MINUTE+1)
    rows = agg.option_history("123", now_ns=base+NS_PER_MINUTE+1)
    bars = agg.spot_bars(now_ns=base+NS_PER_MINUTE+1)
    assert len(rows) == 1
    assert len(bars) == 1
    assert bars[0].open == bars[0].high == bars[0].low == bars[0].close == 25000


def test_repeated_spot_source_timestamp_does_not_change_bar():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    agg.ingest([], Spot(25000, base+1), now_ns=base+10_000_000_000)
    agg.ingest([], Spot(25100, base+1), now_ns=base+20_000_000_000)
    # Still a partial bucket; no completed bar can be emitted.
    assert agg.spot_bars(now_ns=base+30_000_000_000) == ()

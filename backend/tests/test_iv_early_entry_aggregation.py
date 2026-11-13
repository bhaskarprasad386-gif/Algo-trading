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


def test_source_observations_are_retained_even_while_spot_minute_is_partial():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    sample = Sample(sampled_at_ns=base, source_timestamp_ns=base+1,
                    spot_timestamp_ns=base+1, oi_timestamp_ns=base+1)
    agg.ingest([sample, sample], Spot(25000, base+1), now_ns=base+30_000_000_000)
    rows = agg.option_history("123", now_ns=base+30_000_000_000)
    assert len(rows) == 1
    assert rows[0].source_timestamp_ns == base+1
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


def test_distinct_option_changes_in_same_minute_are_both_preserved():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    first = Sample(sampled_at_ns=base, source_timestamp_ns=base+1,
                   spot_timestamp_ns=base+1, oi_timestamp_ns=base+1, iv=12.0)
    second = Sample(sampled_at_ns=base+10_000_000_000,
                    source_timestamp_ns=base+10_000_000_000,
                    spot_timestamp_ns=base+10_000_000_000,
                    oi_timestamp_ns=base+10_000_000_000, iv=12.4)
    agg.ingest([first], Spot(25000, base+1), now_ns=base+11_000_000_000)
    agg.ingest([second], Spot(25001, base+10_000_000_000), now_ns=base+12_000_000_000)
    rows = agg.option_history("123", now_ns=base+12_000_000_000)
    assert [row.iv for row in rows] == [12.0, 12.4]
    assert [row.source_timestamp_ns for row in rows] == [base+1, base+10_000_000_000]


def test_oi_change_is_preserved_when_price_quote_timestamp_is_unchanged():
    agg = SourceMinuteAggregator()
    base = 1_800_000_000_000_000_000
    first = Sample(sampled_at_ns=base, source_timestamp_ns=base+1,
                   spot_timestamp_ns=base+1, oi_timestamp_ns=base+1, oi=1000)
    oi_update = Sample(sampled_at_ns=base+5_000_000_000,
                       source_timestamp_ns=base+1,
                       spot_timestamp_ns=base+1,
                       oi_timestamp_ns=base+5_000_000_000, oi=900)
    agg.ingest([first], None, now_ns=base+6_000_000_000)
    agg.ingest([oi_update], None, now_ns=base+7_000_000_000)
    rows = agg.option_history("123", now_ns=base+7_000_000_000)
    assert len(rows) == 2
    assert [row.oi for row in rows] == [1000, 900]
    assert [row.source_timestamp_ns for row in rows] == [base+1, base+1]
    assert rows[1].oi_timestamp_ns > rows[0].oi_timestamp_ns

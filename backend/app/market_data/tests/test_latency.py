from app.market_data.latency import LatencySample


def test_latency_sample_converts_nanoseconds_to_milliseconds():
    sample = LatencySample(started_ns=1_000_000, finished_ns=16_000_000)
    assert sample.milliseconds == 15.0

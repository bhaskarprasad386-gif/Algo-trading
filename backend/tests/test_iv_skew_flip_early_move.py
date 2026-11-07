from types import SimpleNamespace

from app.scanner.iv_skew_flip.early_move import IVEarlyMoveDetector


def test_early_move_detector_fails_closed_without_fresh_new_ticks():
    detector = IVEarlyMoveDetector()
    result = detector.update(
        quotes=(), metrics=(), spot_price=24000.0, now_ns=100_000_000_000,
    )
    assert result["state"] == "WATCH"
    assert result["signals_allowed"] is False


def test_early_move_detector_does_not_double_count_repeated_volume_ticks():
    detector = IVEarlyMoveDetector()
    quote = SimpleNamespace(
        token="ce", timestamp_ns=100_000_000_000, volume=100,
        option_type="CE", mark=20.0, bid=19.9, ask=20.1,
    )
    metric = {"token": "ce", "iv": 18.0, "iv_input": "BID_ASK_MIDPOINT", "source_timestamp_ns": 100_000_000_000, "spot_timestamp_ns": 100_000_000_000}
    detector.update(quotes=(quote,), metrics=(metric,), spot_price=24000, now_ns=100_000_000_000)
    first = detector.update(quotes=(quote,), metrics=(metric,), spot_price=24000, now_ns=101_000_000_000)
    assert first["signals_allowed"] is False
    assert first["volume_ratio"] is None


def test_early_move_detector_requires_volume_iv_premium_and_spot_confluence():
    detector = IVEarlyMoveDetector()
    base = 100_000_000_000
    # Warm the rolling buckets with modest cumulative volume.
    for i in range(1, 25):
        ts = base + i * 10_000_000_000
        quotes = (
            SimpleNamespace(token="ce1", timestamp_ns=ts, volume=100 + i * 10, option_type="CE", mark=20.0 + i * .01, bid=19.9 + i * .01, ask=20.1 + i * .01),
            SimpleNamespace(token="ce2", timestamp_ns=ts, volume=200 + i * 10, option_type="CE", mark=25.0 + i * .01, bid=24.9 + i * .01, ask=25.1 + i * .01),
        )
        metrics = (
            {"token": "ce1", "iv": 18.0 + i * .01, "iv_input": "BID_ASK_MIDPOINT", "source_timestamp_ns": ts, "spot_timestamp_ns": ts},
            {"token": "ce2", "iv": 19.0 + i * .01, "iv_input": "BID_ASK_MIDPOINT", "source_timestamp_ns": ts, "spot_timestamp_ns": ts},
        )
        detector.update(quotes=quotes, metrics=metrics, spot_price=24000 + i * .01, now_ns=ts)
    # A sharp new burst across two contracts.
    ts = base + 250_000_000_000
    quotes = (
        SimpleNamespace(token="ce1", timestamp_ns=ts, volume=1000, option_type="CE", mark=22.0, bid=21.9, ask=22.1),
        SimpleNamespace(token="ce2", timestamp_ns=ts, volume=1100, option_type="CE", mark=27.0, bid=26.9, ask=27.1),
    )
    metrics = ({"token": "ce1", "iv": 20.0, "iv_input": "BID_ASK_MIDPOINT", "source_timestamp_ns": ts, "spot_timestamp_ns": ts}, {"token": "ce2", "iv": 21.0, "iv_input": "BID_ASK_MIDPOINT", "source_timestamp_ns": ts, "spot_timestamp_ns": ts})
    result = detector.update(quotes=quotes, metrics=metrics, spot_price=24010, now_ns=ts)
    assert result["direction"] == "BULLISH"
    assert result["volume_ratio"] >= 4
    assert result["signals_allowed"] is True
    assert result["state"] in {"PRE_MOVE", "CONFIRMED"}

    # Repeated runtime refreshes must not erase a valid alert while the
    # original source quotes are still within the five-second freshness gate.
    held = detector.update(
        quotes=quotes,
        metrics=metrics,
        spot_price=24010,
        now_ns=ts + 1_000_000_000,
        spot_timestamp_ns=ts,
    )
    assert held["state"] == result["state"]
    assert held["direction"] == result["direction"]
    assert held["signals_allowed"] is True
    assert "holding recent alert" in held["reason"]



def test_early_move_detector_rejects_iv_from_a_different_option_tick():
    detector = IVEarlyMoveDetector()
    ts = 200_000_000_000
    quote = SimpleNamespace(
        token="ce", timestamp_ns=ts, volume=100,
        option_type="CE", mark=20.0, bid=19.9, ask=20.1,
    )
    stale_metric = {
        "token": "ce", "iv": 18.5, "iv_input": "BID_ASK_MIDPOINT",
        "source_timestamp_ns": ts - 1_000_000_000,
        "spot_timestamp_ns": ts,
    }
    result = detector.update(
        quotes=(quote,), metrics=(stale_metric,), spot_price=24000,
        now_ns=ts, spot_timestamp_ns=ts,
    )
    assert result["signals_allowed"] is False
    assert result["contracts_confirming"] == 0



def test_early_move_volume_baseline_counts_empty_buckets_as_zero():
    detector = IVEarlyMoveDetector()
    base = 300_000_000_000
    volume = 100
    mark = 20.0
    iv = 18.0
    # One small volume increment every 20 seconds leaves every alternate
    # 10-second bucket empty. Empty buckets must lower the true time baseline.
    for step in range(13):
        ts = base + step * 20_000_000_000
        if step:
            volume += 10
            mark += 0.01
            iv += 0.01
        quote = SimpleNamespace(
            token="sparse-ce", timestamp_ns=ts, volume=volume,
            option_type="CE", mark=mark, bid=mark - 0.1, ask=mark + 0.1,
        )
        metric = {
            "token": "sparse-ce", "iv": iv,
            "iv_input": "BID_ASK_MIDPOINT",
            "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
        }
        detector.update(
            quotes=(quote,), metrics=(metric,), spot_price=24000 + step,
            now_ns=ts, spot_timestamp_ns=ts,
        )

    ts = base + 13 * 20_000_000_000
    volume += 100
    mark += 1.0
    iv += 0.2
    quote = SimpleNamespace(
        token="sparse-ce", timestamp_ns=ts, volume=volume,
        option_type="CE", mark=mark, bid=mark - 0.1, ask=mark + 0.1,
    )
    metric = {
        "token": "sparse-ce", "iv": iv,
        "iv_input": "BID_ASK_MIDPOINT",
        "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
    }
    result = detector.update(
        quotes=(quote,), metrics=(metric,), spot_price=24020,
        now_ns=ts, spot_timestamp_ns=ts,
    )
    # 10 active buckets over the 20-bucket lookback => baseline 5,
    # rather than 10 if empty buckets were incorrectly omitted.
    assert result["volume_ratio"] == 20.0



def test_early_move_does_not_assign_cross_bucket_volume_delta_to_new_bucket():
    detector = IVEarlyMoveDetector()
    first_ts = IVEarlyMoveDetector.BUCKET_NS - 1
    crossed_ts = IVEarlyMoveDetector.BUCKET_NS + 1
    same_bucket_ts = IVEarlyMoveDetector.BUCKET_NS + 2

    def update(ts, volume, mark, iv):
        quote = SimpleNamespace(
            token="boundary-ce", timestamp_ns=ts, volume=volume,
            option_type="CE", mark=mark, bid=mark - 0.1, ask=mark + 0.1,
        )
        metric = {
            "token": "boundary-ce", "iv": iv,
            "iv_input": "BID_ASK_MIDPOINT",
            "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
        }
        return detector.update(
            quotes=(quote,), metrics=(metric,), spot_price=24000,
            now_ns=ts, spot_timestamp_ns=ts,
        )

    update(first_ts, 100, 20.0, 18.0)
    crossed = update(crossed_ts, 1000, 21.0, 18.5)
    new_bucket = crossed_ts // IVEarlyMoveDetector.BUCKET_NS

    # The 900-contract cumulative delta spans the boundary and is not
    # credited to the new bucket or used to create a volume-ratio spike.
    assert detector._volume_buckets["boundary-ce"].get(new_bucket, 0) == 0
    assert crossed["volume_ratio"] is None

    update(same_bucket_ts, 1010, 21.1, 18.6)
    # Only the subsequent 10-contract same-bucket increment is attributable.
    assert detector._volume_buckets["boundary-ce"][new_bucket] == 10

def test_early_move_spot_move_alone_cannot_confirm_without_multi_contract_pressure():
    detector = IVEarlyMoveDetector()
    base = 500_000_000_000
    volume = 100
    mark = 20.0
    iv = 18.0

    # Build at least 12 prior time buckets with one active contract.
    for step in range(1, 15):
        ts = base + step * IVEarlyMoveDetector.BUCKET_NS
        volume += 10
        mark += 0.01
        iv += 0.01
        quote = SimpleNamespace(
            token="single-ce", timestamp_ns=ts, volume=volume,
            option_type="CE", mark=mark, bid=mark - 0.1, ask=mark + 0.1,
        )
        metric = {
            "token": "single-ce", "iv": iv,
            "iv_input": "BID_ASK_MIDPOINT",
            "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
        }
        detector.update(
            quotes=(quote,), metrics=(metric,), spot_price=24000 + step * 10,
            now_ns=ts, spot_timestamp_ns=ts,
        )

    # One contract has a large volume/premium/IV jump, while spot has already
    # moved >0.15% over 60s. This is not enough for multi-contract confirmation.
    ts = base + 15 * IVEarlyMoveDetector.BUCKET_NS
    volume += 100
    mark += 1.0
    iv += 0.2
    quote = SimpleNamespace(
        token="single-ce", timestamp_ns=ts, volume=volume,
        option_type="CE", mark=mark, bid=mark - 0.1, ask=mark + 0.1,
    )
    metric = {
        "token": "single-ce", "iv": iv,
        "iv_input": "BID_ASK_MIDPOINT",
        "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
    }
    result = detector.update(
        quotes=(quote,), metrics=(metric,), spot_price=24150,
        now_ns=ts, spot_timestamp_ns=ts,
    )

    assert result["contracts_confirming"] == 1
    assert result["state"] != "CONFIRMED"
    assert result["signals_allowed"] is False



def test_early_move_flags_option_pressure_before_spot_move_as_pre_move():
    detector = IVEarlyMoveDetector()
    base = 700_000_000_000
    volumes = {"ce1": 100, "ce2": 200}
    marks = {"ce1": 20.0, "ce2": 25.0}
    ivs = {"ce1": 18.0, "ce2": 19.0}

    # Warm 14 full 10-second buckets while spot remains nearly flat.
    for step in range(1, 15):
        ts = base + step * IVEarlyMoveDetector.BUCKET_NS
        quotes = []
        metrics = []
        for token in ("ce1", "ce2"):
            volumes[token] += 10
            marks[token] += 0.01
            ivs[token] += 0.01
            quotes.append(SimpleNamespace(
                token=token, timestamp_ns=ts, volume=volumes[token],
                option_type="CE", mark=marks[token],
                bid=marks[token] - 0.1, ask=marks[token] + 0.1,
            ))
            metrics.append({
                "token": token, "iv": ivs[token],
                "iv_input": "BID_ASK_MIDPOINT",
                "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
            })
        detector.update(
            quotes=tuple(quotes), metrics=tuple(metrics),
            spot_price=24000.0, now_ns=ts, spot_timestamp_ns=ts,
        )

    # Both contracts show a sharp, synchronized pressure burst, but spot is
    # still flat: the detector should flag the lead signal, not wait for a
    # 10-second positive spot move.
    ts = base + 15 * IVEarlyMoveDetector.BUCKET_NS
    quotes = []
    metrics = []
    for token in ("ce1", "ce2"):
        volumes[token] += 1000
        marks[token] += 1.0
        ivs[token] += 0.2
        quotes.append(SimpleNamespace(
            token=token, timestamp_ns=ts, volume=volumes[token],
            option_type="CE", mark=marks[token],
            bid=marks[token] - 0.1, ask=marks[token] + 0.1,
        ))
        metrics.append({
            "token": token, "iv": ivs[token],
            "iv_input": "BID_ASK_MIDPOINT",
            "source_timestamp_ns": ts, "spot_timestamp_ns": ts,
        })

    result = detector.update(
        quotes=tuple(quotes), metrics=tuple(metrics),
        spot_price=24000.0, now_ns=ts, spot_timestamp_ns=ts,
    )
    assert result["state"] == "PRE_MOVE"
    assert result["direction"] == "BULLISH"
    assert result["contracts_confirming"] == 2
    assert result["qualifying_tokens"] == ["ce1", "ce2"]
    assert result["source_timestamp_ns"] == ts
    assert result["spot_timestamp_ns"] == ts
    assert result["signals_allowed"] is True
    assert "before a material spot move" in result["reason"]

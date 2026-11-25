from app.market_data.shared_index_option_rotation_controller import SharedIndexOptionRotationController



class StubLifecycle:
    def __init__(self):
        self.rotations = []
        self.current_options = ()
    def rotate_options(self, descriptors):
        selected = tuple(descriptors)
        self.rotations.append(selected)
        self.current_options = selected
    def option_descriptors(self):
        return self.current_options



class FakeClock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now



def _rows(expiry="26NOV2026"):
    rows = []
    for underlying, base, step, count in (("NIFTY", 21750, 50, 13), ("BANKNIFTY", 47600, 100, 11)):
        for index in range(count):
            strike = base + index * step
            for side in ("CE", "PE"):
                token = f"{underlying}{strike}{side}"
                rows.append({"exch_seg":"NFO", "name":underlying, "instrumenttype":"OPTIDX",
                    "expiry":expiry, "strike":strike*100, "symbol":token, "token":token,
                    "lotsize":50})
    return rows



def test_controller_initial_spot_tick_only_anchors_and_threshold_rotates():
    clock = FakeClock()
    lifecycle = StubLifecycle()
    controller = SharedIndexOptionRotationController(lifecycle=lifecycle,
        instrument_rows=_rows, expiry_for=lambda _: "26NOV2026",
        thresholds={"NIFTY":50}, cooldown_seconds=1, clock=clock)
    assert not controller.on_record(_spot("NIFTY", 22000))
    assert controller.on_record(_spot("NIFTY", 22050))
    assert len(lifecycle.rotations) == 1
    assert len(lifecycle.rotations[0]) == 26
    assert controller.snapshot()["rotation_successes"] == 1



def test_controller_resolver_failure_keeps_existing_subscriptions_untouched():
    lifecycle = StubLifecycle()
    controller = SharedIndexOptionRotationController(lifecycle=lifecycle,
        instrument_rows=lambda: [], expiry_for=lambda _: "26NOV2026",
        thresholds={"NIFTY":50})
    assert not controller.on_record(_spot("NIFTY", 22000))
    assert not controller.on_record(_spot("NIFTY", 22050))
    assert lifecycle.rotations == []
    assert controller.snapshot()["rotation_failures"] == 1



def _spot(symbol, ltp):
    from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
    from time import time_ns
    return MarketDataRecord(instrument=InstrumentKey("NSE", "NSE", symbol),
        symbol=symbol, instrument_type=InstrumentType.INDEX, timestamp_ns=time_ns(),
        ltp=ltp, underlying=symbol)


def test_concurrent_same_underlying_ticks_are_serialized_to_single_rotation():
    from concurrent.futures import ThreadPoolExecutor
    lifecycle = StubLifecycle()
    controller = SharedIndexOptionRotationController(lifecycle=lifecycle,
        instrument_rows=_rows, expiry_for=lambda _: "26NOV2026",
        thresholds={"NIFTY":50}, cooldown_seconds=0)
    assert not controller.on_record(_spot("NIFTY", 22000))
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _i: controller.on_record(_spot("NIFTY", 22050)), range(24)))
    assert sum(results) == 1
    assert len(lifecycle.rotations) == 1
    snap = controller.snapshot()
    assert snap["rotation_successes"] == 1
    assert snap["trigger"]["successful_anchor_spot"]["NIFTY"] == 22050


def test_concurrent_rotations_for_both_locked_underlyings_are_consistent():
    from concurrent.futures import ThreadPoolExecutor

    lifecycle = StubLifecycle()
    controller = SharedIndexOptionRotationController(
        lifecycle=lifecycle,
        instrument_rows=_rows,
        expiry_for=lambda _: "26NOV2026",
        thresholds={"NIFTY": 50, "BANKNIFTY": 100},
        cooldown_seconds=0,
    )
    assert not controller.on_record(_spot("NIFTY", 22000))
    assert not controller.on_record(_spot("BANKNIFTY", 48000))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda item: controller.on_record(_spot(*item)),
            (("NIFTY", 22050), ("BANKNIFTY", 48100)),
        ))

    assert results == [True, True]
    assert len(lifecycle.rotations) == 2
    # The first request selects one underlying; the serialized second request
    # must preserve the first set and add the other underlying's contracts.
    rotation_counts = sorted(len(rotation) for rotation in lifecycle.rotations)
    assert rotation_counts in ([22, 48], [26, 48])
    final_options = lifecycle.option_descriptors()
    assert len(final_options) == 48
    assert len({descriptor.key for descriptor in final_options}) == 48
    assert sum(descriptor.underlying == "NIFTY" for descriptor in final_options) == 26
    assert sum(descriptor.underlying == "BANKNIFTY" for descriptor in final_options) == 22
    snapshot = controller.snapshot()
    assert snapshot["rotation_successes"] == 2
    assert snapshot["rotation_failures"] == 0
    assert snapshot["trigger"]["successful_anchor_spot"] == {
        "NIFTY": 22050.0,
        "BANKNIFTY": 48100.0,
    }
    assert set(snapshot["last_rotation"]) == {"NIFTY", "BANKNIFTY"}

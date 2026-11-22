from app.market_data.shared_index_spot_rotation_trigger import SpotRotationTrigger



class FakeClock:
    def __init__(self):
        self.now = 10.0
    def __call__(self):
        return self.now



def test_rotation_trigger_ignores_initial_tick_and_small_moves():
    clock = FakeClock()
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50}, clock=clock)
    assert not trigger.on_spot("NIFTY", 22000)
    assert not trigger.on_spot("NIFTY", 22025)
    assert trigger.snapshot()["pending_underlyings"] == []



def test_rotation_trigger_fires_on_threshold_then_coalesces_cooldown():
    clock = FakeClock()
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50}, cooldown_seconds=2, clock=clock)
    assert not trigger.on_spot("NIFTY", 22000)
    assert trigger.on_spot("NIFTY", 22050)
    trigger.rotation_result("NIFTY", success=True, spot=22050)
    clock.now += .5
    assert not trigger.on_spot("NIFTY", 22100)
    assert trigger.snapshot()["pending_underlyings"] == ["NIFTY"]
    clock.now += 1.5
    assert trigger.flush_due() == ("NIFTY",)
    assert trigger.flush_due() == ()


def test_rotation_trigger_rejects_unlocked_symbol_and_invalid_prices():
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50})
    assert not trigger.on_spot("FINNIFTY", 20000)
    assert not trigger.on_spot("NIFTY", 0)

def test_failed_rotation_preserves_successful_anchor_and_retries_after_cooldown():
    clock = FakeClock()
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50}, cooldown_seconds=2, clock=clock)
    assert not trigger.on_spot("NIFTY", 22000)
    assert trigger.on_spot("NIFTY", 22060)
    trigger.rotation_result("NIFTY", success=False, spot=22060)
    state = trigger.snapshot()
    assert state["successful_anchor_spot"]["NIFTY"] == 22000
    assert state["pending_underlyings"] == ["NIFTY"]
    clock.now += 2
    assert trigger.flush_due() == ("NIFTY",)
    assert trigger.snapshot()["inflight_underlyings"] == ["NIFTY"]
    trigger.rotation_result("NIFTY", success=True, spot=22060)
    assert trigger.snapshot()["successful_anchor_spot"]["NIFTY"] == 22060
    assert trigger.snapshot()["pending_underlyings"] == []


def test_successful_rotation_commits_anchor_and_slow_drift_is_measured_from_it():
    clock = FakeClock()
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50}, clock=clock)
    assert not trigger.on_spot("NIFTY", 22000)
    assert trigger.on_spot("NIFTY", 22055)
    trigger.rotation_result("NIFTY", success=True, spot=22055)
    assert not trigger.on_spot("NIFTY", 22080)
    assert trigger.on_spot("NIFTY", 22105)


def test_rotation_trigger_rejects_non_finite_values():
    import math
    for invalid in (math.nan, math.inf, -math.inf):
        try:
            SpotRotationTrigger(thresholds={"NIFTY": invalid})
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite thresholds must be rejected")
    trigger = SpotRotationTrigger(thresholds={"NIFTY": 50})
    assert not trigger.on_spot("NIFTY", math.nan)
    assert not trigger.on_spot("NIFTY", math.inf)

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

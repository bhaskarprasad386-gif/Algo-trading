from app.scanner.early_impulse_signal_store import EarlyImpulseSignalStore
import pytest


def _signal(**kwargs):
    return {"underlying": "NIFTY", "direction": "BUY", "score": 85, "entry": 100, **kwargs}


def test_store_accepts_scanner_only_signal():
    store = EarlyImpulseSignalStore(max_signals=2, cooldown_seconds=10)
    assert store.publish(_signal(), now=100)
    s = store.snapshot()
    assert s["signal_count"] == 1 and s["signals"][0]["scanner_only"]
    assert s["signals"][0]["order_routing_enabled"] is False


def test_cooldown_suppresses_same_direction_duplicate():
    store = EarlyImpulseSignalStore(cooldown_seconds=20)
    assert store.publish(_signal(), now=100)
    assert not store.publish(_signal(score=90), now=110)
    assert store.snapshot()["suppressed_duplicate_count"] == 1
    assert store.publish(_signal(), now=121)


def test_opposite_direction_is_not_blocked():
    store = EarlyImpulseSignalStore(cooldown_seconds=60)
    assert store.publish(_signal(), now=10)
    assert store.publish(_signal(direction="SELL"), now=11)


def test_history_bounded():
    store = EarlyImpulseSignalStore(max_signals=2, cooldown_seconds=0)
    for i in range(3):
        assert store.publish(_signal(entry=100+i), now=i)
    assert [s["entry"] for s in store.snapshot()["signals"]] == [102, 101]


@pytest.mark.parametrize("bad", [
    {"underlying": "", "direction": "BUY", "score": 80},
    {"underlying": "NIFTY", "direction": "HOLD", "score": 80},
    {"underlying": "NIFTY", "direction": "BUY", "score": True},
    {"underlying": "NIFTY", "direction": "BUY", "score": 74},
])
def test_invalid_signal_rejected(bad):
    with pytest.raises(ValueError):
        EarlyImpulseSignalStore().publish(bad, now=1)


def test_clear_resets_history_and_cooldowns():
    store = EarlyImpulseSignalStore()
    store.publish(_signal(), now=100)
    store.clear()
    assert store.snapshot()["signal_count"] == 0
    assert store.publish(_signal(), now=101)

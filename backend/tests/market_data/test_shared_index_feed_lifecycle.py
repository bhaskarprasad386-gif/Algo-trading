from app.market_data.shared_index_feed_lifecycle import SharedIndexFeedLifecycle
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
from app.market_data.contracts import InstrumentKey


class StubManager:
    def __init__(self):
        self.registry = InstrumentRegistry()
        self.callbacks = {}
        self.active = {}
    def register_normalized_callback(self, consumer, callback):
        self.callbacks[consumer] = callback
    def subscribe(self, consumer, keys, mode=1):
        for key in keys:
            self.registry.subscribe(consumer, key, mode)
            self.active.setdefault(consumer, set()).add(key)
    def unsubscribe(self, consumer, keys=None):
        keys = list(keys or self.registry.active_keys())
        for key in keys:
            self.registry.unsubscribe(consumer, key)
            self.active.setdefault(consumer, set()).discard(key)
    def clear_consumer(self, consumer):
        self.registry.clear_consumer(consumer)
        self.active.pop(consumer, None)
        self.callbacks.pop(consumer, None)
    def snapshot(self):
        return {"active_subscriptions": len(self.registry.subscriptions())}


def _spot_descriptors():
    return tuple(InstrumentDescriptor(
        key=InstrumentKey("NSE", "NSE", token), symbol=symbol,
        instrument_type="index", exchange="NSE", segment="NSE", underlying=symbol,
    ) for symbol, token in (("NIFTY", "99001"), ("BANKNIFTY", "99002"))
)


def _options(shift=0):
    descriptors = []
    for underlying, base, count in (("NIFTY", 22000, 13), ("BANKNIFTY", 48000, 11)):
        for n in range(count):
            strike = base + n * 50 + shift
            for opt in ("CE", "PE"):
                token = f"{underlying}-{strike}-{opt}-{shift}"
                descriptors.append(InstrumentDescriptor(
                    key=InstrumentKey("NFO", "NFO", token), symbol=token,
                    instrument_type="option", exchange="NFO", segment="NFO",
                    expiry="TEST", strike=strike, option_type=opt, underlying=underlying,
                ))
    return tuple(descriptors)


def test_options_rotation_adds_before_removing_and_keeps_spot_owner():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_spot(_spot_descriptors())
    lifecycle.start_options(_options())
    spot_before = set(manager.active[lifecycle.SPOT_CONSUMER])
    lifecycle.rotate_options(_options(shift=25))
    assert manager.active[lifecycle.SPOT_CONSUMER] == spot_before
    assert len(manager.active[lifecycle.OPTIONS_CONSUMER]) == 48
    assert len(manager.active[lifecycle.SPOT_CONSUMER]) == 2
    lifecycle.stop_options()
    assert len(manager.active[lifecycle.SPOT_CONSUMER]) == 2
    lifecycle.stop_spot()
    assert not manager.registry.subscriptions()


def test_invalid_locked_option_count_rejected_before_mutation():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    bad = _options()[:-2]
    try:
        lifecycle.start_options(bad)
    except ValueError as exc:
        assert "exactly 26 NIFTY" in str(exc)
    else:
        raise AssertionError("invalid option universe must be rejected")
    assert not manager.registry.subscriptions()

def test_locked_options_reject_unbalanced_pair_before_registry_mutation():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    broken = list(_options())
    # Replace one NIFTY PE with a second CE for the same strike.
    target = broken[1]
    broken[1] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "duplicate-side-token"),
        symbol="duplicate-side-token", instrument_type="option", exchange="NFO", segment="NFO",
        expiry=target.expiry, strike=target.strike, option_type="CE", underlying=target.underlying,
    )
    try:
        lifecycle.start_options(broken)
    except ValueError as exc:
        assert "exactly one CE and PE" in str(exc)
    else:
        raise AssertionError("unbalanced option pairs must be rejected")
    assert not manager.registry.subscriptions()
    assert not manager.registry.active_keys()


def test_locked_options_reject_expiry_mismatch_and_asymmetric_strike_window():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    broken_expiry = list(_options())
    broken_expiry[0] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "expiry-mismatch"),
        symbol="expiry-mismatch", instrument_type="option", exchange="NFO", segment="NFO",
        expiry="DIFFERENT", strike=broken_expiry[0].strike, option_type=broken_expiry[0].option_type,
        underlying=broken_expiry[0].underlying,
    )
    try:
        lifecycle.start_options(broken_expiry)
    except ValueError as exc:
        assert "expiry must be uniform" in str(exc)
    else:
        raise AssertionError("mixed expiries must be rejected")
    assert not manager.registry.active_keys()

    broken_strikes = list(_options())
    original = broken_strikes[0]
    broken_strikes[0] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "off-grid"),
        symbol="off-grid", instrument_type="option", exchange="NFO", segment="NFO",
        expiry=original.expiry, strike=original.strike + 10, option_type=original.option_type,
        underlying=original.underlying,
    )
    try:
        lifecycle.start_options(broken_strikes)
    except ValueError as exc:
        assert "strike window must be symmetric" in str(exc) or "uniform spacing" in str(exc)
    else:
        raise AssertionError("asymmetric strikes must be rejected")
    assert not manager.registry.active_keys()

class FailAfterMutationManager(StubManager):
    def __init__(self):
        super().__init__()
        self.fail_next_subscribe = False
    def subscribe(self, consumer, keys, mode=1):
        super().subscribe(consumer, keys, mode)
        if self.fail_next_subscribe:
            self.fail_next_subscribe = False
            raise RuntimeError("forced reconcile failure after registry mutation")


def test_failed_rotation_rolls_back_new_option_refs_and_preserves_old_set():
    manager = FailAfterMutationManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_spot(_spot_descriptors())
    original = _options()
    lifecycle.start_options(original)
    original_active = set(manager.active[lifecycle.OPTIONS_CONSUMER])
    original_keys = lifecycle._option_keys

    replacement = _options(shift=25)
    manager.fail_next_subscribe = True
    try:
        lifecycle.rotate_options(replacement)
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("rotation failure must propagate")

    assert manager.active[lifecycle.OPTIONS_CONSUMER] == original_active
    assert lifecycle._option_keys == original_keys
    assert len(manager.registry.active_keys()) == 50
    assert lifecycle.snapshot()["spot_instruments"] == 2
    assert manager.registry.get(replacement[0].key) is None

def test_failed_initial_options_start_rolls_back_owner_and_preserves_spot():
    manager = FailAfterMutationManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_spot(_spot_descriptors())
    manager.fail_next_subscribe = True
    try:
        lifecycle.start_options(_options())
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("failed options startup must propagate")

    assert not manager.active.get(lifecycle.OPTIONS_CONSUMER)
    assert len(manager.active[lifecycle.SPOT_CONSUMER]) == 2
    assert lifecycle.snapshot()["options_active"] is False
    assert lifecycle.snapshot()["options_instruments"] == 0
    assert lifecycle.OPTIONS_CONSUMER not in manager.callbacks
    assert len(manager.registry.active_keys()) == 2

def test_failed_initial_spot_start_rolls_back_owner_and_added_descriptors():
    manager = FailAfterMutationManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    manager.fail_next_subscribe = True
    try:
        lifecycle.start_spot(_spot_descriptors())
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("failed spot startup must propagate")
    assert not manager.active.get(lifecycle.SPOT_CONSUMER)
    assert lifecycle.SPOT_CONSUMER not in manager.callbacks
    assert not manager.registry.active_keys()
    assert lifecycle.snapshot()["spot_active"] is False

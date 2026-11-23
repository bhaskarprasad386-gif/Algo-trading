from app.market_data.shared_index_feed_lifecycle import SharedIndexFeedLifecycle
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
from app.market_data.contracts import InstrumentKey, MarketDataRecord, InstrumentType
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed


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


def _future_descriptors(expiry_tag="old"):
    return tuple(InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", f"{underlying}-{expiry_tag}"),
        symbol=f"{underlying}-{expiry_tag}", instrument_type="future",
        exchange="NFO", segment="NFO", expiry=expiry_tag, underlying=underlying,
    ) for underlying in ("NIFTY", "BANKNIFTY"))


def test_futures_rotation_preserves_owner_and_replaces_contracts():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    old = _future_descriptors("old")
    lifecycle.start_futures(old)
    original = set(manager.active[lifecycle.FUTURES_CONSUMER])
    new = _future_descriptors("new")
    lifecycle.rotate_futures(new)
    assert len(manager.active[lifecycle.FUTURES_CONSUMER]) == 2
    assert manager.active[lifecycle.FUTURES_CONSUMER] == {d.key for d in new}
    assert manager.active[lifecycle.FUTURES_CONSUMER].isdisjoint(original)
    assert all(manager.registry.get(key) is None for key in original)
    assert lifecycle.snapshot()["futures_active"] is True
    lifecycle.stop_futures()
    assert not manager.registry.active_keys()


def test_failed_futures_rotation_rolls_back_new_refs_and_keeps_old_contracts():
    manager = FailAfterMutationManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    old = _future_descriptors("old")
    lifecycle.start_futures(old)
    old_keys = set(lifecycle._futures_keys)
    manager.fail_next_subscribe = True
    try:
        lifecycle.rotate_futures(_future_descriptors("new"))
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("futures rotation failure must propagate")
    assert set(lifecycle._futures_keys) == old_keys
    assert manager.active[lifecycle.FUTURES_CONSUMER] == old_keys
    assert len(manager.registry.active_keys()) == 2


def test_options_rotation_adds_before_removing_and_keeps_spot_owner():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_spot(_spot_descriptors())
    lifecycle.start_options(_options())
    spot_before = set(manager.active[lifecycle.SPOT_CONSUMER])
    old_option_keys = set(lifecycle._option_keys)
    lifecycle.rotate_options(_options(shift=25))
    assert manager.active[lifecycle.SPOT_CONSUMER] == spot_before
    assert len(manager.active[lifecycle.OPTIONS_CONSUMER]) == 48
    assert all(manager.registry.get(key) is None for key in old_option_keys)
    assert len(manager.active[lifecycle.SPOT_CONSUMER]) == 2
    lifecycle.stop_options()
    assert len(manager.active[lifecycle.SPOT_CONSUMER]) == 2
    lifecycle.stop_spot()
    assert not manager.registry.subscriptions()


def test_invalid_locked_option_count_rejected_before_mutation():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    bad = _options()[2:]
    try:
        lifecycle.start_options(bad)
    except ValueError as exc:
        assert "NIFTY must contain exactly 26 options" in str(exc)
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
    paired_pe = next(i for i, item in enumerate(broken_strikes) if item.underlying == original.underlying and item.strike == original.strike and item.option_type == "PE")
    broken_strikes[0] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "off-grid-ce"),
        symbol="off-grid-ce", instrument_type="option", exchange="NFO", segment="NFO",
        expiry=original.expiry, strike=original.strike + 25, option_type="CE",
        underlying=original.underlying,
    )
    pe_original = broken_strikes[paired_pe]
    broken_strikes[paired_pe] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "off-grid-pe"),
        symbol="off-grid-pe", instrument_type="option", exchange="NFO", segment="NFO",
        expiry=pe_original.expiry, strike=original.strike + 25, option_type="PE",
        underlying=original.underlying,
    )
    try:
        lifecycle.start_options(broken_strikes)
    except ValueError as exc:
        assert "uniform spacing" in str(exc) or "strike window must be symmetric" in str(exc)
    else:
        raise AssertionError("off-grid strike pair must be rejected")
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


class FailBeforeObsoleteUnsubscribeManager(StubManager):
    def __init__(self):
        super().__init__()
        self.fail_unsubscribe = False
    def unsubscribe(self, consumer, keys=None):
        keys = list(keys or self.registry.active_keys())
        if self.fail_unsubscribe and keys:
            self.fail_unsubscribe = False
            super().unsubscribe(consumer, keys)
            raise RuntimeError("obsolete unsubscribe failed")
        return super().unsubscribe(consumer, keys)


def test_option_rotation_unsubscribe_failure_rolls_back_new_keys():
    manager = FailBeforeObsoleteUnsubscribeManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_options(_options())
    old_keys = set(lifecycle._option_keys)
    manager.fail_unsubscribe = True
    try:
        lifecycle.rotate_options(_options(shift=25))
    except RuntimeError as exc:
        assert "obsolete unsubscribe failed" in str(exc)
    else:
        raise AssertionError("obsolete unsubscribe failure should propagate")
    assert set(lifecycle._option_keys) == old_keys
    assert manager.active[lifecycle.OPTIONS_CONSUMER] == old_keys
    assert set(manager.registry.active_keys()) == old_keys


def test_futures_rotation_unsubscribe_failure_rolls_back_new_keys():
    manager = FailBeforeObsoleteUnsubscribeManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    old = _future_descriptors("old")
    lifecycle.start_futures(old)
    old_keys = set(lifecycle._futures_keys)
    manager.fail_unsubscribe = True
    try:
        lifecycle.rotate_futures(_future_descriptors("new"))
    except RuntimeError as exc:
        assert "obsolete unsubscribe failed" in str(exc)
    else:
        raise AssertionError("obsolete unsubscribe failure should propagate")
    assert set(lifecycle._futures_keys) == old_keys
    assert manager.active[lifecycle.FUTURES_CONSUMER] == old_keys
    assert set(manager.registry.active_keys()) == old_keys


class ReconcileTrackingFailManager(FailAfterMutationManager):
    def __init__(self):
        super().__init__()
        self.reconcile_calls = 0
    def _reconcile(self):
        self.reconcile_calls += 1


def test_registry_rollback_requests_transport_reconciliation_after_partial_subscribe():
    manager = ReconcileTrackingFailManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    old = _options()
    lifecycle.start_options(old)
    manager.fail_next_subscribe = True
    try:
        lifecycle.rotate_options(_options(shift=25))
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("partial subscribe must fail and roll back")
    old_keys = set(d.key for d in old)
    assert manager.registry.active_keys() == tuple(sorted(old_keys, key=lambda key: key.value))
    assert manager.active[lifecycle.OPTIONS_CONSUMER] == old_keys
    assert manager.reconcile_calls == 1


class FailAfterConsumerClearManager(StubManager):
    def __init__(self):
        super().__init__()
        self.fail_consumer = None
    def clear_consumer(self, consumer):
        super().clear_consumer(consumer)
        if consumer == self.fail_consumer:
            self.fail_consumer = None
            raise RuntimeError("reconcile failed after clear mutation")


def test_stop_clears_local_owner_flags_and_continues_after_partial_reconcile_failure():
    manager = FailAfterConsumerClearManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_spot(_spot_descriptors())
    lifecycle.start_options(_options())
    lifecycle.start_futures(_future_descriptors())
    manager.fail_consumer = lifecycle.OPTIONS_CONSUMER

    try:
        lifecycle.stop()
    except RuntimeError as exc:
        assert "reconcile failed after clear mutation" in str(exc)
    else:
        raise AssertionError("teardown should surface the reconcile error")


    snapshot = lifecycle.snapshot()
    assert snapshot["options_active"] is False
    assert snapshot["options_instruments"] == 0
    assert snapshot["futures_active"] is False
    assert snapshot["futures_instruments"] == 0
    assert snapshot["spot_active"] is False
    assert snapshot["spot_instruments"] == 0
    assert manager.registry.active_keys() == ()


def _spot_record(key, timestamp_ns=1):
    return MarketDataRecord(
        instrument=key, symbol="NIFTY", instrument_type=InstrumentType.INDEX,
        timestamp_ns=timestamp_ns, ltp=22000.0,
    )


def test_common_feed_old_callback_is_fenced_after_stop_and_restart():
    manager = StubManager()
    feed = CommonStrategyMarketFeed("epoch-test", manager=manager)
    descriptors = _spot_descriptors()
    key = descriptors[0].key
    old_received = []
    new_received = []
    feed.start(descriptors, old_received.append)
    old_callback = manager.callbacks[feed.consumer]


    feed.stop()
    feed.start(descriptors, new_received.append)
    new_callback = manager.callbacks[feed.consumer]


    # A delayed callback from the prior transport epoch must not reach the
    # retired callback, nor be delivered into the new callback's owner.
    old_callback(_spot_record(key, 100))
    assert old_received == []
    assert new_received == []

    # New epoch starts with a clean timestamp baseline and accepts the tick.
    new_callback(_spot_record(key, 5))
    assert len(new_received) == 1
    assert new_received[0].timestamp_ns == 5
    feed.stop()



def test_common_feed_start_rejects_duplicate_instruments_before_registration():
    manager = StubManager()
    feed = CommonStrategyMarketFeed("duplicate-test", manager=manager)
    descriptors = _spot_descriptors()
    try:
        feed.start((descriptors[0], descriptors[0]), lambda _record: None)
    except ValueError as exc:
        assert "duplicate instruments" in str(exc)
    else:
        raise AssertionError("duplicate keys must be rejected")
    assert not manager.registry.subscriptions()
    assert manager.callbacks == {}


def test_shared_consumer_refs_survive_one_owner_clear_and_duplicate_subscribe():
    registry = InstrumentRegistry()
    descriptor = _spot_descriptors()[0]
    registry.register(descriptor)
    registry.subscribe("owner-a", descriptor.key, mode=1)
    registry.subscribe("owner-a", descriptor.key, mode=1)  # idempotent set ref
    registry.subscribe("owner-b", descriptor.key, mode=3)

    sub = registry.subscriptions()[0]
    assert sub.consumers == frozenset({"owner-a", "owner-b"})
    assert sub.ref_count == 2
    assert sub.mode == 3  # strongest active mode wins

    registry.clear_consumer("owner-a")
    remaining = registry.subscriptions()[0]
    assert remaining.consumers == frozenset({"owner-b"})
    assert remaining.ref_count == 1
    assert remaining.mode == 3
    assert registry.active_keys() == (descriptor.key,)

    registry.clear_consumer("owner-b")
    assert registry.active_keys() == ()
    assert registry.subscriptions() == ()



def test_full_stop_barrier_blocks_concurrent_start_until_all_owners_teardown():
    import threading
    import time

    class BlockingClearManager(StubManager):
        def __init__(self):
            super().__init__()
            self.clear_entered = threading.Event()
            self.release_clear = threading.Event()
        def clear_consumer(self, consumer):
            if consumer == SharedIndexFeedLifecycle.OPTIONS_CONSUMER:
                self.clear_entered.set()
                assert self.release_clear.wait(timeout=2)
            return super().clear_consumer(consumer)


    manager = BlockingClearManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    lifecycle.start_options(_options())
    stop_errors = []
    stop_thread = threading.Thread(target=lambda: _capture_exception(lifecycle.stop, stop_errors))
    stop_thread.start()
    assert manager.clear_entered.wait(timeout=1)

    start_finished = threading.Event()
    start_errors = []
    start_thread = threading.Thread(target=lambda: (_capture_exception(lambda: lifecycle.start_spot(_spot_descriptors()), start_errors), start_finished.set()))
    start_thread.start()
    time.sleep(0.05)
    assert not start_finished.is_set(), "start must wait until full OFF teardown barrier releases"

    manager.release_clear.set()
    stop_thread.join(timeout=2)
    start_thread.join(timeout=2)
    assert not stop_thread.is_alive()
    assert not start_thread.is_alive()
    assert stop_errors == []
    assert start_errors == []
    assert lifecycle.snapshot()["spot_active"] is True
    lifecycle.stop()


def _capture_exception(call, errors):
    try:
        call()
    except Exception as exc:
        errors.append(exc)

def test_subscription_validation_rejects_duplicate_tokens_before_registry_mutation():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    items = list(_future_descriptors("dupe"))
    second = items[1]
    items[1] = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", items[0].key.token),
        symbol=second.symbol, instrument_type=second.instrument_type,
        exchange=second.exchange, segment=second.segment, expiry=second.expiry,
        underlying=second.underlying,
    )
    try:
        lifecycle.start_futures(items)
    except ValueError as exc:
        assert "duplicate subscription tokens" in str(exc)
    else:
        raise AssertionError("duplicate broker tokens must be rejected")
    assert not manager.registry.subscriptions()
    assert lifecycle.snapshot()["futures_active"] is False


def test_subscription_validation_rejects_empty_token_before_registry_mutation():
    manager = StubManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    # InstrumentKey itself rejects empty tokens before descriptors reach the
    # lifecycle validator, so exercise that public contract directly.
    try:
        InstrumentKey("NSE", "NSE", "")
    except ValueError as exc:
        assert "token is required" in str(exc)
    else:
        raise AssertionError("empty broker tokens must be rejected")
    assert not manager.registry.subscriptions()
    assert lifecycle.snapshot()["spot_active"] is False

class RepeatedRollbackFailureManager(StubManager):
    def __init__(self):
        super().__init__()
        self.fail_added_unsubscribe = False
    def unsubscribe(self, consumer, keys=None):
        keys = list(keys or self.registry.active_keys())
        if self.fail_added_unsubscribe and keys:
            raise RuntimeError("persistent transport cleanup failure")
        return super().unsubscribe(consumer, keys)


def test_rotation_rollback_restores_registry_when_transport_cleanup_retries_fail():
    manager = RepeatedRollbackFailureManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    old = _options()
    lifecycle.start_options(old)
    old_keys = set(lifecycle._option_keys)
    replacement = _options(shift=25)
    manager.fail_added_unsubscribe = True
    try:
        lifecycle.rotate_options(replacement)
    except RuntimeError as exc:
        assert "persistent transport cleanup failure" in str(exc)
    else:
        raise AssertionError("rotation cleanup failure must propagate")
    # Registry is the local desired-state authority; transport reconciliation
    # is requested so the supervisor can retry cleanup after this failure.
    assert set(manager.registry.active_keys()) == old_keys
    assert manager.registry.active_keys() == tuple(sorted(old_keys, key=lambda key: key.value))
    assert lifecycle._option_keys == tuple(d.key for d in old)
    assert lifecycle.snapshot()["options_active"] is True

class FailBeforeClearManager(FailAfterMutationManager):
    def __init__(self):
        super().__init__()
        self.fail_clear_before_mutation = False
        self._transport_observers = {}
    def clear_consumer(self, consumer):
        if self.fail_clear_before_mutation:
            self.fail_clear_before_mutation = False
            raise RuntimeError("clear failed before mutation")
        return super().clear_consumer(consumer)


def test_start_rollback_force_cleans_transport_observer_when_clear_fails_before_mutation():
    manager = FailBeforeClearManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    consumer = lifecycle.OPTIONS_CONSUMER
    manager._transport_observers[consumer] = lambda *_args: None
    manager.fail_next_subscribe = True
    manager.fail_clear_before_mutation = True

    try:
        lifecycle.start_options(_options())
    except RuntimeError as exc:
        assert "forced reconcile failure" in str(exc)
    else:
        raise AssertionError("initial options start should fail")

    assert manager.registry.active_keys() == ()
    assert consumer not in manager.callbacks
    assert consumer not in manager._transport_observers
    assert lifecycle.snapshot()["options_active"] is False


def test_concurrent_option_rotations_are_serialized_and_final_owner_is_atomic():
    import threading

    class BlockingFirstRotationManager(StubManager):
        def __init__(self):
            super().__init__()
            self.block_once = False
            self.entered = threading.Event()
            self.release = threading.Event()
            self.rotation_subscribe_calls = []
        def subscribe(self, consumer, keys, mode=1):
            keys = list(keys)
            if consumer == SharedIndexFeedLifecycle.OPTIONS_CONSUMER and self.block_once:
                self.block_once = False
                self.rotation_subscribe_calls.append(tuple(keys))
                self.entered.set()
                assert self.release.wait(timeout=2.0), "blocked subscribe should be released"
            elif consumer == SharedIndexFeedLifecycle.OPTIONS_CONSUMER:
                self.rotation_subscribe_calls.append(tuple(keys))
            return super().subscribe(consumer, keys, mode)

    manager = BlockingFirstRotationManager()
    lifecycle = SharedIndexFeedLifecycle(manager=manager)
    initial = _options()
    lifecycle.start_options(initial)
    first = _options(shift=25)
    second = _options(shift=50)
    manager.block_once = True
    first_errors = []
    second_errors = []
    first_thread = threading.Thread(
        target=lambda: _capture_exception(lambda: lifecycle.rotate_options(first), first_errors),
        daemon=True,
    )
    second_entered = threading.Event()
    def rotate_second():
        second_entered.set()
        _capture_exception(lambda: lifecycle.rotate_options(second), second_errors)
    first_thread.start()
    assert manager.entered.wait(timeout=1.0), "first rotation must enter subscribe"
    second_thread = threading.Thread(target=rotate_second, daemon=True)
    second_thread.start()
    assert second_entered.wait(timeout=1.0)
    # The second caller has started but cannot enter the manager's subscribe
    # until the first caller finishes its add-before-remove transaction.
    assert len(manager.rotation_subscribe_calls) == 1
    manager.release.set()
    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first_errors == []
    assert second_errors == []
    assert len(manager.rotation_subscribe_calls) == 2
    final_keys = set(d.key for d in second)
    assert set(lifecycle._option_keys) == final_keys
    assert manager.active[SharedIndexFeedLifecycle.OPTIONS_CONSUMER] == final_keys
    assert manager.registry.active_keys() == tuple(sorted(final_keys, key=lambda key: key.value))

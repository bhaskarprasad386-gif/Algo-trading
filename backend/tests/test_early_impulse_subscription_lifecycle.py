from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.scanner.early_impulse_subscription_lifecycle import EarlyImpulseSubscriptionLifecycle


def _desc(token="1", symbol="NIFTY", kind="index", expiry=None, strike=None, opt=None, underlying="NIFTY"):
    return InstrumentDescriptor(
        key=InstrumentKey("NSE", "NFO", token),
        symbol=symbol,
        instrument_type=kind,
        exchange="NSE",
        segment="NFO",
        expiry=expiry,
        strike=strike,
        option_type=opt,
        underlying=underlying,
    )


def test_configure_universe_is_bounded_and_scanner_off():
    lifecycle = EarlyImpulseSubscriptionLifecycle(max_universe=2)
    result = lifecycle.set_universe([_desc("1"), _desc("2", symbol="NIFTY FUT", kind="future", underlying="NIFTY")])
    assert result["configured"] == 2
    assert lifecycle.snapshot()["scanner_enabled"] is False
    assert lifecycle.snapshot()["order_routing_enabled"] is False


def test_duplicate_tokens_rejected():
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    try:
        lifecycle.set_universe([_desc("1"), _desc("1")])
    except ValueError as exc:
        assert "duplicate token" in str(exc)
    else:
        raise AssertionError("duplicate should be rejected")


def test_conflicting_duplicate_tokens_rejected():
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    try:
        lifecycle.set_universe([_desc("1"), _desc("1", symbol="OTHER")])
    except ValueError as exc:
        assert "duplicate token with conflicting" in str(exc)
    else:
        raise AssertionError("conflicting token should be rejected")


def test_option_requires_expiry_strike_and_type():
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    invalid = _desc("2", symbol="NIFTY CE", kind="option", underlying="NIFTY", strike=None, opt="CE")
    try:
        lifecycle.set_universe([invalid])
    except ValueError as exc:
        assert "valid expiry" in str(exc)
    else:
        raise AssertionError("invalid option descriptor should be rejected")


def test_expiry_limit_enforced_per_underlying():
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    options = [
        _desc(str(i), symbol=f"NIFTY {exp} {strike} {side}", kind="option", underlying="NIFTY", expiry=exp, strike=strike, opt=side)
        for i, (exp, strike, side) in enumerate([
            ("2026-11-26", 22000, "CE"), ("2026-11-26", 22000, "PE"),
            ("2026-12-03", 22100, "CE"), ("2026-12-03", 22100, "PE"),
            ("2026-12-10", 22200, "CE"),
        ], start=10)
    ]
    try:
        lifecycle.set_universe(options)
    except ValueError as exc:
        assert "maximum 2 option expiries" in str(exc)
    else:
        raise AssertionError("three expiries should be rejected")


def test_enable_disable_calls_injected_transport_once_and_is_idempotent():
    calls = []
    lifecycle = EarlyImpulseSubscriptionLifecycle(
        subscribe=lambda items: calls.append(("sub", len(items))) or len(items),
        unsubscribe=lambda items: calls.append(("unsub", len(items))) or len(items),
    )
    lifecycle.set_universe([_desc("1"), _desc("2", symbol="NIFTY2")])
    lifecycle.enable()
    lifecycle.enable()
    assert calls == [("sub", 2)]
    assert lifecycle.snapshot()["enabled"] is True
    lifecycle.disable()
    lifecycle.disable()
    assert calls == [("sub", 2), ("unsub", 2)]
    assert lifecycle.snapshot()["enabled"] is False


def test_enable_requires_configured_universe_and_transport():
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    try:
        lifecycle.enable()
    except ValueError as exc:
        assert "configure" in str(exc)
    else:
        raise AssertionError("empty enable must fail")
    lifecycle.set_universe([_desc()])
    try:
        lifecycle.enable()
    except RuntimeError as exc:
        assert "transport" in str(exc)
    else:
        raise AssertionError("missing transport must fail closed")


def test_cap_constructor_is_validated():
    try:
        EarlyImpulseSubscriptionLifecycle(max_universe=151)
    except ValueError as exc:
        assert "max_universe" in str(exc)
    else:
        raise AssertionError("cap above hard limit should fail")


def test_enabled_universe_change_fails_closed_without_scoped_unsubscribe():
    subscribed = []
    lifecycle = EarlyImpulseSubscriptionLifecycle(subscribe=lambda items: subscribed.extend(items) or len(items))
    lifecycle.set_universe([_desc("1")])
    lifecycle.enable()
    try:
        lifecycle.set_universe([_desc("2")])
    except RuntimeError as exc:
        assert "unsubscribe transport" in str(exc)
    else:
        raise AssertionError("must reject live replacement without scoped unsubscribe")
    assert lifecycle.snapshot()["configured_instruments"] == 1
    assert lifecycle.snapshot()["instrument_keys"] == ["NSE:1"]
    assert len(subscribed) == 1


def test_consumer_mux_subscribes_physical_token_only_on_first_owner_and_unsub_last():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    physical = []
    mux = ShoonyaConsumerSubscriptionMux(
        subscribe_physical=lambda items: physical.append(("subscribe", tuple(items))) or len(items),
        unsubscribe_physical=lambda items: physical.append(("unsubscribe", tuple(items))) or len(items),
    )
    one = _desc("shared")
    mux.subscribe("early-impulse", [one])
    mux.subscribe("box-shadow", [one])
    assert len(physical) == 1 and physical[0][0] == "subscribe"
    assert mux.snapshot()["owners"]["NSE:shared"] == ["box-shadow", "early-impulse"]
    mux.unsubscribe("early-impulse", [one])
    assert len(physical) == 1
    mux.unsubscribe("box-shadow", [one])
    assert len(physical) == 2 and physical[1][0] == "unsubscribe"


def test_consumer_mux_rejects_token_conflict_and_duplicate_batch():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=lambda items: len(items))
    mux.subscribe("consumer-a", [_desc("same")])
    try:
        mux.subscribe("consumer-b", [_desc("same", symbol="DIFFERENT")])
    except ValueError as exc:
        assert "descriptor conflict" in str(exc)
    else:
        raise AssertionError("descriptor collision should fail")
    try:
        mux.subscribe("consumer-b", [_desc("dup"), _desc("dup")])
    except ValueError as exc:
        assert "duplicate token" in str(exc)
    else:
        raise AssertionError("duplicate request should fail")


def test_consumer_mux_physical_token_limit_is_atomic():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=lambda items: len(items), max_tokens=1)
    mux.subscribe("a", [_desc("one")])
    try:
        mux.subscribe("b", [_desc("two")])
    except ValueError as exc:
        assert "physical token cap" in str(exc)
    else:
        raise AssertionError("cap should fail")
    assert mux.snapshot()["physical_tokens"] == 1


def test_consumer_mux_conflicts_on_same_exchange_token_even_if_segment_differs():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=lambda items: len(items))
    original = _desc("collision")
    mux.subscribe("owner-1", [original])
    alternate = InstrumentDescriptor(
        key=InstrumentKey("NSE", "OTHER_SEGMENT", "collision"), symbol="NIFTY", instrument_type="index",
        exchange="NSE", segment="OTHER_SEGMENT",
    )
    try:
        mux.subscribe("owner-2", [alternate])
    except ValueError as exc:
        assert "descriptor conflict" in str(exc)
    else:
        raise AssertionError("exchange-token collision should be rejected regardless of segment")


def test_consumer_mux_does_not_commit_owner_on_short_physical_subscribe():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: 0, unsubscribe_physical=lambda items: len(items))
    try:
        mux.subscribe("owner", [_desc("short-sub")])
    except RuntimeError as exc:
        assert "incomplete count" in str(exc)
    else:
        raise AssertionError("short subscribe must fail closed")
    assert mux.snapshot()["physical_tokens"] == 0


def test_consumer_mux_does_not_commit_removal_on_short_physical_unsubscribe():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=lambda items: 0)
    item = _desc("short-unsub")
    mux.subscribe("owner", [item])
    try:
        mux.unsubscribe("owner", [item])
    except RuntimeError as exc:
        assert "incomplete count" in str(exc)
    else:
        raise AssertionError("short unsubscribe must fail closed")
    assert mux.snapshot()["physical_tokens"] == 1
    assert mux.snapshot()["owners"]["NSE:short-unsub"] == ["owner"]


def test_lifecycle_universe_rejects_same_exchange_token_with_different_segment():
    alternate = InstrumentDescriptor(
        key=InstrumentKey("NSE", "OTHER_SEGMENT", "collision"), symbol="NIFTY", instrument_type="index",
        exchange="NSE", segment="OTHER_SEGMENT",
    )
    lifecycle = EarlyImpulseSubscriptionLifecycle()
    try:
        lifecycle.set_universe([_desc("collision"), alternate])
    except ValueError as exc:
        assert "duplicate token" in str(exc)
    else:
        raise AssertionError("duplicate exchange-token identity should be rejected")


def test_consumer_mux_quarantines_token_after_partial_physical_subscribe_exception():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    def partial_subscribe(items):
        # Simulate adapter mutating one token before failing.
        raise RuntimeError("adapter partially subscribed")
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=partial_subscribe, unsubscribe_physical=lambda items: len(items))
    item = _desc("uncertain-sub")
    try:
        mux.subscribe("owner", [item])
    except RuntimeError as exc:
        assert "partially subscribed" in str(exc)
    else:
        raise AssertionError("physical callback exception must propagate")
    assert mux.snapshot()["physical_tokens"] == 0
    assert mux.snapshot()["uncertain_tokens"] == ["NSE:uncertain-sub"]
    try:
        mux.subscribe("another-owner", [item])
    except RuntimeError as exc:
        assert "uncertain" in str(exc)
    else:
        raise AssertionError("uncertain token must be quarantined")


def test_consumer_mux_quarantines_token_after_partial_physical_unsubscribe_exception():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    def partial_unsubscribe(items):
        # Simulate adapter removing a token then raising before reporting count.
        raise RuntimeError("adapter partially unsubscribed")
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=partial_unsubscribe)
    item = _desc("uncertain-unsub")
    mux.subscribe("owner", [item])
    try:
        mux.unsubscribe("owner", [item])
    except RuntimeError as exc:
        assert "partially unsubscribed" in str(exc)
    else:
        raise AssertionError("physical callback exception must propagate")
    assert mux.snapshot()["physical_tokens"] == 1
    assert mux.snapshot()["owners"]["NSE:uncertain-unsub"] == ["owner"]
    assert mux.snapshot()["uncertain_tokens"] == ["NSE:uncertain-unsub"]


def test_consumer_mux_rejects_empty_physical_identity_before_adapter_call():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    physical_calls = []
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: physical_calls.append(items) or len(items), unsubscribe_physical=lambda items: len(items))
    malformed = InstrumentDescriptor(key=InstrumentKey("NSE", "NFO", ""), symbol="NIFTY", instrument_type="index", exchange="NSE", segment="NFO")
    try:
        mux.subscribe("consumer", [malformed])
    except ValueError as exc:
        assert "exchange and token" in str(exc)
    else:
        raise AssertionError("empty physical identity must be rejected")
    assert physical_calls == []


def test_consumer_mux_rejects_duplicate_unsubscribe_batch_atomically():
    from app.scanner.early_impulse_subscription_lifecycle import ShoonyaConsumerSubscriptionMux
    physical_calls = []
    item = _desc("dup-unsub")
    mux = ShoonyaConsumerSubscriptionMux(subscribe_physical=lambda items: len(items), unsubscribe_physical=lambda items: physical_calls.append(items) or len(items))
    mux.subscribe("owner", [item])
    try:
        mux.unsubscribe("owner", [item, item])
    except ValueError as exc:
        assert "duplicate token" in str(exc)
    else:
        raise AssertionError("duplicate unsubscribe entries should fail")
    assert physical_calls == []
    assert mux.snapshot()["owners"]["NSE:dup-unsub"] == ["owner"]

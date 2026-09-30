from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


def d(t):
    return InstrumentDescriptor(InstrumentKey("NSE", "NSE_CM", t), f"SYM{t}", "equity", "NSE", "NSE_CM")


def test_registry_refcounts_and_deduplicates():
    r = InstrumentRegistry()
    k = r.register(d("101"))
    r.subscribe("cash", k)
    s = r.subscribe("box", k)
    assert s.ref_count == 2 and r.broker_tokens() == {"NSE:NSE_CM": ("101",)}
    assert r.unsubscribe("cash", k).ref_count == 1
    assert r.unsubscribe("box", k) is None
    assert r.active_keys() == ()


def test_registry_rejects_conflicting_metadata():
    r = InstrumentRegistry()
    r.register(d("101"))
    try:
        r.register(InstrumentDescriptor(InstrumentKey("NSE", "NSE_CM", "101"), "OTHER", "equity", "NSE", "NSE_CM"))
        assert False
    except ValueError:
        pass


def test_clear_consumer_preserves_shared_token():
    r = InstrumentRegistry()
    a = r.register(d("101"))
    b = r.register(d("102"))
    r.subscribe("a", a)
    r.subscribe("a", b)
    r.subscribe("b", b)
    assert r.clear_consumer("a") == 2 and r.active_keys() == (b,)


def test_subscription_mode_is_highest_required_and_updates_per_consumer():
    r = InstrumentRegistry()
    k = r.register(d("101"))
    assert r.subscribe("cash", k, mode=1).mode == 1
    assert r.subscribe("box", k, mode=3).mode == 3
    assert r.subscribe("cash", k, mode=2).mode == 3
    assert r.unsubscribe("box", k).mode == 2
    assert r.unsubscribe("cash", k) is None


def test_register_many_is_atomic_on_conflict():
    r = InstrumentRegistry()
    existing = r.register(d("101"))
    conflicting = InstrumentDescriptor(existing, "OTHER", "equity", "NSE", "NSE_CM")
    try:
        r.register_many((d("102"), conflicting))
        assert False
    except ValueError:
        pass
    assert r.get(InstrumentKey("NSE", "NSE_CM", "102")) is None
    assert r.get(existing) == d("101")


def test_registry_rejects_invalid_subscription_inputs():
    r = InstrumentRegistry()
    k = r.register(d("101"))
    for consumer in ("", "   "):
        try:
            r.subscribe(consumer, k)
            assert False
        except ValueError:
            pass
    for mode in (0, 5, True, "2"):
        try:
            r.subscribe("cash", k, mode=mode)
            assert False
        except ValueError:
            pass
    try:
        r.subscribe("cash", InstrumentKey("NSE", "NSE_CM", "999"))
        assert False
    except KeyError:
        pass

from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor,InstrumentRegistry

def d(t): return InstrumentDescriptor(InstrumentKey("NSE","NSE_CM",t),f"SYM{t}","equity","NSE","NSE_CM")
def test_registry_refcounts_and_deduplicates():
    r=InstrumentRegistry(); k=r.register(d("101")); r.subscribe("cash",k); s=r.subscribe("box",k)
    assert s.ref_count==2 and r.broker_tokens()=={"NSE:NSE_CM":("101",)}
    assert r.unsubscribe("cash",k).ref_count==1; assert r.unsubscribe("box",k) is None; assert r.active_keys()==()
def test_registry_rejects_conflicting_metadata():
    r=InstrumentRegistry(); r.register(d("101"))
    try: r.register(InstrumentDescriptor(InstrumentKey("NSE","NSE_CM","101"),"OTHER","equity","NSE","NSE_CM")); assert False
    except ValueError: pass
def test_clear_consumer_preserves_shared_token():
    r=InstrumentRegistry(); a=r.register(d("101")); b=r.register(d("102")); r.subscribe("a",a); r.subscribe("a",b); r.subscribe("b",b)
    assert r.clear_consumer("a")==2 and r.active_keys()==(b,)

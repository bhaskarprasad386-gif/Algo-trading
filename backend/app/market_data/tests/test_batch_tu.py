from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.market_data_replay import MarketDataReplay
from app.market_data.strategy_manifest import StrategyManifest, StrategyManifestRegistry

class FakeSocket:
    def __init__(self): self.closed=False; self.connect_calls=0
    def connect(self, **kwargs): self.connect_calls += 1; self.on_data=kwargs["on_data"]
    def subscribe(self, tokens, mode=1): pass
    def close(self): self.closed=True

def test_strategy_failure_isolated_and_shared_socket_reused():
    sockets=[]
    manager=CommonWebSocketManager(socket_factory=lambda: sockets.append(FakeSocket()) or sockets[-1])
    key=InstrumentKey("NFO","NFO","1")
    descriptor=InstrumentDescriptor(key=key,symbol="X",instrument_type="future",exchange="NFO",segment="NFO")
    manager.registry.register(descriptor)
    good=[]
    manager.register_normalized_callback("bad", lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    manager.register_normalized_callback("good", good.append)
    manager.subscribe("bad",[key],mode=3); manager.subscribe("good",[key],mode=3)
    sockets[0].on_data({"token":"1","exchange_timestamp":1727000000000,"last_traded_price":10000})
    assert len(good)==1
    assert manager.snapshot()["socket_groups"]==1
    assert manager.snapshot()["delivery_errors"]==1

def test_manifest_and_streaming_replay_share_contract():
    manifest=StrategyManifest("demo","Demo",instrument_types=("future",))
    reg=StrategyManifestRegistry(); reg.register(manifest)
    assert reg.get("DEMO") == manifest
    row=HistoricalRecord("angelone-live-1s","NFO:1:X","1s",1727000000000000000,{"exchange":"NFO","segment":"NFO","token":"1","symbol":"X","instrument_type":"future","ltp":100.0})
    out=[]
    assert MarketDataReplay((row,)).stream(lambda e: out.append(e.record))==1
    assert out[0].instrument==InstrumentKey("NFO","NFO","1")
    assert out[0].ltp==100.0

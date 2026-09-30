"""Production Box Spread live runner using Angel One option ticks."""
from __future__ import annotations
from threading import Thread, Event
from time import time_ns, sleep
from app.algo.auth import AngelOneAuth
from app.core.database import SessionLocal
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_stream import LiveSyntheticOptionFutureRecorder
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker, concrete_strikes_from_master
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.scanner.live_box_spread_pipeline import LiveBoxSpreadPipeline
from app.scanner.box_spread import BoxSpreadScanConfig

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})

class BoxSpreadLiveTarget:
    def __init__(self, underlying: str, instrument_class: str, expiry: str|None=None):
        self.underlying=underlying.strip().upper(); self.instrument_class=instrument_class.strip().upper(); self.expiry=expiry

class LiveBoxSpreadRunner:
    def __init__(self,data_db,targets,*,allowed_stock_symbols,future_master=None,auth=None,on_results=None,policy=None):
        self.data_db=data_db; self.targets=tuple(targets); self.allowed_stock_symbols=frozenset(allowed_stock_symbols)
        self.master=future_master or InstrumentMaster(); self.auth=auth or AngelOneAuth(); self.on_results=on_results
        self.policy=policy or ScanPolicy(); self.stop_event=Event(); self.recorder=None; self.feed=None; self.tracker=None

    def run_forever(self):
        self.master.download()
        symbols=tuple(dict.fromkeys(t.underlying for t in self.targets))
        from datetime import datetime
        today = datetime.now().date()
        strikes = {}
        for target in self.targets:
            wanted = target.expiry
            if wanted is None:
                future_type = "FUTSTK" if target.instrument_class == "STOCK" else "FUTIDX"
                expiries = []
                for item in self.master.instruments:
                    expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                    if str(item.get("exch_seg","")).upper() != expected_segment or str(item.get("name","")).strip().upper() != target.underlying:
                        continue
                    if str(item.get("instrumenttype","")).upper() != future_type:
                        continue
                    text = str(item.get("expiry","")).strip().upper()
                    for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d"):
                        try:
                            value = datetime.strptime(text, fmt).date()
                            if value >= today: expiries.append((value, text))
                            break
                        except ValueError:
                            continue
                if expiries: wanted = min(expiries)[1]
            if wanted:
                strikes.update(concrete_strikes_from_master(self.master.instruments,symbols=(target.underlying,),expiry=wanted, exchange_segment=("BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO")))
        self.tracker=LiveSyntheticAtmTracker(strikes_by_symbol=strikes)
        index_symbols=frozenset(t.underlying for t in self.targets if t.instrument_class=="INDEX")
        self.feed=LiveSyntheticUnderlyingFeed(symbols,tracker=self.tracker,instrument_master=self.master,auth=self.auth,index_symbols=index_symbols)
        Thread(target=self.feed.run_forever,daemon=True,name="box-underlying-feed").start()
        try:
            while not self.stop_event.is_set():
                if not all(self.tracker.atm(s,time_ns()) is not None for s in symbols):
                    sleep(.25); continue
                subs=[]
                for target in self.targets:
                    wanted = target.expiry
                    if wanted is None:
                        future_type = "FUTSTK" if target.instrument_class == "STOCK" else "FUTIDX"
                        expiries = []
                        for item in self.master.instruments:
                            expected_segment = "BFO" if target.instrument_class == "INDEX" and target.underlying in BSE_INDEX_SYMBOLS else "NFO"
                            if str(item.get("exch_seg","")).upper() != expected_segment or str(item.get("name","")).strip().upper() != target.underlying:
                                continue
                            if str(item.get("instrumenttype","")).upper() != future_type:
                                continue
                            text = str(item.get("expiry","")).strip().upper()
                            for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d"):
                                try:
                                    value = datetime.strptime(text, fmt).date()
                                    if value >= datetime.now().date(): expiries.append((value, text))
                                    break
                                except ValueError:
                                    continue
                        if expiries:
                            wanted = min(expiries)[1]
                    sel=select_box_contracts(self.master,underlying=target.underlying,instrument_class=target.instrument_class,
                        atm_strike=self.tracker.atm(target.underlying,time_ns()),expiry=wanted,
                        allowed_stock_symbols=self.allowed_stock_symbols,policy=self.policy)
                    subs.extend(sel.subscriptions)
                scanner=LiveBoxSpreadScanner(
                    atm_provider=self.tracker.atm,
                    config_provider=lambda _s: BoxSpreadScanConfig(allowed_stock_symbols=self.allowed_stock_symbols),
                    policy=self.policy,
                )
                pipeline=LiveBoxSpreadPipeline(scanner,SessionLocal,on_results=self.on_results)
                initial_atm={s:self.tracker.atm(s,time_ns()) for s in symbols}
                def observe(payload):
                    pipeline.observe(payload)
                    symbol=str(payload.get("underlying") or "").upper()
                    previous=initial_atm.get(symbol)
                    current=self.tracker.atm(symbol,time_ns())
                    if previous is not None and current is not None and current != previous and self.recorder is not None:
                        self.recorder.stop()
                self.recorder=LiveSyntheticOptionFutureRecorder(self.data_db,subs,auth=self.auth,on_observation=observe,consumer="box-options")
                self.recorder.run_forever()
                self.recorder=None
        finally:
            self.stop()

    def stop(self):
        self.stop_event.set()
        if self.feed:self.feed.stop()
        if self.recorder:self.recorder.stop()

__all__=["BoxSpreadLiveTarget","LiveBoxSpreadRunner"]

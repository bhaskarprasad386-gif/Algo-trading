"""Continuous 1-second Angel One Calendar-Spread futures collector.

Discovers the two nearest active contracts for every supported index, stock and
commodity future and persists source-backed bid/ask observations. Live orders
are never placed by this component.
"""

from __future__ import annotations
from datetime import date, datetime, time
from queue import Empty, Queue
import threading, time as time_module
from typing import Any
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.core.logger import app_logger
from app.market_data.instruments import InstrumentMaster
from app.market_data.websocket import MarketDataWebSocket

IST=ZoneInfo("Asia/Kolkata")
SOURCE="angelone-calendar-live-1s"
TIMEFRAME="1s"
EXCHANGE_TYPES={"NFO":2,"BFO":4,"MCX":5}
INSTRUMENT_TYPES={"FUTIDX":"INDEX_FUTURE","INDEX_FUTURE":"INDEX_FUTURE",
                  "FUTSTK":"STOCK_FUTURE","STOCK_FUTURE":"STOCK_FUTURE",
                  "FUTCOM":"COMMODITY_FUTURE","FUTCOMINDEX":"COMMODITY_FUTURE"}

def _expiry(value: Any)->date|None:
    text=str(value or "").strip().upper()
    for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d","%d-%m-%Y"):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    return None

def _timestamp_ns(message: dict[str,Any])->int|None:
    try:n=int(float(message.get("exchange_timestamp")))
    except (TypeError,ValueError):return None
    if n<=0:return None
    if n<100_000_000_000:return n*1_000_000_000
    if n<100_000_000_000_000:return n*1_000_000
    if n<100_000_000_000_000_000:return n*1_000
    return n

def _side(message:dict[str,Any],key:str)->tuple[float|None,float|None]:
    levels=message.get(key)
    if not isinstance(levels,list) or not levels or not isinstance(levels[0],dict):return None,None
    try:p=float(levels[0].get("price"))/100.0;q=float(levels[0].get("quantity"))
    except (TypeError,ValueError):return None,None
    return (p if p>0 else None,q if q>0 else None)

class LiveCalendarSpreadOneSecondCollector:
    """Collect all supported futures at 1-second source resolution."""

    def __init__(self,data_db:str,*,auth:AngelOneAuth|None=None,instrument_master:InstrumentMaster|None=None,poll_seconds:float=.25,on_observation=None)->None:
        if poll_seconds<=0:raise ValueError("poll_seconds must be positive")
        self.data_db=data_db; self.auth=auth or AngelOneAuth(); self.instrument_master=instrument_master or InstrumentMaster()
        self.poll_seconds=poll_seconds; self.on_observation=on_observation; self.stop_event=threading.Event(); self._sockets=[]

    @staticmethod
    def market_open(now:datetime|None=None)->bool:
        t=(now or datetime.now(IST))
        return t.weekday()<5 and ((time(9,15)<=t.time()<=time(15,30)) or (time(9,0)<=t.time()<=time(23,30)))

    @staticmethod
    def _exchange_open(exchange: str, value: datetime) -> bool:
        if value.weekday() >= 5:
            return False
        if exchange.upper() == "MCX":
            return time(9, 0) <= value.time() <= time(23, 30)
        return time(9, 15) <= value.time() <= time(15, 30)

    def _contracts(self)->list[dict[str,Any]]:
        today=datetime.now(IST).date(); grouped={}
        for row in self.instrument_master.download():
            exchange=str(row.get("exch_seg") or "").upper()
            kind=INSTRUMENT_TYPES.get(str(row.get("instrumenttype") or "").upper())
            if exchange not in EXCHANGE_TYPES or not kind:continue
            expiry=_expiry(row.get("expiry")); token=str(row.get("token") or "").strip()
            symbol=str(row.get("symbol") or "").strip(); underlying=str(row.get("name") or "").strip().upper()
            try:lot=int(str(row.get("lotsize") or row.get("lotSize") or "0"))
            except ValueError:continue
            if not expiry or expiry<today or not token or not symbol or not underlying or lot<=0:continue
            grouped.setdefault((exchange,underlying),[]).append({"exchange":exchange,"kind":kind,"expiry":expiry,"token":token,"symbol":symbol,"underlying":underlying,"lot_size":lot})
        selected=[]
        for rows in grouped.values():
            rows.sort(key=lambda x:(x["expiry"],x["symbol"],x["token"])); selected.extend(rows[:2])
        return selected

    def _run_session(self)->None:
        contracts=self._contracts()
        if not contracts:
            app_logger.warning("Calendar Spread live collector found no eligible futures"); time_module.sleep(30); return
        self.auth.login(); queue=Queue(); self._sockets=[]
        token_meta={(EXCHANGE_TYPES[r["exchange"]],r["token"]):r for r in contracts}
        grouped={}
        for r in contracts:grouped.setdefault(EXCHANGE_TYPES[r["exchange"]],[]).append(r["token"])
        def receive(message):
            if isinstance(message,dict):queue.put(message)
        for et,tokens in grouped.items():
            socket=MarketDataWebSocket(auth=self.auth); self._sockets.append(socket)
            threading.Thread(target=lambda s=socket,e=et,t=list(dict.fromkeys(tokens)):s.connect(exchange_type=e,tokens=t,mode=3,correlation_id=f"calendar-live-{e}",on_data=receive,reconnect_attempts=3,reconnect_delay_seconds=2),daemon=True).start()
        catalog=HistoricalCatalog(self.data_db); latest={}; written=0
        try:
            while not self.stop_event.is_set() and self.market_open():
                deadline=time_module.monotonic()+self.poll_seconds
                while time_module.monotonic()<deadline:
                    try:message=queue.get(timeout=max(.01,deadline-time_module.monotonic()))
                    except Empty:break
                    token=str(message.get("token") or "").strip()\n                    try: exchange_type=int(message.get("exchange_type"))\n                    except (TypeError,ValueError): exchange_type=None\n                    meta=token_meta.get((exchange_type,token)) if exchange_type is not None else None\n                    if meta is None and exchange_type is None:\n                        matches=[r for (et,tk),r in token_meta.items() if tk==token]\n                        meta=matches[0] if len(matches)==1 else None\n                    ts=_timestamp_ns(message)
                    if not meta or ts is None:continue
                    sec=ts//1_000_000_000*1_000_000_000
                    local_timestamp=datetime.fromtimestamp(sec/1_000_000_000, tz=ZoneInfo("UTC")).astimezone(IST)
                    if not self._exchange_open(meta["exchange"], local_timestamp): continue
                    resolved_exchange_type=EXCHANGE_TYPES[meta["exchange"]]
                    previous=latest.get((resolved_exchange_type,token))
                    if previous and previous[0]!=sec:
                        try:written+=catalog.ingest(HistoricalRecord(SOURCE,f'{meta["exchange"]}:{token}:{meta["symbol"]}',TIMEFRAME,previous[0],previous[1]))
                        except ValueError as exc:app_logger.error(f"Calendar 1-second record rejected {token}: {exc}")
                    bid,bq=_side(message,"best_5_buy_data"); ask,aq=_side(message,"best_5_sell_data")
                    payload=dict(message); payload.update({"ltp":float(message["last_traded_price"])/100.0 if message.get("last_traded_price") is not None else None,"bid":bid,"ask":ask,"bid_qty":bq,"ask_qty":aq,"underlying":meta["underlying"],"leg":"FUTURE","instrument_type":meta["kind"],"exchange":meta["exchange"],"contract_month":f'{meta["expiry"].year:04d}-{meta["expiry"].month:02d}',"expiry":meta["expiry"].isoformat(),"lot_size":meta["lot_size"]})
                    latest[(resolved_exchange_type,token)]=(sec,payload)
                    if self.on_observation is not None:
                        try: self.on_observation(dict(payload, timestamp_ns=sec))
                        except Exception as exc: app_logger.warning(f"Calendar Spread live scanner callback failed: {exc}")
            for (exchange_type,token),(ts,payload) in latest.items():
                meta=token_meta[(exchange_type,token)]; written+=catalog.ingest(HistoricalRecord(SOURCE,f'{meta["exchange"]}:{token}:{meta["symbol"]}',TIMEFRAME,ts,payload))
        finally:
            catalog.close()
            for socket in self._sockets:socket.close()
            self._sockets=[]
        app_logger.info(f"Calendar Spread 1-second live session complete: written={written}")

    def run_forever(self)->None:
        while not self.stop_event.is_set():
            try:
                if self.market_open():self._run_session()
                else:time_module.sleep(5)
            except Exception as exc:
                app_logger.error(f"Calendar Spread 1-second collector failed: {exc}"); time_module.sleep(10)

    def stop(self)->None:
        self.stop_event.set()
        for socket in self._sockets:socket.close()

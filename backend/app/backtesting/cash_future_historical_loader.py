"""Stream durable Cash-Future history into strategy-ready observations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import date, datetime, time, timezone
from itertools import groupby
from math import isfinite
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from .contract_master import ContractMasterCatalog, ContractRecord
from .historical_catalog import HistoricalCatalog, HistoricalRecord
from app.scanner.cash_future_history import CashFutureHistoryPoint

MARKET_TZ = ZoneInfo("Asia/Kolkata")
PAIR_TOLERANCE_NS = 60 * 1_000_000_000
LOADER_IDENTITY = "cash_future_historical_loader:v1"

@dataclass(frozen=True)
class CashFutureHistorySelection:
    spot_instrument: str
    exchange: str
    underlying: str
    start_date: date
    end_date: date
    timeframe: str = "1m"
    contract_month: str | None = None
    mode: str = "CURRENT"
    source: str = "angelone"
    def __post_init__(self) -> None:
        if not isinstance(self.spot_instrument, str) or not self.spot_instrument.strip(): raise ValueError("spot_instrument is required")
        if not isinstance(self.exchange, str) or not self.exchange.strip() or not isinstance(self.underlying, str) or not self.underlying.strip(): raise ValueError("exchange and underlying are required")
        if type(self.start_date) is not date or type(self.end_date) is not date: raise TypeError("start_date and end_date must be dates")
        if self.end_date < self.start_date: raise ValueError("end_date cannot be before start_date")
        if not isinstance(self.mode, str) or self.mode.upper() not in {"CURRENT", "NEAR"}: raise ValueError("mode must be CURRENT or NEAR")
        if not isinstance(self.source, str) or not self.source.strip() or not isinstance(self.timeframe, str) or not self.timeframe.strip(): raise ValueError("source and timeframe are required")
        if self.contract_month is not None and not isinstance(self.contract_month, str): raise ValueError("contract_month must be YYYY-MM")

def _ns(dt: datetime) -> int:
    if dt.tzinfo is None: dt = dt.replace(tzinfo=MARKET_TZ)
    utc = dt.astimezone(timezone.utc)
    return (utc.toordinal()-date(1970,1,1).toordinal())*86_400_000_000_000 + utc.hour*3_600_000_000_000 + utc.minute*60_000_000_000 + utc.second*1_000_000_000 + utc.microsecond*1_000

def _market_bounds(day: date) -> tuple[int, int]: return _ns(datetime.combine(day,time(9,15),tzinfo=MARKET_TZ)), _ns(datetime.combine(day,time(15,30),tzinfo=MARKET_TZ))

def _record_price(record: HistoricalRecord) -> float:
    value=record.payload.get("close")
    if value is None: raise ValueError(f"historical record has no close price: {record.instrument} @ {record.timestamp_ns}")
    try: price=float(value)
    except (TypeError,ValueError) as exc: raise ValueError(f"historical close price must be numeric: {record.instrument} @ {record.timestamp_ns}") from exc
    if not isfinite(price) or price<=0: raise ValueError(f"historical close price must be finite and positive: {record.instrument} @ {record.timestamp_ns}")
    return price

def _optional_price(payload: dict, key: str) -> float | None:
    value=payload.get(key)
    if value is None: return None
    try: price=float(value)
    except (TypeError,ValueError): return None
    return price if isfinite(price) and price>0 else None

def _optional_quantity(payload:dict,*keys:str)->float|None:
    for key in keys:
        value=payload.get(key)
        if value is not None:
            try: quantity=float(value)
            except (TypeError,ValueError): return None
            return quantity if isfinite(quantity) and quantity>=0 else None
    return None

def _datetime_from_ns(timestamp_ns:int)->datetime:
    seconds,remainder=divmod(int(timestamp_ns),1_000_000_000)
    return datetime.fromtimestamp(seconds,tz=timezone.utc).replace(microsecond=remainder//1000).astimezone(MARKET_TZ)

def _in_nse_session(timestamp_ns:int)->bool:
    local=_datetime_from_ns(timestamp_ns)
    return local.weekday()<5 and time(9,15)<=local.time()<=time(15,30)

def _payload_volume(payload:dict)->float|None:
    value=payload.get("volume")
    if value is None: return None
    try: value=float(value)
    except (TypeError,ValueError): return None
    return value if isfinite(value) and value>=0 else None

def _payload_oi(payload:dict)->float|None:
    value=payload.get("open_interest",payload.get("oi"))
    if value is None: return None
    try: value=float(value)
    except (TypeError,ValueError): return None
    return value if isfinite(value) and value>=0 else None

def _merge_pair(cash_records:Iterator[HistoricalRecord],future_records:Iterator[HistoricalRecord],*,symbol:str,contract:ContractRecord)->Iterator[CashFutureHistoryPoint]:
    cash=next(cash_records,None); future=next(future_records,None)
    while cash is not None and future is not None:
        delta=cash.timestamp_ns-future.timestamp_ns
        if abs(delta)>PAIR_TOLERANCE_NS:
            if delta<0: cash=next(cash_records,None)
            else: future=next(future_records,None)
            continue
        timestamp_ns=max(cash.timestamp_ns,future.timestamp_ns)
        cash_date=_datetime_from_ns(cash.timestamp_ns).date(); future_date=_datetime_from_ns(future.timestamp_ns).date()
        if cash_date!=future_date:
            if cash.timestamp_ns < future.timestamp_ns: cash=next(cash_records,None)
            else: future=next(future_records,None)
            continue
        if not _in_nse_session(timestamp_ns):
            if cash.timestamp_ns <= timestamp_ns: cash=next(cash_records,None)
            if future.timestamp_ns <= timestamp_ns: future=next(future_records,None)
            continue
        cash_payload=dict(cash.payload); future_payload=dict(future.payload); cash_price=_record_price(cash); future_price=_record_price(future)
        gap=future_price-cash_price; gap_pct=gap/cash_price*100.0
        margin=float(future_payload.get("margin_required",future_payload.get("margin",0.0)) or 0.0)
        if not isfinite(margin): margin=0.0
        charges=float(future_payload.get("charges",0.0) or 0.0)
        if not isfinite(charges): charges=0.0
        funding_cost=float(future_payload.get("funding_cost",0.0) or 0.0)
        if not isfinite(funding_cost): funding_cost=0.0
        yield CashFutureHistoryPoint(timestamp=_datetime_from_ns(timestamp_ns),symbol=symbol,contract_month=f"{contract.expiry.year:04d}-{contract.expiry.month:02d}",cash_price=cash_price,future_price=future_price,gap=gap,gap_pct=gap_pct,lot_size=contract.lot_size,margin_required=max(0.0,margin),volume=_payload_volume(future_payload),oi=_payload_oi(future_payload),cash_bid=_optional_price(cash_payload,"bid"),cash_ask=_optional_price(cash_payload,"ask"),future_bid=_optional_price(future_payload,"bid"),future_ask=_optional_price(future_payload,"ask"),cash_bid_qty=_optional_quantity(cash_payload,"bid_qty","bid_quantity","buy_quantity"),cash_ask_qty=_optional_quantity(cash_payload,"ask_qty","ask_quantity","sell_quantity"),future_bid_qty=_optional_quantity(future_payload,"bid_qty","bid_quantity","buy_quantity"),future_ask_qty=_optional_quantity(future_payload,"ask_qty","ask_quantity","sell_quantity"),charges=charges,funding_cost=funding_cost,expiry_date=contract.expiry)
        cash=next(cash_records,None); future=next(future_records,None)

class CashFutureHistoricalLoader:
    """Resolve historical contracts point-in-time and stream matching cash/future bars."""
    def __init__(self,catalog:HistoricalCatalog,contract_catalog:ContractMasterCatalog)->None: self.catalog=catalog; self.contract_catalog=contract_catalog
    def _contracts_by_segment(self,selection:CashFutureHistorySelection)->tuple[tuple[date,date,ContractRecord],...]:
        days=[]; current=selection.start_date
        while current<=selection.end_date:
            if current.weekday()<5:
                def resolve(exchange:str):
                    if selection.contract_month: return self.contract_catalog.resolve_contract_month(exchange=exchange,underlying=selection.underlying.upper(),contract_month=selection.contract_month.strip(),as_of=current)
                    return self.contract_catalog.resolve(exchange=exchange,underlying=selection.underlying.upper(),as_of=current,mode=selection.mode)
                try:
                    try: contract=resolve(selection.exchange)
                    except LookupError:
                        if selection.exchange.upper()!="NFO": contract=resolve("NFO")
                        else: raise
                except LookupError as exc: raise LookupError(f"no historical cash-future contract for {selection.underlying.upper()} on {current.isoformat()}") from exc
                days.append((current,contract))
            current=current.fromordinal(current.toordinal()+1)
        segments=[]
        for _,grouped in groupby(days,key=lambda item:item[1]):
            block=list(grouped); segments.append((block[0][0],block[-1][0],block[0][1]))
        return tuple(segments)
    def _resolve_spot_instrument(self,symbol:str,start_ns:int,end_ns:int,requested:str,source:str,timeframe:str)->str:
        if ":" in requested: return requested
        exact=[instrument for instrument in self.catalog.instruments(source=source,timeframe=timeframe,start_ns=start_ns,end_ns=end_ns,prefix="NSE:") if instrument.rsplit(":",1)[-1].upper()==symbol.upper()]
        return exact[0] if exact else requested
    def iter_points(self,selection:CashFutureHistorySelection)->Iterable[CashFutureHistoryPoint]:
        segments = self._contracts_by_segment(selection)
        for segment_start,segment_end,contract in segments:
            start_ns,_=_market_bounds(segment_start); _,end_ns=_market_bounds(segment_end)
            cash_instrument=self._resolve_spot_instrument(selection.underlying.upper(),start_ns,end_ns,selection.spot_instrument,selection.source,selection.timeframe)
            cash_iter=self.catalog.iter_records(source=selection.source,instrument=cash_instrument,timeframe=selection.timeframe,start_ns=start_ns,end_ns=end_ns)
            future_iter=self.catalog.iter_records(source=selection.source,instrument=f"{contract.exchange}:{contract.token}:{contract.symbol}",timeframe=selection.timeframe,start_ns=start_ns,end_ns=end_ns)
            yield from _merge_pair(cash_iter,future_iter,symbol=selection.underlying.upper(),contract=contract)
    def dataset_fingerprint(self, selection: CashFutureHistorySelection) -> str:
        """Hash the exact raw rows and point-in-time contracts selected by this loader."""
        digest = hashlib.sha256()
        def add(value: object) -> None:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        add({"loader_identity": LOADER_IDENTITY, "selection": {
            "spot_instrument": selection.spot_instrument, "exchange": selection.exchange,
            "underlying": selection.underlying, "start_date": selection.start_date.isoformat(),
            "end_date": selection.end_date.isoformat(), "timeframe": selection.timeframe,
            "contract_month": selection.contract_month, "mode": selection.mode.upper(), "source": selection.source,
        }})
        for segment_start, segment_end, contract in self._contracts_by_segment(selection):
            add({"segment_start": segment_start.isoformat(), "segment_end": segment_end.isoformat(),
                 "contract": {"exchange": contract.exchange, "symbol": contract.symbol, "token": contract.token,
                              "expiry": contract.expiry.isoformat(), "instrument_type": contract.instrument_type,
                              "underlying": contract.underlying, "lot_size": contract.lot_size,
                              "snapshot_date": contract.snapshot_date.isoformat() if contract.snapshot_date else None,
                              "tick_size": contract.tick_size}})
            start_ns, _ = _market_bounds(segment_start); _, end_ns = _market_bounds(segment_end)
            cash_instrument = self._resolve_spot_instrument(selection.underlying.upper(), start_ns, end_ns,
                                                            selection.spot_instrument, selection.source, selection.timeframe)
            future_instrument = f"{contract.exchange}:{contract.token}:{contract.symbol}"
            for label, instrument in (("cash", cash_instrument), ("future", future_instrument)):
                add({"leg": label, "instrument": instrument})
                for record in self.catalog.iter_records(source=selection.source, instrument=instrument,
                                                        timeframe=selection.timeframe, start_ns=start_ns, end_ns=end_ns):
                    add({"source": record.source, "instrument": record.instrument, "timeframe": record.timeframe,
                         "timestamp_ns": record.timestamp_ns, "sequence": record.sequence,
                         "payload_hash": self.catalog._hash(self.catalog._payload_json(record.payload))})
        return digest.hexdigest()

    def load_points(self,selection:CashFutureHistorySelection)->tuple[CashFutureHistoryPoint,...]: return tuple(self.iter_points(selection))

__all__=["CashFutureHistorySelection","CashFutureHistoricalLoader"]
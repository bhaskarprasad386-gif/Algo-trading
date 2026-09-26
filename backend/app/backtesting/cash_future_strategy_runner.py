"""Historical Cash-Future strategy application and deterministic execution."""
from __future__ import annotations
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Callable, Iterable, Iterator, Mapping, Any
import json, math
from app.backtesting.provenance import provenance_hash
from app.backtesting.cash_future_strategy_checkpoint import CashFutureStrategyCheckpoint
from app.backtesting.ledger import LedgerRecord, Checkpoint
from app.scanner.cash_future_backtest import _executable_spread_profit
from app.scanner.cash_future_history import CashFutureHistoryPoint

CashFutureStrategy = Callable[[CashFutureHistoryPoint, tuple[CashFutureHistoryPoint, ...]], str | None]

@dataclass(frozen=True)
class CashFutureStrategyConfig:
    initial_capital: float=100_000_000.0; execution_model:str="gap"; charges_per_trade:float=0.0; funding_cost_per_trade:float=0.0; start_date:date|None=None; end_date:date|None=None; start_timestamp:datetime|None=None; end_timestamp:datetime|None=None; contract_month:str|None=None; history_window:int|None=None; checkpoint_interval:int|None=None; cash_side:str="BUY"; future_side:str="SELL"; slippage_per_share:float=0.0
    def __post_init__(self)->None:
        for value,name in ((self.initial_capital,"initial_capital"),(self.charges_per_trade,"charges_per_trade"),(self.funding_cost_per_trade,"funding_cost_per_trade"),(self.slippage_per_share,"slippage_per_share")):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)): raise ValueError(f"{name} must be a finite number")
        if self.initial_capital<=0: raise ValueError("initial_capital must be positive")
        if self.charges_per_trade<0 or self.funding_cost_per_trade<0: raise ValueError("charges_per_trade and funding_cost_per_trade must be non-negative")
        if self.execution_model not in {"gap","bid_ask"}: raise ValueError("execution_model must be 'gap' or 'bid_ask'")
        if self.cash_side not in {"BUY","SELL"} or self.future_side not in {"BUY","SELL"} or self.cash_side==self.future_side: raise ValueError("cash_side and future_side must be opposite BUY/SELL sides")
        if self.slippage_per_share<0: raise ValueError("slippage_per_share must be non-negative")
        if self.start_date is not None and self.end_date is not None and self.end_date<self.start_date: raise ValueError("end_date cannot be before start_date")
        if self.start_timestamp is not None and self.end_timestamp is not None and self.end_timestamp<self.start_timestamp: raise ValueError("end_timestamp cannot be before start_timestamp")
        for value,name in ((self.history_window,"history_window"),(self.checkpoint_interval,"checkpoint_interval")):
            if value is not None and (isinstance(value,bool) or not isinstance(value,int) or value<=0): raise ValueError(f"{name} must be a positive integer when provided")

@dataclass
class CashFutureCapitalLedger:
    initial_capital:float; realized_capital:float|None=None; reserved_margin:float=0.0; blocked_entries:int=0
    def __post_init__(self)->None:
        if not math.isfinite(float(self.initial_capital)) or self.initial_capital<=0: raise ValueError("initial_capital must be finite and positive")
        if self.realized_capital is None: self.realized_capital=self.initial_capital
        if not math.isfinite(float(self.realized_capital)) or self.realized_capital<0: raise ValueError("realized_capital must be finite and non-negative")
        if not math.isfinite(float(self.reserved_margin)) or self.reserved_margin<0: raise ValueError("reserved_margin must be finite and non-negative")
        if isinstance(self.blocked_entries,bool) or not isinstance(self.blocked_entries,int) or self.blocked_entries<0: raise ValueError("blocked_entries must be a non-negative integer")
    @property
    def available_capital(self)->float: return float(self.realized_capital)-self.reserved_margin
    def reserve(self,margin_required:float)->bool:
        if isinstance(margin_required,bool) or not isinstance(margin_required,(int,float)) or not math.isfinite(float(margin_required)) or margin_required<0: raise ValueError("margin_required must be finite and non-negative")
        margin=float(margin_required)
        if margin>self.available_capital: self.blocked_entries+=1; return False
        self.reserved_margin+=margin; return True
    def release(self,margin_required:float)->None:
        if isinstance(margin_required,bool) or not isinstance(margin_required,(int,float)) or not math.isfinite(float(margin_required)) or margin_required<0: raise ValueError("margin_required must be finite and non-negative")
        self.reserved_margin-=float(margin_required)
        if self.reserved_margin<0: raise ValueError("reserved margin cannot become negative")
    def apply_realized_pnl(self,net_profit:float)->None:
        if not math.isfinite(float(net_profit)): raise ValueError("net_profit must be finite")
        self.realized_capital=float(self.realized_capital)+float(net_profit)
        if self.realized_capital<0: raise ValueError("realized capital cannot become negative")

class _LedgerPayloadSequence(Sequence[Mapping[str,Any]]):
    def __init__(self,ledger,run_id:str,record_type:str)->None:self._ledger=ledger;self._run_id=run_id;self._record_type=record_type
    def __len__(self)->int:return self._ledger.record_count(self._run_id,self._record_type)
    def __iter__(self)->Iterator[Mapping[str,Any]]:
        for record in self._ledger.iter_records(self._run_id,self._record_type): yield record.payload
    def __getitem__(self,index):
        if isinstance(index,slice): start,stop,step=index.indices(len(self)); return tuple(self[i] for i in range(start,stop,step))
        return self._ledger.record_at(self._run_id,self._record_type,index).payload

@dataclass(frozen=True)
class CashFutureStrategyRun:
    strategy_id:str; strategy_version:str; initial_capital:float; final_capital:float; net_profit:float; signals:Sequence[Mapping[str,Any]]; trades:Sequence[Mapping[str,Any]]; equity_curve:Sequence[Mapping[str,Any]]; final_available_capital:float=0.0; final_reserved_margin:float=0.0; blocked_entry_count:int=0

def _builder_gap_profit(entry,exit_point,config)->float:
    direction=1.0 if (config.cash_side,config.future_side)==("BUY","SELL") else -1.0
    gross=direction*(entry.gap-exit_point.gap)*entry.lot_size
    return gross-config.slippage_per_share*entry.lot_size*2.0

def _trade_gross_profit(entry,exit_point,config)->float:
    if config.execution_model=="gap": return _builder_gap_profit(entry,exit_point,config)
    gross=_executable_spread_profit(entry,exit_point)
    if (config.cash_side,config.future_side)!=("BUY","SELL"): gross=-gross
    return gross-config.slippage_per_share*entry.lot_size*2.0

def _execution_metadata(config:CashFutureStrategyConfig)->dict[str,Any]:
    return {"execution_model":config.execution_model,"charges_per_trade":config.charges_per_trade,"funding_cost_per_trade":config.funding_cost_per_trade,"start_date":config.start_date.isoformat() if config.start_date else None,"end_date":config.end_date.isoformat() if config.end_date else None,"start_timestamp":config.start_timestamp.isoformat() if config.start_timestamp else None,"end_timestamp":config.end_timestamp.isoformat() if config.end_timestamp else None,"contract_month":config.contract_month,"history_window":config.history_window,"checkpoint_interval":config.checkpoint_interval,"cash_side":config.cash_side,"future_side":config.future_side,"slippage_per_share":config.slippage_per_share}

def _persist(ledger,run_id,record_type,point,payload,pending=None):
    record=LedgerRecord(run_id,record_type,_timestamp_ns(point.timestamp),payload)
    if pending is None: ledger.append(record)
    else: pending.append(record)
def _timestamp_ns(value:datetime)->int: return int(value.timestamp()*1_000_000_000)

def run_cash_future_strategy(points:Iterable[CashFutureHistoryPoint],strategy:CashFutureStrategy,*,strategy_id:str,strategy_version:str="1",config:CashFutureStrategyConfig|None=None,ledger=None,run_id:str|None=None,strategy_hash:str|None=None,strategy_config_hash:str|None=None,data_source_fingerprint:str|None=None)->CashFutureStrategyRun:
    if not isinstance(strategy_id,str) or not strategy_id.strip(): raise ValueError("strategy_id is required")
    if not isinstance(strategy_version,str) or not strategy_version.strip(): raise ValueError("strategy_version is required")
    config=config or CashFutureStrategyConfig(); start_timestamp=_normalize_bound(config.start_timestamp); end_timestamp=_normalize_bound(config.end_timestamp); selected_contract=config.contract_month; selected_symbol=None; event_index=0; previous_timestamp=None
    if ledger is not None:
        if not run_id or not run_id.strip(): raise ValueError("run_id is required when ledger persistence is enabled")
        ledger.start_run(run_id,strategy_id,strategy_version,config.initial_capital,strategy_hash=strategy_hash,data_source_fingerprint=data_source_fingerprint,metadata={"domain":"cash_future","strategy_config_hash":strategy_config_hash,**_execution_metadata(config)})
    history=[] if config.history_window is None else deque(maxlen=config.history_window); signals=[] if ledger is None else None; trades=[] if ledger is None else None; equity_curve=[] if ledger is None else None; entry=None; capital_ledger=CashFutureCapitalLedger(config.initial_capital); last_point=None; pending_records=[] if ledger is not None and config.checkpoint_interval is not None else None
    entry_action="BUY"; exit_action="SELL"
    for point in points:
        point_date=_point_date(point)
        if previous_timestamp is not None and point.timestamp<previous_timestamp: raise ValueError("Cash-Future strategy input must be ordered by timestamp")
        previous_timestamp=point.timestamp
        if config.end_date is not None and point_date>config.end_date: break
        if end_timestamp is not None and point.timestamp>end_timestamp: break
        # A configured contract month is a hard point-in-time universe boundary.
        if config.contract_month is not None and point.contract_month != config.contract_month:
            continue
        if selected_contract is None:
            selected_contract=point.contract_month
        elif point.contract_month!=selected_contract:
            if config.contract_month is not None:
                continue
            raise ValueError("Cash-Future strategy input contains multiple contract months")
        if selected_symbol is None: selected_symbol=point.symbol
        elif point.symbol!=selected_symbol: raise ValueError("Cash-Future strategy input contains multiple symbols")
        event_index+=1
        if (config.start_date is not None and point_date<config.start_date) or (start_timestamp is not None and point.timestamp<start_timestamp):
            if ledger is not None: _maybe_checkpoint(ledger,config,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)
            continue
        last_point=point; history.append(point); visible_history=tuple(history); raw_signal=strategy(point,visible_history); action="NONE" if raw_signal is None else str(raw_signal).strip().upper()
        if action not in {"BUY","SELL","HOLD","NONE"}: raise ValueError("Cash-Future strategy must return BUY, SELL, HOLD, or NONE")
        signal_record={"timestamp":point.timestamp.isoformat(),"symbol":point.symbol,"contract_month":point.contract_month,"action":action,"cash_price":point.cash_price,"future_price":point.future_price,"gap":point.gap,"lot_size":point.lot_size,"execution_status":"not_executed"}
        expiry_day=point.expiry_date is not None and point_date>=point.expiry_date
        if action==entry_action and entry is None:
            if expiry_day: signal_record.update({"execution_status":"blocked","blocked_reason":"expiry_day_new_entry"})
            elif capital_ledger.reserve(point.margin_required): entry=point; signal_record["execution_status"]="executed"
            else: signal_record.update({"execution_status":"blocked","blocked_reason":"insufficient_available_capital","required_margin":point.margin_required,"available_capital":capital_ledger.available_capital})
        elif action==entry_action and entry is not None:
            capital_ledger.blocked_entries+=1; signal_record.update({"execution_status":"blocked","blocked_reason":"position_already_open"})
        elif action==exit_action and entry is None: signal_record.update({"execution_status":"rejected","rejection_reason":"no_open_position"})
        exit_reason=None
        if action==exit_action and entry is not None: exit_reason="strategy"
        elif entry is not None and expiry_day: exit_reason="expiry"
        if ledger is None: signals.append(signal_record)
        else: _persist(ledger,run_id,"signal",point,signal_record,pending_records)
        if exit_reason is not None and entry is not None:
            gross=_trade_gross_profit(entry,point,config); net=gross-config.charges_per_trade-config.funding_cost_per_trade; capital_ledger.apply_realized_pnl(net); capital_ledger.release(entry.margin_required)
            trade={"entry_time":entry.timestamp.isoformat(),"exit_time":point.timestamp.isoformat(),"symbol":entry.symbol,"contract_month":entry.contract_month,"lot_size":entry.lot_size,"quantity":1,"entry_cash_price":entry.cash_price,"entry_future_price":entry.future_price,"entry_gap":entry.gap,"exit_cash_price":point.cash_price,"exit_future_price":point.future_price,"exit_gap":point.gap,"gross_profit":gross,"charges":config.charges_per_trade,"funding_cost":config.funding_cost_per_trade,"net_profit":net,"execution_model":config.execution_model,"cash_side":config.cash_side,"future_side":config.future_side,"slippage_per_share":config.slippage_per_share,"exit_reason":exit_reason,"reserved_margin":entry.margin_required}
            if ledger is None: trades.append(trade)
            else: _persist(ledger,run_id,"trade",point,trade,pending_records)
            entry=None
        unrealized=_trade_gross_profit(entry,point,config) if entry is not None else 0.0
        equity={"timestamp":point.timestamp.isoformat(),"equity":float(capital_ledger.realized_capital)+unrealized,"realized_capital":float(capital_ledger.realized_capital),"unrealized_pnl":unrealized,"available_capital":capital_ledger.available_capital,"reserved_margin":capital_ledger.reserved_margin}
        if ledger is None: equity_curve.append(equity)
        else: _persist(ledger,run_id,"equity",point,equity,pending_records)
        _maybe_checkpoint(ledger,config,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)
    if ledger is not None and last_point is not None and config.checkpoint_interval is not None: _write_checkpoint(ledger,run_id,event_index,last_point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)
    if ledger is not None: result_signals=_LedgerPayloadSequence(ledger,run_id,"signal"); result_trades=_LedgerPayloadSequence(ledger,run_id,"trade"); result_equity=_LedgerPayloadSequence(ledger,run_id,"equity")
    else: result_signals=tuple(signals or ()); result_trades=tuple(trades or ()); result_equity=tuple(equity_curve or ())
    return CashFutureStrategyRun(strategy_id,strategy_version,config.initial_capital,float(capital_ledger.realized_capital),float(capital_ledger.realized_capital)-config.initial_capital,result_signals,result_trades,result_equity,capital_ledger.available_capital,capital_ledger.reserved_margin,capital_ledger.blocked_entries)

def _maybe_checkpoint(ledger,config,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records=None):
    if ledger is None or config.checkpoint_interval is None or event_index%config.checkpoint_interval!=0:return
    _write_checkpoint(ledger,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)

def _write_checkpoint(ledger,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records=None):
    checkpoint=CashFutureStrategyCheckpoint(run_id=run_id,strategy_id=strategy_id,strategy_version=strategy_version,strategy_hash=strategy_hash,last_timestamp=point.timestamp.isoformat(),selected_contract=selected_contract,realized_capital=float(capital_ledger.realized_capital),reserved_margin=float(capital_ledger.reserved_margin),blocked_entries=int(capital_ledger.blocked_entries),open_entry=_serialize_entry(entry),source_fingerprint=data_source_fingerprint,strategy_state=_capture_strategy_state(strategy))
    durable_checkpoint=Checkpoint(run_id=run_id,event_index=event_index,timestamp_ns=_timestamp_ns(point.timestamp),state=json.loads(checkpoint.to_json()))
    if pending_records is None:
        ledger.checkpoint(durable_checkpoint)
    else:
        ledger.append_and_checkpoint(tuple(pending_records),durable_checkpoint)
        pending_records.clear()

def _capture_strategy_state(strategy)->Mapping[str,Any]|None:
    capture=getattr(strategy,"checkpoint_state",None)
    if capture is None:return None
    state=capture()
    if not isinstance(state,Mapping):raise ValueError("Cash-Future strategy checkpoint_state() must return a Mapping")
    try: json.dumps(state)
    except (TypeError,ValueError) as exc: raise ValueError("Cash-Future strategy checkpoint_state() must return JSON-serializable data") from exc
    return dict(state)

def _serialize_entry(entry):
    if entry is None:return None
    return {"timestamp":entry.timestamp.isoformat(),"symbol":entry.symbol,"contract_month":entry.contract_month,"cash_price":entry.cash_price,"future_price":entry.future_price,"gap":entry.gap,"gap_pct":entry.gap_pct,"lot_size":entry.lot_size,"margin_required":entry.margin_required,"volume":entry.volume,"oi":entry.oi,"cash_bid":entry.cash_bid,"cash_ask":entry.cash_ask,"future_bid":entry.future_bid,"future_ask":entry.future_ask,"cash_bid_qty":entry.cash_bid_qty,"cash_ask_qty":entry.cash_ask_qty,"future_bid_qty":entry.future_bid_qty,"future_ask_qty":entry.future_ask_qty,"charges":entry.charges,"funding_cost":entry.funding_cost,"net_profit":entry.net_profit,"roi_pct":entry.roi_pct,"expiry_date":entry.expiry_date.isoformat() if entry.expiry_date else None}

def _point_date(point): return point.timestamp.date() if isinstance(point.timestamp,datetime) else point.timestamp

def _normalize_bound(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)

__all__=["CashFutureStrategyConfig","CashFutureCapitalLedger","CashFutureStrategyRun","run_cash_future_strategy"]

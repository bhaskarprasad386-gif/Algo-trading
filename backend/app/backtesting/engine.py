"""Deterministic backtesting engine for bars and high-resolution events."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Callable, Iterable, Mapping, Sequence

from app.algo.strategy import Strategy
from app.backtesting.continuous_futures import ContinuousFuturesRecord, build_continuous_futures_series
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.statistics import EquityPoint, calculate_statistics

@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    quantity: float = 1.0
    slippage_rate: float = 0.0
    transaction_cost_rate: float = 0.0
    quantity_step: float | None = None
    tick_size: float | None = None
    transaction_cost_minimum: float = 0.0
    entry_slippage_rate: float | None = None
    exit_slippage_rate: float | None = None
    execution_timing: str = "close"
    enforce_cash: bool = True
    def __post_init__(self):
        for name,value in (("initial_capital",self.initial_capital),("quantity",self.quantity),("slippage_rate",self.slippage_rate),("transaction_cost_rate",self.transaction_cost_rate),("transaction_cost_minimum",self.transaction_cost_minimum)):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(float(value)): raise ValueError(f"{name} must be a finite number")
        if self.initial_capital<=0 or self.quantity<=0: raise ValueError("initial_capital and quantity must be positive")
        if self.quantity_step is not None and (self.quantity_step<=0 or not _is_multiple(self.quantity,self.quantity_step)): raise ValueError("invalid quantity_step/quantity")
        if self.tick_size is not None and self.tick_size<=0: raise ValueError("tick_size must be positive")
        if self.slippage_rate<0 or self.slippage_rate>=1: raise ValueError("slippage_rate must be in [0, 1)")
        for name,value in (("entry_slippage_rate",self.entry_slippage_rate),("exit_slippage_rate",self.exit_slippage_rate)):
            if value is not None and (value<0 or value>=1): raise ValueError(f"{name} must be in [0, 1)")
        if self.transaction_cost_rate<0 or self.transaction_cost_minimum<0: raise ValueError("transaction costs cannot be negative")
        if self.execution_timing not in {"close","next_open"}: raise ValueError("invalid execution_timing")
    @property
    def effective_entry_slippage(self): return self.slippage_rate if self.entry_slippage_rate is None else self.entry_slippage_rate
    @property
    def effective_exit_slippage(self): return self.slippage_rate if self.exit_slippage_rate is None else self.exit_slippage_rate

@dataclass(frozen=True)
class BacktestTrade:
    entry_timestamp: object; exit_timestamp: object; entry_price: float; exit_price: float; quantity: float; gross_pnl: float; costs: float; net_pnl: float

@dataclass(frozen=True)
class BacktestResult:
    initial_capital: float; final_capital: float; net_pnl: float; total_return: float; trades: tuple[BacktestTrade,...]; win_rate: float; expectancy: float; sharpe_ratio: float|None; sortino_ratio: float|None; max_drawdown: float; cagr: float|None; unrealized_pnl: float=0.0; has_open_trade: bool=False; equity_curve: tuple[EquityPoint,...]=()

@dataclass(frozen=True)
class EventContext:
    timestamp_ns:int; sequence:int|None; source:str; instrument:str; payload:Mapping[str,object]; record:HistoricalRecord

@dataclass(frozen=True)
class EventSignal:
    action:str; price:float|None=None
    def __post_init__(self):
        if not isinstance(self.action,str) or self.action.strip().upper() not in {"BUY","SELL","HOLD","NONE"}: raise ValueError("action must be BUY, SELL, HOLD, or NONE")
        if self.price is not None and (isinstance(self.price,bool) or not isinstance(self.price,(int,float)) or not isfinite(float(self.price)) or self.price<=0): raise ValueError("signal price must be finite and positive")
EventStrategy=Callable[[EventContext],EventSignal|str|None]

class BacktestEngine:
    """Run deterministic bar, continuous-futures, or event backtests."""
    def __init__(self,config=None): self.config=config or BacktestConfig()
    def run(self,candles,entry_strategy,exit_strategy):
        capital=self.config.initial_capital; open_trade=None; trades=[]; curve=[]; peak=capital; dd=0.0; prev=None; pending=None; pending_ts=None; last_ts=None; last_close=None
        for candle in candles:
            ts=_validate_candle(candle,prev); prev=ts; last_ts,last_close=ts,float(candle["close"]); context={k:v for k,v in candle.items() if _is_number(v)}
            if self.config.execution_timing=="close":
                if open_trade is None and entry_strategy.evaluate(context): open_trade=(ts,_execution_price(self.config,last_close,"BUY")) ; _ensure_cash_available(self.config,capital,open_trade[1])
                elif open_trade is not None and exit_strategy.evaluate(context):
                    et,ep=open_trade; tr=_build_trade(self.config,et,ep,ts,_execution_price(self.config,last_close,"SELL")); capital+=tr.net_pnl; trades.append(tr); open_trade=None
            point=_equity_point(ts,capital,open_trade,last_close,self.config); curve.append(point); peak=max(peak,point.equity)
            if open_trade is not None and "low" in candle:
                marked=capital+_calculate_unrealized_pnl(self.config,open_trade[1],float(candle["low"])); dd=max(dd,(peak-marked)/peak if peak>0 else 0.0)
        unreal= _calculate_unrealized_pnl(self.config,open_trade[1],last_close) if open_trade is not None and last_close is not None else 0.0
        return _build_result(self.config.initial_capital,capital+unreal,trades,dd,unreal,open_trade is not None,curve)
    def run_events(self,events,strategy,*,price_field="price"):
        if not isinstance(price_field,str) or not price_field.strip(): raise ValueError("price_field is required")
        capital=self.config.initial_capital; open_trade=None; trades=[]; curve=[]; previous=None; last_price=None; last_ts=None
        for record in events:
            if record.sequence is not None and (not isinstance(record.sequence,int) or isinstance(record.sequence,bool) or record.sequence<0): raise ValueError("invalid event sequence")
            key=(record.timestamp_ns,record.sequence if record.sequence is not None else -1)
            if previous is not None and key<=previous: raise ValueError("events must be strictly ordered by timestamp_ns and sequence")
            previous=key
            signal=_normalize_event_signal(strategy(EventContext(record.timestamp_ns,record.sequence,record.source,record.instrument,record.payload,record)))
            if signal.action in {"HOLD","NONE"}: continue
            price,source=_resolve_signal_price(signal,record.payload,price_field); last_price,last_ts=price,record.timestamp_ns
            if open_trade is None and signal.action=="BUY": open_trade=(record.timestamp_ns,_execution_price(self.config,price,"BUY")); _ensure_cash_available(self.config,capital,open_trade[1])
            elif open_trade is not None and signal.action=="SELL":
                et,ep=open_trade; tr=_build_trade(self.config,et,ep,record.timestamp_ns,_execution_price(self.config,price,"SELL")); capital+=tr.net_pnl; trades.append(tr); open_trade=None
            curve.append(_equity_point(record.timestamp_ns,capital,open_trade,price,self.config))
        unreal=_calculate_unrealized_pnl(self.config,open_trade[1],last_price) if open_trade is not None and last_price is not None else 0.0
        return _build_result(self.config.initial_capital,capital+unreal,trades,0.0,unreal,open_trade is not None,curve)

def _validate_candle(candle,previous_timestamp):
    if not isinstance(candle,Mapping): raise ValueError("candle must be a mapping")
    timestamp=candle.get("timestamp")
    if timestamp is None: raise ValueError("candle timestamp is required")
    if previous_timestamp is not None and timestamp<=previous_timestamp: raise ValueError("candles must have strictly increasing timestamps")
    if "close" not in candle: raise ValueError("candle close is required")
    _validate_price_field(candle["close"],"close")
    return timestamp

def _validate_price_field(value,field):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(float(value)) or float(value)<=0: raise ValueError(f"candle {field} must be finite and positive")

def _execution_price(config,market_price,side):
    slippage=config.effective_entry_slippage if side=="BUY" else config.effective_exit_slippage
    return _round_price(market_price*(1+slippage if side=="BUY" else 1-slippage),config.tick_size)

def _round_price(price,tick_size):
    if tick_size is None:return price
    return float((Decimal(str(price))/Decimal(str(tick_size))).quantize(Decimal("1"),rounding="ROUND_HALF_UP")*Decimal(str(tick_size)))

def _ensure_cash_available(config,capital,entry_price):
    if config.enforce_cash and entry_price*config.quantity+max(config.transaction_cost_minimum,entry_price*config.quantity*config.transaction_cost_rate)>capital: raise ValueError("insufficient cash for backtest entry")
def _is_multiple(value,step): return Decimal(str(value))/Decimal(str(step)) == (Decimal(str(value))/Decimal(str(step))).to_integral_value()
def _timestamp_ns(value):
    if isinstance(value,datetime): return int((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()*1e9)
    if isinstance(value,date): return int(datetime(value.year,value.month,value.day,tzinfo=timezone.utc).timestamp()*1e9)
    if isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(float(value)): return int(value)
    raise ValueError("invalid timestamp")
def _equity_point(timestamp,capital,open_trade,mark_price,config):
    unreal=_calculate_unrealized_pnl(config,open_trade[1],mark_price) if open_trade is not None else 0.0
    return EquityPoint(_timestamp_ns(timestamp),capital+unreal,capital-config.initial_capital,unreal)
def _normalize_event_signal(decision):
    if decision is None:return EventSignal("NONE")
    if isinstance(decision,EventSignal):return EventSignal(decision.action.strip().upper(),decision.price)
    if isinstance(decision,str):return EventSignal(decision.strip().upper())
    raise TypeError("event strategy must return EventSignal, action string, or None")
def _resolve_signal_price(decision,payload,price_field):
    if decision.price is not None:return float(decision.price),"signal"
    raw=payload.get(price_field)
    if not _is_number(raw):raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
    return float(raw),f"payload:{price_field}"
def _build_trade(config,entry_timestamp,entry_price,exit_timestamp,exit_price):
    gross=(exit_price-entry_price)*config.quantity; value=(entry_price+exit_price)*config.quantity; costs=max(config.transaction_cost_minimum,value*config.transaction_cost_rate)
    return BacktestTrade(entry_timestamp,exit_timestamp,entry_price,exit_price,config.quantity,gross,costs,gross-costs)
def _calculate_unrealized_pnl(config,entry_price,mark_price):
    exit_price=_execution_price(config,mark_price,"SELL"); gross=(exit_price-entry_price)*config.quantity; value=(entry_price+exit_price)*config.quantity; costs=max(config.transaction_cost_minimum,value*config.transaction_cost_rate); return gross-costs
def _build_result(initial_capital,final_capital,trades,max_drawdown,unrealized_pnl=0.0,has_open_trade=False,equity_curve=()):
    wins=sum(1 for t in trades if t.net_pnl>0); net=final_capital-initial_capital; curve=tuple(equity_curve); stats=calculate_statistics(curve,initial_capital) if curve else None
    return BacktestResult(initial_capital,final_capital,net,net/initial_capital,tuple(trades),wins/len(trades) if trades else 0.0,net/len(trades) if trades else 0.0,stats.sharpe_ratio if stats else None,stats.sortino_ratio if stats else None,max(stats.max_drawdown if stats else 0.0,max_drawdown),stats.cagr if stats else None,unrealized_pnl,has_open_trade,curve)
def _is_number(value): return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(float(value))

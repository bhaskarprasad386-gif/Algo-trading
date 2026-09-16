"""Deterministic, memory-bounded performance metrics for backtest result streams."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable, Mapping

def _timestamp_ns(timestamp: str) -> int:
    parsed=datetime.fromisoformat(str(timestamp).replace("Z","+00:00"))
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
    utc=parsed.astimezone(timezone.utc)
    return (utc.toordinal()-datetime(1970,1,1,tzinfo=timezone.utc).toordinal())*86_400_000_000_000 + utc.hour*3_600_000_000_000 + utc.minute*60_000_000_000 + utc.second*1_000_000_000 + utc.microsecond*1_000

@dataclass(frozen=True)
class BacktestTrade:
    timestamp_ns:int; instrument:str; quantity:int; entry_value:float; exit_value:float; fees:float=0.0
    @property
    def gross_pnl(self)->float:return self.exit_value-self.entry_value
    @property
    def net_pnl(self)->float:return self.gross_pnl-self.fees

@dataclass(frozen=True)
class BacktestReport:
    initial_capital:float; final_equity:float; net_pnl:float; roi:float; max_drawdown:float; max_drawdown_pct:float; win_rate:float; profit_factor:float; turnover:float; trade_count:int; wins:int; losses:int; equity_curve:tuple[tuple[int,float],...]; monthly_pnl:Mapping[str,float]; yearly_pnl:Mapping[str,float]

def build_cash_future_report(initial_capital:float,trades:Iterable[Mapping[str,object]],equity_curve:Iterable[Mapping[str,object]]=())->BacktestReport:
    if not isfinite(initial_capital) or initial_capital<=0: raise ValueError("initial_capital must be positive and finite")
    trade_count=wins=losses=0; gains=losses_abs=turnover=0.0; monthly={}; yearly={}; curve=[]
    for trade in trades:
        try:
            timestamp=str(trade["exit_time"]); gross=float(trade["gross_profit"]); net=float(trade["net_profit"]); charges=float(trade.get("charges",0.0)); funding=float(trade.get("funding_cost",0.0)); legacy_lot=int(trade["lot_size"]); filled=float(trade.get("filled_quantity",trade.get("quantity",legacy_lot)))
        except (KeyError,TypeError,ValueError) as exc: raise ValueError("invalid Cash-Future trade record") from exc
        if legacy_lot<=0 or not isfinite(filled) or filled<=0 or not all(isfinite(v) for v in (gross,net,charges,funding)): raise ValueError("invalid Cash-Future trade values")
        if net!=gross-charges-funding: raise ValueError("Cash-Future net_profit does not reconcile with gross_profit, charges and funding_cost")
        timestamp_ns=_timestamp_ns(timestamp)
        if timestamp_ns<0: raise ValueError("Cash-Future exit_time cannot be before epoch")
        for key in ("entry_cash_price","entry_future_price","exit_cash_price","exit_future_price"):
            value=float(trade.get(key,0.0))
            if not isfinite(value) or value<0: raise ValueError("invalid Cash-Future price")
        turnover += (float(trade.get("entry_cash_price",0.0))+float(trade.get("entry_future_price",0.0))+float(trade.get("exit_cash_price",0.0))+float(trade.get("exit_future_price",0.0)))*filled
        trade_count+=1
        if net>0: wins+=1; gains+=net
        elif net<0: losses+=1; losses_abs+=-net
        parsed=datetime.fromisoformat(timestamp.replace("Z","+00:00")); parsed=parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc); parsed=parsed.astimezone(timezone.utc)
        month=f"{parsed.year:04d}-{parsed.month:02d}"; year=f"{parsed.year:04d}"; monthly[month]=monthly.get(month,0.0)+net; yearly[year]=yearly.get(year,0.0)+net
    equity_points=[]
    for item in equity_curve:
        timestamp=str(item["timestamp"]); equity=float(item["equity"])
        if not isfinite(equity): raise ValueError("invalid Cash-Future equity value")
        equity_points.append((_timestamp_ns(timestamp),equity))
    equity_points.sort()
    if equity_points:
        peak=float(initial_capital); max_dd=max_dd_pct=0.0
        for _,equity in equity_points:
            peak=max(peak,equity); dd=max(0.0,peak-equity); max_dd=max(max_dd,dd); max_dd_pct=max(max_dd_pct,dd/peak if peak>0 else 0.0)
        final_equity=equity_points[-1][1]; curve=equity_points
    else:
        final_equity=initial_capital+sum(monthly.values()); max_dd=max_dd_pct=0.0
    net_pnl=final_equity-initial_capital
    return BacktestReport(initial_capital,final_equity,net_pnl,net_pnl/initial_capital,max_dd,max_dd_pct,wins/(wins+losses) if wins+losses else 0.0,gains/losses_abs if losses_abs>0 else (float("inf") if gains>0 else 0.0),turnover,trade_count,wins,losses,tuple(curve),dict(sorted(monthly.items())),dict(sorted(yearly.items())))

def build_report(initial_capital:float,trades:Iterable[BacktestTrade])->BacktestReport:
    if not isfinite(initial_capital) or initial_capital<=0: raise ValueError("initial_capital must be positive and finite")
    equity=float(initial_capital); peak=equity; max_dd=max_dd_pct=turnover=0.0; wins=losses=0; gains=losses_abs=0.0; trade_count=0; monthly={}; yearly={}; curve=[]
    for trade in trades:
        if trade.timestamp_ns<0 or not trade.instrument.strip() or trade.quantity<=0: raise ValueError("invalid trade record")
        values=(trade.entry_value,trade.exit_value,trade.fees)
        if not all(isfinite(v) and v>=0 for v in values): raise ValueError("trade values must be finite and non-negative")
        pnl=trade.net_pnl; equity+=pnl; peak=max(peak,equity); dd=max(0.0,peak-equity); max_dd=max(max_dd,dd); max_dd_pct=max(max_dd_pct,dd/peak if peak>0 else 0.0); turnover+=trade.entry_value+trade.exit_value
        if pnl>0: wins+=1; gains+=pnl
        elif pnl<0: losses+=1; losses_abs+=-pnl
        dt=datetime.fromtimestamp(trade.timestamp_ns/1_000_000_000,tz=timezone.utc); month=f"{dt.year:04d}-{dt.month:02d}"; year=f"{dt.year:04d}"; monthly[month]=monthly.get(month,0.0)+pnl; yearly[year]=yearly.get(year,0.0)+pnl; curve.append((trade.timestamp_ns,equity)); trade_count+=1
    net_pnl=equity-initial_capital
    return BacktestReport(initial_capital,equity,net_pnl,net_pnl/initial_capital,max_dd,max_dd_pct,wins/(wins+losses) if wins+losses else 0.0,gains/losses_abs if losses_abs>0 else (float("inf") if gains>0 else 0.0),turnover,trade_count,wins,losses,tuple(curve),dict(sorted(monthly.items())),dict(sorted(yearly.items())))

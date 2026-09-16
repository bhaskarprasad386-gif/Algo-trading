"""Portfolio-level Cash-Future strategy execution with simultaneous positions."""
from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from app.backtesting.cash_future_portfolio import CashFuturePortfolioLedger, PositionKey
from app.scanner.cash_future_backtest import _executable_spread_profit, _legacy_gap_profit
from app.scanner.cash_future_history import CashFutureHistoryPoint
PortfolioStrategy=Callable[[CashFutureHistoryPoint,tuple[CashFutureHistoryPoint,...]],str|None]
@dataclass(frozen=True)
class CashFuturePortfolioStrategyRun:
    initial_capital:float; final_capital:float; net_profit:float; signals:tuple[Mapping[str,Any],...]; trades:tuple[Mapping[str,Any],...]; equity_curve:tuple[Mapping[str,Any],...]; final_available_capital:float; final_reserved_margin:float; blocked_entry_count:int; open_position_count:int

def _unrealized_profit(entry,current,execution_model,quantity):
    if execution_model=="gap": return _legacy_gap_profit(entry,current)*(quantity/entry.lot_size)
    exit_prices=(current.cash_bid,current.future_ask)
    if any(price is None or not math.isfinite(float(price)) or float(price)<=0 for price in exit_prices): return 0.0
    return _executable_spread_profit(entry,current)*(quantity/entry.lot_size)

def _historical_fill_capacity(point,*,side,requested_quantity,execution_model):
    requested=float(requested_quantity)
    if not math.isfinite(requested) or requested<=0:return 0.0,"invalid_quantity"
    if execution_model!="bid_ask":return requested,"gap_analytical"
    prices=(point.cash_ask,point.future_bid) if side=="entry" else (point.cash_bid,point.future_ask)
    required=(point.cash_ask_qty,point.future_bid_qty) if side=="entry" else (point.cash_bid_qty,point.future_ask_qty)
    if any(value is None or not math.isfinite(float(value)) or float(value)<=0 for value in prices):return 0.0,"missing_executable_quote"
    if any(value is None for value in required):return 0.0,"strict_bid_ask_no_depth"
    depth=tuple(float(value) for value in required)
    if any(not math.isfinite(value) or value<0 for value in depth):return 0.0,"invalid_depth"
    return min(requested,min(depth)),"historical_depth"

def _close_position(account,entries,key,point,*,execution_model,charges_per_trade,funding_cost_per_trade,exit_reason):
    entry,quantity=entries[key]
    if not math.isfinite(float(quantity)) or quantity<=0:raise ValueError(f"invalid open quantity for {key}: {quantity}")
    fill_quantity,liquidity_source=_historical_fill_capacity(point,side="exit",requested_quantity=quantity,execution_model=execution_model)
    if fill_quantity<=0:return None
    gross=_unrealized_profit(entry,point,execution_model,fill_quantity); net=gross-charges_per_trade-funding_cost_per_trade; account.apply_realized_pnl(net)
    reservation=account.reservation(key); released_margin=float(reservation.margin_required)*fill_quantity/quantity; account.release(released_margin); remaining=quantity-fill_quantity
    if remaining>0:
        remaining_margin=max(float(entry.margin_required),0.0)*remaining/entry.lot_size
        if not account.reserve(remaining_margin):raise ValueError("cannot restore capital reservation for unfilled Cash-Future quantity")
        entries[key]=(entry,remaining)
    else:entries.pop(key,None)
    return {"entry_time":entry.timestamp.isoformat(),"exit_time":point.timestamp.isoformat(),"symbol":entry.symbol,"contract_month":entry.contract_month,"lot_size":entry.lot_size,"requested_quantity":quantity,"filled_quantity":fill_quantity,"unfilled_quantity":remaining,"gross_profit":gross,"charges":charges_per_trade,"funding_cost":funding_cost_per_trade,"net_profit":net,"execution_model":execution_model,"exit_reason":exit_reason,"reserved_margin":released_margin,"liquidity_source":liquidity_source,"fill_status":"filled" if remaining==0 else "partial_fill"}

def _marked_equity(account,entries,latest,execution_model):
    return float(account.realized_capital)+sum(_unrealized_profit(entry,latest[key],execution_model,quantity) for key,(entry,quantity) in entries.items() if key in latest)

def run_cash_future_portfolio_strategy(points:Iterable[CashFutureHistoryPoint],strategy:PortfolioStrategy,*,initial_capital:float=100_000_000.0,execution_model:str="gap",charges_per_trade:float=0.0,funding_cost_per_trade:float=0.0,rollover_policy:str="force_exit",history_window:int=1000)->CashFuturePortfolioStrategyRun:
    if not math.isfinite(float(initial_capital)) or initial_capital<=0:raise ValueError("initial_capital must be positive and finite")
    if execution_model not in {"gap","bid_ask"}:raise ValueError("execution_model must be 'gap' or 'bid_ask'")
    if rollover_policy not in {"force_exit","reject"}:raise ValueError("rollover_policy must be 'force_exit' or 'reject'")
    if any(not math.isfinite(float(value)) or value<0 for value in (charges_per_trade,funding_cost_per_trade)):raise ValueError("charges_per_trade and funding_cost_per_trade must be finite and non-negative")
    if isinstance(history_window,bool) or not isinstance(history_window,int) or history_window<=0:raise ValueError("history_window must be a positive integer")
    ordered=tuple(sorted(points,key=lambda p:(p.timestamp,p.symbol,p.contract_month)))
    account=CashFuturePortfolioLedger(initial_capital); histories:dict[str,deque[CashFutureHistoryPoint]]={}; latest={}; entries={}; active_contract={}; signals=[]; trades=[]; equity=[]
    for point in ordered:
        key:PositionKey=(point.symbol,point.contract_month); previous_contract=active_contract.get(point.symbol)
        if previous_contract is not None and previous_contract!=point.contract_month:
            old_keys=[open_key for open_key in entries if open_key[0]==point.symbol and open_key[1]!=point.contract_month]
            if old_keys:
                if rollover_policy=="reject":raise ValueError("open Cash-Future position crossed a contract rollover boundary")
                for old_key in old_keys:
                    old_point=latest.get(old_key)
                    if old_point is None:raise ValueError("cannot roll over without a genuine last observation for the old contract")
                    rollover_trade=_close_position(account,entries,old_key,old_point,execution_model=execution_model,charges_per_trade=charges_per_trade,funding_cost_per_trade=funding_cost_per_trade,exit_reason="rollover")
                    if rollover_trade is None:raise ValueError("rollover requires executable liquidity at the old contract boundary")
                    trades.append(rollover_trade)
            active_contract[point.symbol]=point.contract_month
        elif previous_contract is None:active_contract[point.symbol]=point.contract_month
        symbol_history=histories.setdefault(point.symbol,deque(maxlen=history_window)); visible_history=tuple(symbol_history); raw_signal=strategy(point,visible_history+(point,)); symbol_history.append(point); latest[key]=point
        action="NONE" if raw_signal is None else str(raw_signal).strip().upper()
        if action not in {"BUY","SELL","HOLD","NONE"}:raise ValueError("Cash-Future portfolio strategy must return BUY, SELL, HOLD, or NONE")
        entry_state=entries.get(key); expiry_day=point.expiry_date is not None and point.timestamp.date()>=point.expiry_date
        signal_record={"timestamp":point.timestamp.isoformat(),"symbol":point.symbol,"contract_month":point.contract_month,"action":action,"lot_size":point.lot_size,"margin_required":point.margin_required}
        if action=="BUY" and entry_state is None:
            requested_quantity=float(point.lot_size)
            if expiry_day:signal_record.update({"execution_status":"blocked","blocked_reason":"expiry_day_new_entry","requested_quantity":requested_quantity,"filled_quantity":0.0})
            else:
                fill_quantity,liquidity_source=_historical_fill_capacity(point,side="entry",requested_quantity=requested_quantity,execution_model=execution_model)
                if fill_quantity<=0:signal_record.update({"execution_status":"no_fill","requested_quantity":requested_quantity,"filled_quantity":0.0,"liquidity_source":liquidity_source})
                else:
                    proportional_margin=max(float(point.margin_required),0.0)*fill_quantity/point.lot_size
                    if account.reserve(key,proportional_margin):
                        entries[key]=(point,fill_quantity); signal_record.update({"execution_status":"executed" if fill_quantity>=requested_quantity else "partial_fill","requested_quantity":requested_quantity,"filled_quantity":fill_quantity,"unfilled_quantity":requested_quantity-fill_quantity,"liquidity_source":liquidity_source,"reserved_margin":proportional_margin})
                    else:signal_record.update({"execution_status":"blocked","blocked_reason":"insufficient_available_capital","required_margin":proportional_margin,"available_capital":account.available_capital,"requested_quantity":requested_quantity,"filled_quantity":0.0,"liquidity_source":liquidity_source})
        elif action=="BUY" and entry_state is not None:account.blocked_entries+=1; signal_record.update({"execution_status":"blocked","blocked_reason":"position_already_open"})
        elif action=="SELL" and entry_state is None:signal_record.update({"execution_status":"rejected","rejection_reason":"no_open_position"})
        else:signal_record["execution_status"]="not_executed"
        entry_state=entries.get(key); exit_reason="strategy" if action=="SELL" and entry_state is not None else None
        if entry_state is not None and expiry_day:exit_reason="expiry"
        signals.append(signal_record)
        if exit_reason is not None and entry_state is not None:
            trade=_close_position(account,entries,key,point,execution_model=execution_model,charges_per_trade=charges_per_trade,funding_cost_per_trade=funding_cost_per_trade,exit_reason=exit_reason)
            if trade is None:
                signal_record.update({"exit_execution_status":"no_fill","exit_requested_quantity":entry_state[1],"exit_filled_quantity":0.0})
                if exit_reason=="expiry":raise ValueError("expiry close could not be filled from the historical observation")
            else:trades.append(trade)
        marked_equity=_marked_equity(account,entries,latest,execution_model)
        if entries and marked_equity<account.reserved_margin:
            signal_record["margin_breach"]=True; signal_record["margin_breach_equity"]=marked_equity; signal_record["margin_breach_reserved_margin"]=account.reserved_margin
            for liquidation_key in sorted(tuple(entries)):
                liquidation_point=latest.get(liquidation_key)
                if liquidation_point is None:raise ValueError("margin breach has no genuine liquidation observation")
                liquidation_trade=_close_position(account,entries,liquidation_key,liquidation_point,execution_model=execution_model,charges_per_trade=charges_per_trade,funding_cost_per_trade=funding_cost_per_trade,exit_reason="margin_breach")
                if liquidation_trade is None:raise ValueError("margin breach requires executable liquidity for every open position")
                trades.append(liquidation_trade)
        if entries and _marked_equity(account,entries,latest,execution_model)<account.reserved_margin:raise ValueError("portfolio margin breach remains after liquidation; current observations cannot safely liquidate all open positions")
        unrealized=_marked_equity(account,entries,latest,execution_model)-float(account.realized_capital); equity.append({"timestamp":point.timestamp.isoformat(),"equity":float(account.realized_capital)+unrealized,"realized_capital":float(account.realized_capital),"unrealized_pnl":unrealized,"available_capital":account.available_capital,"reserved_margin":account.reserved_margin,"open_position_count":account.open_position_count})
    final_unrealized=_marked_equity(account,entries,latest,execution_model)-float(account.realized_capital); final_equity=float(account.realized_capital)+final_unrealized
    return CashFuturePortfolioStrategyRun(initial_capital,final_equity,final_equity-initial_capital,tuple(signals),tuple(trades),tuple(equity),account.available_capital,account.reserved_margin,account.blocked_entries,account.open_position_count)
__all__=["CashFuturePortfolioStrategyRun","PortfolioStrategy","run_cash_future_portfolio_strategy"]
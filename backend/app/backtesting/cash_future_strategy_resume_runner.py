"""Controlled continuation of durable Cash-Future strategy runs."""
from __future__ import annotations
from collections import deque
from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable, Mapping
from app.backtesting.cash_future_strategy_resume import load_validated_cash_future_checkpoint
from app.backtesting.cash_future_strategy_runner import CashFutureCapitalLedger,CashFutureStrategy,CashFutureStrategyConfig,CashFutureStrategyRun,_LedgerPayloadSequence,_point_date,_trade_gross_profit,_write_checkpoint,_persist
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _stored_identity_matches(metadata:Mapping[str,Any],config:CashFutureStrategyConfig)->None:
    expected={"execution_model":config.execution_model,"charges_per_trade":config.charges_per_trade,"funding_cost_per_trade":config.funding_cost_per_trade,"start_date":config.start_date.isoformat() if config.start_date else None,"end_date":config.end_date.isoformat() if config.end_date else None,"contract_month":config.contract_month,"history_window":config.history_window,"checkpoint_interval":config.checkpoint_interval,"cash_side":config.cash_side,"future_side":config.future_side,"slippage_per_share":config.slippage_per_share}
    stored=metadata.get("metadata") or {}
    mismatches={k:(stored.get(k),v) for k,v in expected.items() if stored.get(k)!=v}
    if mismatches: raise ValueError(f"unsafe Cash-Future resume: execution configuration mismatch: {mismatches}")


def resume_cash_future_strategy(points:Iterable[CashFutureHistoryPoint],strategy:CashFutureStrategy,*,ledger,run_id:str,strategy_id:str,strategy_version:str="1",config:CashFutureStrategyConfig|None=None,strategy_hash:str|None=None,data_source_fingerprint:str|None=None)->CashFutureStrategyRun:
    config=config or CashFutureStrategyConfig(); checkpoint_row=ledger.load_checkpoint(run_id)
    checkpoint=load_validated_cash_future_checkpoint(ledger,run_id=run_id,strategy_id=strategy_id,strategy_version=strategy_version,strategy_hash=strategy_hash,data_source_fingerprint=data_source_fingerprint)
    metadata=ledger.run_metadata(run_id) or {}; initial_capital=float(metadata.get("initial_capital",config.initial_capital))
    if not isfinite(initial_capital) or initial_capital<=0: raise ValueError("unsafe Cash-Future resume: stored initial_capital is invalid")
    if config.initial_capital!=initial_capital: raise ValueError(f"unsafe Cash-Future resume: initial_capital mismatch (stored={initial_capital!r}, requested={config.initial_capital!r})")
    _stored_identity_matches(metadata,config)
    if checkpoint_row is None: raise ValueError("unsafe Cash-Future resume: checkpoint is required")
    checkpoint_time=datetime.fromisoformat(checkpoint.last_timestamp)
    if checkpoint.selected_contract and config.contract_month and checkpoint.selected_contract!=config.contract_month:
        raise ValueError("unsafe Cash-Future resume: checkpoint contract does not match requested contract")
    selected_contract=checkpoint.selected_contract or config.contract_month
    if selected_contract is None: raise ValueError("unsafe Cash-Future resume: checkpoint has no selected contract")
    restore=getattr(strategy,"restore_checkpoint_state",None)
    if checkpoint.strategy_state is not None and restore is None: raise ValueError("unsafe Cash-Future resume: checkpoint contains strategy state but strategy cannot restore it")
    if restore is not None:
        if checkpoint.strategy_state is None: raise ValueError("unsafe Cash-Future resume: strategy state is unavailable")
        try: restore(dict(checkpoint.strategy_state))
        except Exception as exc: raise ValueError("unsafe Cash-Future resume: strategy state restoration failed") from exc
    capital_ledger=CashFutureCapitalLedger(initial_capital,realized_capital=checkpoint.realized_capital,reserved_margin=checkpoint.reserved_margin,blocked_entries=checkpoint.blocked_entries)
    entry=_deserialize_entry(checkpoint.open_entry,selected_contract=selected_contract)
    history=[] if config.history_window is None else deque(maxlen=config.history_window)
    previous_timestamp=None; resumed=False; last_processed_point=None; last_processed_event_index=checkpoint_row.event_index; selected_symbol=entry.symbol if entry is not None else None; pending_records=[] if config.checkpoint_interval is not None else None
    for point in points:
        point_date=_point_date(point)
        if previous_timestamp is not None and point.timestamp<previous_timestamp: raise ValueError("Cash-Future strategy input must be ordered by timestamp")
        previous_timestamp=point.timestamp
        if config.end_date is not None and point_date>config.end_date: break
        if point.contract_month!=selected_contract: raise ValueError("unsafe Cash-Future resume: source contains a different contract month")
        if selected_symbol is None: selected_symbol=point.symbol
        elif point.symbol!=selected_symbol: raise ValueError("unsafe Cash-Future resume: source contains a different symbol")
        if point.timestamp<=checkpoint_time:
            if config.start_date is None or point_date>=config.start_date: history.append(point)
            continue
        resumed=True; last_processed_point=point; last_processed_event_index+=1
        if config.start_date is not None and point_date<config.start_date:
            _maybe_checkpoint(ledger,config,run_id,last_processed_event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records); continue
        history.append(point); visible_history=tuple(history); raw_signal=strategy(point,visible_history); action="NONE" if raw_signal is None else str(raw_signal).strip().upper()
        if action not in {"BUY","SELL","HOLD","NONE"}: raise ValueError("Cash-Future strategy must return BUY, SELL, HOLD, or NONE")
        entry_action=config.cash_side; exit_action=config.future_side
        signal={"timestamp":point.timestamp.isoformat(),"symbol":point.symbol,"contract_month":point.contract_month,"action":action,"cash_price":point.cash_price,"future_price":point.future_price,"gap":point.gap,"lot_size":point.lot_size,"execution_status":"not_executed"}
        expiry_day=point.expiry_date is not None and point_date>=point.expiry_date
        if action==entry_action and entry is None:
            if expiry_day: signal.update({"execution_status":"blocked","blocked_reason":"expiry_day_new_entry"})
            elif capital_ledger.reserve(point.margin_required): entry=point; signal["execution_status"]="executed"
            else: signal.update({"execution_status":"blocked","blocked_reason":"insufficient_available_capital","required_margin":point.margin_required,"available_capital":capital_ledger.available_capital})
        elif action==entry_action and entry is not None: capital_ledger.blocked_entries+=1; signal.update({"execution_status":"blocked","blocked_reason":"position_already_open"})
        elif action==exit_action and entry is None: signal.update({"execution_status":"rejected","rejection_reason":"no_open_position"})
        exit_reason="strategy" if action==exit_action and entry is not None else ("expiry" if entry is not None and expiry_day else None)
        _persist(ledger,run_id,"signal",point,signal,pending_records)
        if exit_reason is not None and entry is not None:
            gross=_trade_gross_profit(entry,point,config); net=gross-config.charges_per_trade-config.funding_cost_per_trade; capital_ledger.apply_realized_pnl(net); capital_ledger.release(entry.margin_required)
            _persist(ledger,run_id,"trade",point,{"entry_time":entry.timestamp.isoformat(),"exit_time":point.timestamp.isoformat(),"symbol":entry.symbol,"contract_month":entry.contract_month,"lot_size":entry.lot_size,"quantity":1,"entry_cash_price":entry.cash_price,"entry_future_price":entry.future_price,"entry_gap":entry.gap,"exit_cash_price":point.cash_price,"exit_future_price":point.future_price,"exit_gap":point.gap,"gross_profit":gross,"charges":config.charges_per_trade,"funding_cost":config.funding_cost_per_trade,"net_profit":net,"execution_model":config.execution_model,"cash_side":config.cash_side,"future_side":config.future_side,"slippage_per_share":config.slippage_per_share,"exit_reason":exit_reason,"reserved_margin":entry.margin_required},pending_records); entry=None
        unrealized=_trade_gross_profit(entry,point,config) if entry is not None else 0.0
        _persist(ledger,run_id,"equity",point,{"timestamp":point.timestamp.isoformat(),"equity":float(capital_ledger.realized_capital)+unrealized,"realized_capital":float(capital_ledger.realized_capital),"unrealized_pnl":unrealized,"available_capital":capital_ledger.available_capital,"reserved_margin":capital_ledger.reserved_margin},pending_records)
        _maybe_checkpoint(ledger,config,run_id,last_processed_event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)
    if not resumed: raise ValueError(f"Cash-Future resume source does not contain observations after checkpoint timestamp={checkpoint.last_timestamp}")
    if config.checkpoint_interval is not None and last_processed_point is not None: _write_checkpoint(ledger,run_id,last_processed_event_index,last_processed_point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)
    return CashFutureStrategyRun(strategy_id,strategy_version,initial_capital,float(capital_ledger.realized_capital),float(capital_ledger.realized_capital)-initial_capital,_LedgerPayloadSequence(ledger,run_id,"signal"),_LedgerPayloadSequence(ledger,run_id,"trade"),_LedgerPayloadSequence(ledger,run_id,"equity"),capital_ledger.available_capital,capital_ledger.reserved_margin,capital_ledger.blocked_entries)

def _timestamp_ns(value:datetime)->int:return int(value.timestamp()*1_000_000_000)
def _maybe_checkpoint(ledger,config,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records=None):
    if config.checkpoint_interval is None or event_index%config.checkpoint_interval!=0:return
    _write_checkpoint(ledger,run_id,event_index,point,selected_contract,capital_ledger,entry,strategy,strategy_id,strategy_version,strategy_hash,data_source_fingerprint,pending_records)

def _deserialize_entry(payload:Mapping[str,Any]|None,*,selected_contract:str)->CashFutureHistoryPoint|None:
    if payload is None:return None
    required=("timestamp","symbol","contract_month","cash_price","future_price","gap","gap_pct","lot_size","margin_required","expiry_date"); missing=[name for name in required if name not in payload]
    if missing:raise ValueError(f"unsafe Cash-Future resume: open_entry missing fields: {missing}")
    try:
        timestamp=datetime.fromisoformat(str(payload["timestamp"])); expiry_raw=payload.get("expiry_date"); expiry=date.fromisoformat(str(expiry_raw)) if expiry_raw else None; lot_raw=payload["lot_size"]
        if isinstance(lot_raw,bool) or not isinstance(lot_raw,int) or lot_raw<=0:raise ValueError("lot_size must be a positive integer")
        values={"cash_price":float(payload["cash_price"]),"future_price":float(payload["future_price"]),"gap":float(payload["gap"]),"gap_pct":float(payload["gap_pct"]),"margin_required":float(payload["margin_required"]),"volume":float(payload["volume"]) if payload.get("volume") is not None else None,"oi":float(payload["oi"]) if payload.get("oi") is not None else None,"cash_bid":float(payload["cash_bid"]) if payload.get("cash_bid") is not None else None,"cash_ask":float(payload["cash_ask"]) if payload.get("cash_ask") is not None else None,"future_bid":float(payload["future_bid"]) if payload.get("future_bid") is not None else None,"future_ask":float(payload["future_ask"]) if payload.get("future_ask") is not None else None,"cash_bid_qty":float(payload["cash_bid_qty"]) if payload.get("cash_bid_qty") is not None else None,"cash_ask_qty":float(payload["cash_ask_qty"]) if payload.get("cash_ask_qty") is not None else None,"future_bid_qty":float(payload["future_bid_qty"]) if payload.get("future_bid_qty") is not None else None,"future_ask_qty":float(payload["future_ask_qty"]) if payload.get("future_ask_qty") is not None else None,"charges":float(payload.get("charges",0.0)),"funding_cost":float(payload.get("funding_cost",0.0)),"net_profit":float(payload.get("net_profit",0.0)),"roi_pct":float(payload.get("roi_pct",0.0))}
    except (TypeError,ValueError,OverflowError) as exc: raise ValueError("unsafe Cash-Future resume: open_entry contains invalid values") from exc
    if str(payload["contract_month"])!=selected_contract:raise ValueError("unsafe Cash-Future resume: open_entry contract does not match selected contract")
    if not str(payload["symbol"]).strip():raise ValueError("unsafe Cash-Future resume: open_entry symbol is required")
    if not all(isfinite(v) for v in values.values() if v is not None) or values["margin_required"]<0:raise ValueError("unsafe Cash-Future resume: open_entry contains non-finite or invalid values")
    return CashFutureHistoryPoint(timestamp=timestamp,symbol=str(payload["symbol"]),contract_month=str(payload["contract_month"]),lot_size=lot_raw,expiry_date=expiry,**values)

__all__=["resume_cash_future_strategy"]

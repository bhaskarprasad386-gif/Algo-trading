"""API surface for historical Cash-Future strategy runs."""

from __future__ import annotations
from dataclasses import replace
import inspect
from datetime import date, datetime
from math import isfinite
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from app.backtesting.cash_future_historical_loader import CashFutureHistoricalLoader, CashFutureHistorySelection
from app.backtesting.cash_future_replay_routes import router as cash_future_replay_router
from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.backtesting.reporting import build_cash_future_report
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.ledger import BacktestLedger
from app.backtesting.provenance import provenance_hash
from app.core.config import settings
from app.scanner.cash_future_history import CashFutureHistoryPoint
router = APIRouter(prefix="/api/v1/backtesting/cash-future", tags=["Cash-Future Backtesting"])
router.include_router(cash_future_replay_router)
class StrategyPointRequest(BaseModel):
    timestamp: datetime; symbol: str; contract_month: str; cash_price: float; future_price: float; gap: float; gap_pct: float=0.0; lot_size:int=Field(gt=0); margin_required:float=Field(ge=0); volume:int=Field(default=0,ge=0); oi:int=Field(default=0,ge=0); cash_bid:float|None=Field(default=None,gt=0); cash_ask:float|None=Field(default=None,gt=0); future_bid:float|None=Field(default=None,gt=0); future_ask:float|None=Field(default=None,gt=0); charges:float=Field(default=0.0,ge=0); funding_cost:float=Field(default=0.0,ge=0); expiry_date:date|None=None
class StrategyRunRequest(BaseModel):
    strategy_id:str=Field(min_length=1); strategy_version:str=Field(default="1",min_length=1); start_date:date|None=None; end_date:date|None=None; contract_month:str|None=None; execution_model:str=Field(default="gap",pattern="^(gap|bid_ask)$"); charges_per_trade:float=Field(default=0.0,ge=0); funding_cost_per_trade:float=Field(default=0.0,ge=0); initial_capital:float=Field(default=100_000_000.0,gt=0); points:list[StrategyPointRequest]|None=None; spot_instrument:str|None=None; exchange:str="NFO"; underlying:str|None=None; timeframe:str="1m"; mode:str=Field(default="CURRENT",pattern="^(CURRENT|NEAR)$"); source:str="angelone"; cash_side:str=Field(default="BUY",pattern="^(BUY|SELL)$"); future_side:str=Field(default="SELL",pattern="^(BUY|SELL)$"); cash_lots:int=Field(default=1,gt=0); future_lots:int=Field(default=1,gt=0); stop_loss:float|None=Field(default=None,ge=0); target:float|None=Field(default=None,ge=0); slippage_per_share:float=Field(default=0.0,ge=0)
    @model_validator(mode="after")
    def validate_input_mode(self):
        if self.cash_lots!=self.future_lots: raise ValueError("cash_lots and future_lots must match for paired Cash-Future execution")
        if self.points is None:
            if self.start_date is None or self.end_date is None: raise ValueError("start_date and end_date are required when points are omitted")
            if not self.spot_instrument or not self.underlying: raise ValueError("spot_instrument and underlying are required when points are omitted")
        elif not self.points: raise ValueError("points cannot be empty")
        return self

def _strategy_registry()->dict[str,Any]: return {"gap_threshold":lambda current,history:"BUY" if current.gap>0 else "SELL" if current.gap<0 else "HOLD"}

def _gap_threshold_implementation_hash()->str:
    return provenance_hash({"strategy_id":"gap_threshold","factory_source":inspect.getsource(_build_builder_strategy)})

def _serialise_run(ledger:BacktestLedger,run_id:str)->dict[str,Any]:
    metadata=ledger.run_metadata(run_id)
    if metadata is None: raise HTTPException(status_code=404,detail=f"unknown Cash-Future run: {run_id}")
    signals=tuple(record.payload for record in ledger.records(run_id,"signal")); trades=tuple(record.payload for record in ledger.records(run_id,"trade")); equity=tuple(record.payload for record in ledger.records(run_id,"equity")); initial_capital=float(metadata["initial_capital"])
    realized_pnl=sum(float(trade.get("net_profit",0.0)) for trade in trades)
    final_capital=initial_capital+realized_pnl
    equity_count=ledger.record_count(run_id,"equity")
    final_equity=ledger.record_at(run_id,"equity",equity_count-1).payload if equity_count else None
    final_available_capital=float(final_equity["available_capital"]) if final_equity else initial_capital
    final_reserved_margin=float(final_equity["reserved_margin"]) if final_equity else 0.0
    blocked_entry_count=sum(1 for signal in signals if signal.get("execution_status")=="blocked")
    return {"status":"success","run_id":run_id,"strategy_id":metadata["strategy_id"],"strategy_version":metadata["strategy_version"],"initial_capital":initial_capital,"final_capital":final_capital,"final_available_capital":final_available_capital,"final_reserved_margin":final_reserved_margin,"blocked_entry_count":blocked_entry_count,"net_profit":realized_pnl,"signal_count":ledger.record_count(run_id,"signal"),"trade_count":ledger.record_count(run_id,"trade"),"signals":signals,"trades":trades,"equity_curve":equity}

def _result_page(ledger:BacktestLedger,run_id:str,record_type:str,limit:int,after_id:int|None)->dict[str,Any]:
    metadata=ledger.run_metadata(run_id)
    if metadata is None: raise ValueError(f"unknown run_id: {run_id}")
    page=ledger.record_page(run_id,record_type,limit=limit,after_id=after_id)
    return {"status":"success","run_id":run_id,"record_type":record_type,"data":[dict(record.payload) for record in page.records],"total":ledger.record_count(run_id,record_type),"next_cursor":page.next_cursor}

def _build_builder_strategy(request:StrategyRunRequest):
    orientation=1.0 if (request.cash_side,request.future_side)==("BUY","SELL") else -1.0; stop,target=request.stop_loss,request.target; state={"entry_gap":None}
    def strategy(current:CashFutureHistoryPoint,history:tuple[CashFutureHistoryPoint,...]):
        effective_gap=orientation*float(current.gap); entry_gap=state["entry_gap"]
        if entry_gap is None:
            if effective_gap>0: state["entry_gap"]=effective_gap; return "BUY"
            return "HOLD"
        spread_profit_per_share=entry_gap-effective_gap
        if target is not None and spread_profit_per_share>=target: state["entry_gap"]=None; return "SELL"
        if stop is not None and spread_profit_per_share<=-stop: state["entry_gap"]=None; return "SELL"
        if effective_gap<=0: state["entry_gap"]=None; return "SELL"
        return "HOLD"
    return strategy

def _scale_points(points,lots:int):
    if lots==1:return points
    return (replace(point,lot_size=point.lot_size*lots,margin_required=point.margin_required*lots) for point in points)

@router.post("/strategy-run")
def strategy_run(request:StrategyRunRequest):
    strategy=_strategy_registry().get(request.strategy_id)
    if strategy is None: raise HTTPException(status_code=404,detail=f"unknown Cash-Future strategy: {request.strategy_id}")
    if request.strategy_id=="gap_threshold": strategy=_build_builder_strategy(request)
    catalog=contracts=ledger=None; run_id=f"cash-future-{uuid4().hex}"
    try:
        if request.points is not None:
            point_payload=[point.model_dump(mode="json") for point in request.points]
            points=tuple(CashFutureHistoryPoint(**point.model_dump()) for point in request.points)
            data_source_fingerprint=provenance_hash({"input_identity":"cash_future_points:v1","points":point_payload})
        else:
            assert request.start_date is not None and request.end_date is not None and request.spot_instrument is not None and request.underlying is not None
            catalog=HistoricalCatalog(settings.BACKTEST_DATA_DB); contracts=ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
            selection=CashFutureHistorySelection(spot_instrument=request.spot_instrument,exchange=request.exchange,underlying=request.underlying,start_date=request.start_date,end_date=request.end_date,timeframe=request.timeframe,contract_month=request.contract_month,mode=request.mode,source=request.source)
            loader=CashFutureHistoricalLoader(catalog,contracts)
            points=loader.iter_points(selection)
            data_source_fingerprint=loader.dataset_fingerprint(selection)
        points=_scale_points(points,request.cash_lots); ledger=BacktestLedger(settings.BACKTEST_LEDGER_DB)
        strategy_hash=_gap_threshold_implementation_hash() if request.strategy_id=="gap_threshold" else None
        strategy_config_hash=provenance_hash({"cash_side":request.cash_side,"future_side":request.future_side,"stop_loss":request.stop_loss,"target":request.target})
        result=run_cash_future_strategy(points,strategy,strategy_id=request.strategy_id,strategy_version=request.strategy_version,config=CashFutureStrategyConfig(initial_capital=request.initial_capital,execution_model=request.execution_model,charges_per_trade=request.charges_per_trade,funding_cost_per_trade=request.funding_cost_per_trade,start_date=request.start_date,end_date=request.end_date,contract_month=request.contract_month,cash_side=request.cash_side,future_side=request.future_side,slippage_per_share=request.slippage_per_share),ledger=ledger,run_id=run_id,strategy_hash=strategy_hash,strategy_config_hash=strategy_config_hash,data_source_fingerprint=data_source_fingerprint)
        payload=_serialise_run(ledger,run_id); report=build_cash_future_report(result.initial_capital,payload["trades"],payload["equity_curve"]); profit_factor=report.profit_factor if isfinite(report.profit_factor) else None
        payload["analysis"]={"initial_capital":result.initial_capital,"final_equity":report.final_equity,"net_pnl":payload["net_profit"],"roi":payload["net_profit"]/result.initial_capital,"max_drawdown":report.max_drawdown,"max_drawdown_pct":report.max_drawdown_pct,"win_rate":report.win_rate,"profit_factor":profit_factor,"turnover":report.turnover,"wins":report.wins,"losses":report.losses,"trade_count":payload["trade_count"],"monthly_pnl":dict(report.monthly_pnl),"yearly_pnl":dict(report.yearly_pnl)}
        return payload
    except (ValueError,LookupError) as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    finally:
        if catalog is not None: catalog.close()
        if contracts is not None: contracts.close()
        if ledger is not None: ledger.close()

@router.get("/strategy-run/{run_id}")
def strategy_run_result(run_id:str):
    ledger=BacktestLedger(settings.BACKTEST_LEDGER_DB)
    try:return _serialise_run(ledger,run_id)
    finally:ledger.close()

@router.get("/strategy-run/{run_id}/results/{record_type}")
def strategy_run_result_page(
    run_id: str,
    record_type: str,
    limit: int = Query(default=100, ge=1, le=1000),
    after_id: int | None = Query(default=None, ge=0),
):
    ledger=BacktestLedger(settings.BACKTEST_LEDGER_DB)
    try:
        return _result_page(ledger,run_id,record_type,limit,after_id)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "unknown run_id" in str(exc) else 422, detail=str(exc)) from exc
    finally:
        ledger.close()

__all__=["router"]
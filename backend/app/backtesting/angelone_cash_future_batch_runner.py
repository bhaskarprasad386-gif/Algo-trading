"""Durable sequential execution for bounded Angel One Cash-Future stock batches."""
from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Mapping
import hashlib
import json
from .angelone_cash_future_runner import AngelOneCashFutureRunConfig, iter_cash_future_stock_batches, run_angelone_cash_future_history
from .cash_future_universe import CashFutureFnoUniverse
from .historical_job_store import HistoricalJobStore

@dataclass(frozen=True)
class AngelOneCashFutureBatchResult:
    batch_index:int; stock_underlyings:tuple[str,...]; skipped:bool; result:object|None

def _batch_job_id(run_id:str,batch_index:int)->str: return f"{run_id}:cash-future-stock-batch:{batch_index}"

def _master_rows_fingerprint(rows:tuple[Mapping[str,object],...])->str:
    payload=json.dumps([dict(row) for row in rows],sort_keys=True,separators=(",",":"),default=str)
    return hashlib.sha256(payload.encode()).hexdigest()

def _batch_fingerprint(*,stock_underlyings:tuple[str,...],indices:tuple[str,...],start:datetime,end:datetime,batch_size:int,config:AngelOneCashFutureRunConfig,master_rows_fingerprint:str,margin_required:float,materialize_batch_size:int)->str:
    return HistoricalJobStore.fingerprint(({"stock_underlyings":stock_underlyings,"indices":indices,"start":start.isoformat(),"end":end.isoformat(),"batch_size":batch_size,"interval_ns":config.interval_ns,"max_request_ns":config.max_request_ns,"timeframe":config.timeframe,"mode":config.mode.strip().upper(),"chunk_days":config.chunk_days,"retry_attempts":config.retry_attempts,"retry_delay_seconds":config.retry_delay_seconds,"max_repair_passes":config.max_repair_passes,"max_stock_underlyings":config.max_stock_underlyings,"stock_batch_offset":config.stock_batch_offset,"master_rows_sha256":master_rows_fingerprint,"margin_required":margin_required,"materialize_batch_size":materialize_batch_size},))

def run_angelone_cash_future_history_in_batches(*,ingestion,contract_master,universe:CashFutureFnoUniverse,master_rows:Iterable[Mapping[str,object]],start:datetime,end:datetime,spot_sessions_by_underlying,db,catalog,config:AngelOneCashFutureRunConfig,batch_size:int=10,job_store:HistoricalJobStore,run_id:str,future_sessions_by_instrument=None,auth=None,limiter=None,retry_policy=None,on_progress:Callable[[str,object],None]|None=None,coverage_store=None,margin_required:float=0.0,materialize_batch_size:int=1000)->tuple[AngelOneCashFutureBatchResult,...]:
    if batch_size<1: raise ValueError("batch_size must be positive")
    if not run_id.strip(): raise ValueError("run_id is required")
    if start>=end: raise ValueError("start must be before end")
    if margin_required<0: raise ValueError("margin_required must not be negative")
    if materialize_batch_size<1: raise ValueError("materialize_batch_size must be positive")
    master_rows=tuple(master_rows); master_fp=_master_rows_fingerprint(master_rows)
    batches=tuple(iter_cash_future_stock_batches(universe,batch_size=batch_size)); results=[]
    for batch_index,batch in enumerate(batches):
        underlyings=tuple(batch.stock_underlyings); job_id=_batch_job_id(run_id,batch_index)
        fingerprint=_batch_fingerprint(stock_underlyings=underlyings,indices=tuple(item.underlying for item in batch.indices),start=start,end=end,batch_size=batch_size,config=config,master_rows_fingerprint=master_fp,margin_required=margin_required,materialize_batch_size=materialize_batch_size)
        try: job=job_store.get(job_id)
        except KeyError:
            job_store.create(job_id=job_id,run_id=run_id,plan_fingerprint=fingerprint,total_chunks=1,plan_metadata=({"batch_index":batch_index,"stock_underlyings":underlyings,"indices":tuple(item.underlying for item in batch.indices),"start":start.isoformat(),"end":end.isoformat(),"batch_size":batch_size,"master_rows_sha256":master_fp,"margin_required":margin_required,"materialize_batch_size":materialize_batch_size},)); job=job_store.get(job_id)
        if job.plan_fingerprint!=fingerprint: raise ValueError(f"batch plan changed for durable job {job_id}")
        if job.state=="cancelled": break
        if job.state=="completed":
            results.append(AngelOneCashFutureBatchResult(batch_index,underlyings,True,None)); continue
        job_store.recover_running_chunks(job_id)
        if job_store.get(job_id).state=="cancelled": break
        try:
            # Keep the outer batch chunk pending while acquisition/materialization runs.
            # Only mark it completed after the returned pipeline passes the authoritative
            # readiness gate. If the worker crashes, restart sees a pending/recoverable
            # outer chunk and safely retries the idempotent inner acquisition jobs.
            batch_config=replace(config,max_stock_underlyings=None,stock_batch_offset=0)
            result=run_angelone_cash_future_history(ingestion=ingestion,contract_master=contract_master,universe=batch,master_rows=master_rows,start=start,end=end,spot_sessions_by_underlying=spot_sessions_by_underlying,db=db,catalog=catalog,future_sessions_by_instrument=future_sessions_by_instrument,job_store=job_store,run_id=job_id,config=batch_config,auth=auth,limiter=limiter,retry_policy=retry_policy,on_progress=on_progress,coverage_store=coverage_store,margin_required=margin_required,batch_size=materialize_batch_size)
            result.require_backtest_ready()
            if job_store.get(job_id).state=="cancelled": break
            job_store.complete_chunk(job_id,0)
            job_store.finish(job_id)
            results.append(AngelOneCashFutureBatchResult(batch_index,underlyings,False,result))
        except Exception as exc:
            if job_store.get(job_id).state=="cancelled": break
            job_store.fail_chunk(job_id,0,str(exc),recoverable=True); job_store.finish(job_id); raise
    return tuple(results)

__all__=["AngelOneCashFutureBatchResult","run_angelone_cash_future_history_in_batches"]
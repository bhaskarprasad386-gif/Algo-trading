"""FastAPI endpoints for durable historical-download and backtest progress."""

from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException

from .backtest_ledger import BacktestTradeLedger
from .download_status_api import download_status_payload
from .historical_download_status import HistoricalDownloadStatusStore


def create_download_status_router(store: HistoricalDownloadStatusStore) -> APIRouter:
    """Create read-only historical-download and durable-backtest routes."""
    router = APIRouter(prefix="/api/v1/backtesting", tags=["backtesting"])

    @router.get("/cash-future/downloads/{job_id}")
    def get_cash_future_download_status(job_id: str) -> dict:
        try:
            return download_status_payload(store, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="download job not found") from exc

    @router.get("/runs")
    def list_backtest_runs() -> dict:
        """List durable backtest run summaries without loading trade history."""
        path = os.getenv("BACKTEST_LEDGER_DB", "./backtest_ledger.sqlite3")
        with BacktestTradeLedger(path) as ledger:
            runs = [ledger.run_summary(run_id) for run_id in ledger.run_ids()]
        return {"runs": [run for run in runs if run is not None]}

    @router.get("/runs/{run_id}")
    def get_backtest_run(run_id: str) -> dict:
        """Return one durable backtest summary plus trade-count/P&L aggregates."""
        path = os.getenv("BACKTEST_LEDGER_DB", "./backtest_ledger.sqlite3")
        with BacktestTradeLedger(path) as ledger:
            summary = ledger.run_summary(run_id)
            if summary is None:
                raise HTTPException(status_code=404, detail="backtest run not found")
            summary["trade_count"] = ledger.count(run_id)
            summary["trade_net_pnl"] = ledger.net_pnl(run_id)
            return summary

    return router

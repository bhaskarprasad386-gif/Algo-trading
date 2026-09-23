"""FastAPI routes for read-only Universal backtest results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .universal_result_service import (
    UniversalResultService,
    UniversalRunNotCompletedError,
    UniversalRunNotFoundError,
)


def create_universal_result_router(service: UniversalResultService) -> APIRouter:
    """Create the read-only Universal result API over an injected service."""
    router = APIRouter(
        prefix="/api/v1/backtesting/universal",
        tags=["Universal Backtesting"],
    )

    def not_found(exc: UniversalRunNotFoundError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(exc))

    def invalid(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=422, detail=str(exc))

    @router.get("/runs/{run_id}")
    def get_universal_run(run_id: str) -> dict:
        try:
            return service.summary(run_id)
        except UniversalRunNotFoundError as exc:
            raise not_found(exc) from exc
        except UniversalRunNotCompletedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/events")
    def get_universal_events(
        run_id: str,
        limit: int = Query(default=100, ge=1),
        after_sequence: int = Query(default=-1, ge=-1),
    ) -> dict:
        try:
            return service.events(run_id, limit=limit, after_sequence=after_sequence)
        except UniversalRunNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise invalid(exc) from exc

    @router.get("/runs/{run_id}/fills")
    def get_universal_fills(
        run_id: str,
        limit: int = Query(default=100, ge=1),
        after_sequence: int = Query(default=-1, ge=-1),
    ) -> dict:
        try:
            return service.fills(run_id, limit=limit, after_sequence=after_sequence)
        except UniversalRunNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise invalid(exc) from exc

    @router.get("/runs/{run_id}/trades")
    def get_universal_trades(
        run_id: str,
        limit: int = Query(default=100, ge=1),
        after_sequence: int = Query(default=-1, ge=-1),
    ) -> dict:
        try:
            return service.trades(run_id, limit=limit, after_sequence=after_sequence)
        except UniversalRunNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise invalid(exc) from exc

    @router.get("/runs/{run_id}/equity")
    def get_universal_equity(
        run_id: str,
        limit: int = Query(default=100, ge=1),
        after_timestamp_ns: int | None = Query(default=None),
        after_equity_id: int | None = Query(default=None),
    ) -> dict:
        if (after_timestamp_ns is None) != (after_equity_id is None):
            raise HTTPException(
                status_code=422,
                detail="after_timestamp_ns and after_equity_id must be supplied together",
            )
        try:
            return service.equity(
                run_id,
                limit=limit,
                after_timestamp_ns=-1 if after_timestamp_ns is None else after_timestamp_ns,
                after_equity_id=-1 if after_equity_id is None else after_equity_id,
            )
        except UniversalRunNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise invalid(exc) from exc

    return router

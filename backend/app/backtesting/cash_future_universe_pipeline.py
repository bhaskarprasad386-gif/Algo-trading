"""Run all-stock Cash-Future acquisition and materialize its durable backtest history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .cash_future_historical_acquisition import CashFutureAcquisitionProgress, CashFutureHistoricalAcquisitionService
from .cash_future_universe import CashFutureFnoUniverse
from .cash_future_universe_acquisition import CashFutureUniverseAcquisitionResult, acquire_cash_future_universe
from .cash_future_universe_download_plan import CashFutureUniverseDownloadJob, CashFutureUniverseDownloadPlan
from .cash_future_universe_materializer import materialize_cash_future_universe_history
from .cash_future_coverage_manifest_store import CashFutureCoverageManifestStore
from .historical_catalog import HistoricalCatalog
from .historical_job_store import HistoricalJobStore
from .historical_sync import HistoricalSyncPlan
from .provider_retry import ProviderRetryPolicy
from .session_gap_planner import SessionWindow
from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_backtest import BacktestConfig
from app.scanner.cash_future_coverage_store import (
    audit_persisted_cash_future_data_quality,
    build_persisted_cash_future_coverage,
    run_persisted_cash_future_backtest,
)


@dataclass(frozen=True)
class CashFutureUniversePipelineResult:
    acquisition: CashFutureUniverseAcquisitionResult
    materialized_rows: int
    materialized_underlyings: tuple[str, ...] = ()
    materialized_requests: tuple[tuple[str, int, int], ...] = ()
    coverage_store: CashFutureCoverageManifestStore | None = None
    coverage_source: str = "angelone"
    coverage_timeframe: str = "1m"

    @property
    def backtest_ready(self) -> bool:
        """Return whether every acquired request is completely materialized and manifested."""
        acquired_underlyings = tuple(
            sorted(_underlying_from_cash_instrument(result.queue.spot.instrument)
                   for result in self.acquisition.results)
        )
        if not (
            self.materialized_rows > 0
            and acquired_underlyings
            and all(result.coverage.complete for result in self.acquisition.results)
            and tuple(sorted(set(self.materialized_underlyings))) == tuple(sorted(set(acquired_underlyings)))
        ):
            return False

        expected_requests = tuple(
            (request.instrument, request.start_ns, request.end_ns)
            for result in self.acquisition.results
            for request in getattr(result.queue, "futures", ())
        )
        if expected_requests and not set(expected_requests).issubset(set(self.materialized_requests)):
            return False

        if self.coverage_store is None:
            return True
        requested_ranges = tuple(
            (request.instrument, request.start_ns, request.end_ns)
            for result in self.acquisition.results
            for request in result.queue.all_requests
        )
        return self.coverage_store.is_complete_for_requests(
            source=self.coverage_source,
            timeframe=self.coverage_timeframe,
            requests=requested_ranges,
        )

    def require_backtest_ready(self) -> None:
        """Block backtesting until every acquired request is complete and materialized."""
        if not self.backtest_ready:
            raise LookupError(
                "Cash-Future historical acquisition/materialization is incomplete; backtest blocked"
            )

    def run_backtest(
        self,
        db: Session,
        config: BacktestConfig,
        *,
        symbol: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page_size: int = 1000,
        result_ledger=None,
    ) -> dict:
        """Run a persisted Cash-Future backtest behind coverage and quality gates."""
        self.require_backtest_ready()
        coverage = build_persisted_cash_future_coverage(
            db,
            symbol=symbol,
            contract_month=config.contract_month,
            start=start,
            end=end,
            page_size=page_size,
        )
        quality = audit_persisted_cash_future_data_quality(
            db,
            symbol=symbol,
            contract_month=config.contract_month,
            start=start,
            end=end,
            page_size=page_size,
        )
        return run_persisted_cash_future_backtest(
            db,
            config,
            symbol=symbol,
            start=start,
            end=end,
            page_size=page_size,
            coverage_report=coverage,
            quality_report=quality,
            result_ledger=result_ledger,
        )


def _underlying_from_cash_instrument(instrument: str) -> str:
    parts = instrument.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid cash instrument: {instrument}")
    return parts[2].rsplit("-", 1)[0].upper()


def _contract_month_for_request(universe: CashFutureFnoUniverse, *, underlying: str, request) -> str:
    parts = request.instrument.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid future instrument: {request.instrument}")
    for item in universe.stocks:
        if item.underlying.upper() == underlying.upper() and item.future_token == parts[1]:
            return item.contract_month
    raise ValueError(f"download plan future has no universe metadata: {request.instrument}")


def _timeframe_interval_ns(timeframe: str) -> int:
    """Return the fixed bar cadence used for persisted session reconciliation."""
    normalized = timeframe.strip().lower()
    units = {"m": 60, "h": 3600, "d": 86400}
    if not normalized or normalized[-1] not in units:
        raise ValueError(f"unsupported fixed-cadence timeframe: {timeframe}")
    try:
        amount = int(normalized[:-1])
    except ValueError as exc:
        raise ValueError(f"unsupported fixed-cadence timeframe: {timeframe}") from exc
    if amount <= 0:
        raise ValueError(f"unsupported fixed-cadence timeframe: {timeframe}")
    return amount * units[normalized[-1]] * 1_000_000_000


def _session_expected_timestamps(
    sessions: tuple[SessionWindow, ...],
    *,
    start_ns: int,
    end_ns: int,
    interval_ns: int,
):
    """Yield expected fixed-cadence timestamps without bridging session boundaries."""
    for session in sorted(sessions, key=lambda item: item.start_ns):
        cursor = max(start_ns, session.start_ns)
        session_end = min(end_ns, session.end_ns)
        if cursor > session_end:
            continue
        # Session windows are inclusive and their starts define the cadence anchor.
        offset = (cursor - session.start_ns) % interval_ns
        if offset:
            cursor += interval_ns - offset
        while cursor <= session_end:
            yield cursor
            cursor += interval_ns


def _request_has_materialized_rows(
    db: Session,
    *,
    symbol: str,
    contract_month: str,
    request,
    sessions: tuple[SessionWindow, ...] | None = None,
    timeframe: str = "1m",
) -> bool:
    """Require persisted rows to cover every expected session timestamp in the request."""
    start = datetime.fromtimestamp(request.start_ns / 1_000_000_000)
    end = datetime.fromtimestamp(request.end_ns / 1_000_000_000)
    stmt = select(
        func.min(CashFutureHistory.timestamp),
        func.max(CashFutureHistory.timestamp),
    ).where(
        CashFutureHistory.symbol == symbol,
        CashFutureHistory.contract_month == contract_month,
        CashFutureHistory.timestamp >= start,
        CashFutureHistory.timestamp <= end,
    )
    first, last = db.execute(stmt).one()
    if first is None or last is None or first > start or last < end:
        return False
    if sessions is None:
        return True

    interval_ns = _timeframe_interval_ns(timeframe)
    expected = _session_expected_timestamps(
        sessions,
        start_ns=request.start_ns,
        end_ns=request.end_ns,
        interval_ns=interval_ns,
    )
    observed_stmt = (
        select(CashFutureHistory.timestamp)
        .where(
            CashFutureHistory.symbol == symbol,
            CashFutureHistory.contract_month == contract_month,
            CashFutureHistory.timestamp >= start,
            CashFutureHistory.timestamp <= end,
        )
        .order_by(CashFutureHistory.timestamp)
        .distinct()
        .execution_options(stream_results=True)
    )
    observed = iter(db.execute(observed_stmt).scalars())
    current = next(observed, None)
    for expected_ns in expected:
        expected_dt = datetime.fromtimestamp(expected_ns / 1_000_000_000)
        while current is not None and current < expected_dt:
            current = next(observed, None)
        if current != expected_dt:
            return False
    return True


def acquire_and_materialize_cash_future_universe(
    *,
    service: CashFutureHistoricalAcquisitionService,
    universe: CashFutureFnoUniverse,
    master_rows: Iterable[Mapping[str, object]],
    start: datetime,
    end: datetime,
    spot_sessions_by_underlying: Mapping[str, tuple[SessionWindow, ...]],
    db: Session,
    catalog: HistoricalCatalog,
    future_sessions_by_instrument: Mapping[str, tuple[SessionWindow, ...]] | None = None,
    timeframe: str = "1m",
    source: str = "angelone",
    mode: str = "BOTH",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    retry_policy: ProviderRetryPolicy | None = None,
    max_repair_passes: int = 3,
    job_store: HistoricalJobStore | None = None,
    run_id: str | None = None,
    job_id_prefix: str = "cash-future",
    on_progress: Callable[[str, CashFutureAcquisitionProgress], None] | None = None,
    coverage_store: CashFutureCoverageManifestStore | None = None,
    margin_required: float = 0.0,
    batch_size: int = 1000,
) -> CashFutureUniversePipelineResult:
    """Acquire the planned universe durably, then materialize downloaded bars."""
    acquisition = acquire_cash_future_universe(
        service=service,
        universe=universe,
        master_rows=master_rows,
        start=start,
        end=end,
        spot_sessions_by_underlying=spot_sessions_by_underlying,
        future_sessions_by_instrument=future_sessions_by_instrument,
        timeframe=timeframe,
        source=source,
        mode=mode,
        retry_attempts=retry_attempts,
        retry_delay_seconds=retry_delay_seconds,
        retry_policy=retry_policy,
        max_repair_passes=max_repair_passes,
        job_store=job_store,
        run_id=run_id,
        job_id_prefix=job_id_prefix,
        on_progress=on_progress,
        coverage_store=coverage_store,
    )

    materialized_rows = 0
    materialized_underlyings: list[str] = []
    materialized_requests: list[tuple[str, int, int]] = []
    for result in acquisition.results:
        underlying = _underlying_from_cash_instrument(result.queue.spot.instrument)
        job = CashFutureUniverseDownloadJob(
            underlying=underlying,
            spot=result.queue.spot,
            futures=result.queue.futures,
        )
        plan = CashFutureUniverseDownloadPlan(
            jobs=(job,),
            plan=HistoricalSyncPlan(job.all_requests),
        )
        rows = materialize_cash_future_universe_history(
            db,
            catalog,
            download_plan=plan,
            universe=universe,
            source=source,
            timeframe=timeframe,
            margin_required=margin_required,
            batch_size=batch_size,
        )
        materialized_rows += rows
        if rows > 0:
            materialized_underlyings.append(underlying)

        spot_sessions = spot_sessions_by_underlying.get(underlying, ())
        if _request_has_materialized_rows(
            db,
            symbol=underlying,
            contract_month=_contract_month_for_request(universe, underlying=underlying, request=result.queue.spot),
            request=result.queue.spot,
            sessions=spot_sessions,
            timeframe=timeframe,
        ):
            materialized_requests.append((result.queue.spot.instrument, result.queue.spot.start_ns, result.queue.spot.end_ns))

        for request in result.queue.futures:
            contract_month = _contract_month_for_request(
                universe,
                underlying=underlying,
                request=request,
            )
            future_sessions = (future_sessions_by_instrument or {}).get(request.instrument, ())
            if _request_has_materialized_rows(
                db,
                symbol=underlying,
                contract_month=contract_month,
                request=request,
                sessions=future_sessions,
                timeframe=timeframe,
            ):
                materialized_requests.append((request.instrument, request.start_ns, request.end_ns))

    return CashFutureUniversePipelineResult(
        acquisition,
        materialized_rows,
        tuple(sorted(set(materialized_underlyings))),
        tuple(sorted(set(materialized_requests))),
        coverage_store=coverage_store,
        coverage_source=source,
        coverage_timeframe=timeframe,
    )


__all__ = ["CashFutureUniversePipelineResult", "acquire_and_materialize_cash_future_universe"]
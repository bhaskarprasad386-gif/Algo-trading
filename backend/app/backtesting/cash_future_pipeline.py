"""End-to-end historical Cash-Future backtest orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .cash_future_download_queue import CashFutureDownloadQueue
from .cash_future_readiness import CashFutureReadinessGate, CashFutureReadinessReport
from .cash_future_replay import (
    CashFutureBar,
    CashFutureBothReplayTrade,
    CashFutureContractLock,
    CashFutureMultiLegBar,
    CashFutureReplayRunner,
    CashFutureReplayTrade,
)
from .cash_future_result_store import CashFutureResultStore
from .historical_catalog import HistoricalCatalog


@dataclass(frozen=True)
class CashFuturePipelineResult:
    mode: str
    readiness: CashFutureReadinessReport
    contract_instruments: Mapping[str, str]
    bars: tuple[CashFutureBar | CashFutureMultiLegBar, ...]
    trades: tuple[CashFutureReplayTrade | CashFutureBothReplayTrade, ...]
    persisted_trade_count: int = 0
    persisted_gross_pnl: float = 0.0
    persisted_net_pnl: float = 0.0


class CashFutureBacktestPipeline:
    """Run readiness -> historical contract resolution -> sync -> locked replay -> durable results."""

    def __init__(self, *, contract_catalog, historical_catalog: HistoricalCatalog,
                 readiness: CashFutureReadinessGate | None = None,
                 result_store: CashFutureResultStore | None = None) -> None:
        self.contract_catalog = contract_catalog
        self.historical_catalog = historical_catalog
        self.readiness = readiness or CashFutureReadinessGate(
            contract_catalog=contract_catalog, historical_catalog=historical_catalog
        )
        self.result_store = result_store

    @staticmethod
    def _instrument(contract: Any) -> str:
        if isinstance(contract, str):
            return contract
        if isinstance(contract, Mapping):
            for key in ("instrument", "symbol", "tradingsymbol", "token"):
                value = contract.get(key)
                if value is not None and str(value).strip():
                    return str(value)
        for key in ("instrument", "symbol", "tradingsymbol", "token"):
            value = getattr(contract, key, None)
            if value is not None and str(value).strip():
                return str(value)
        raise LookupError("resolved historical contract has no instrument identity")

    @staticmethod
    def _lot_size(contract: Any, default: int) -> int:
        value = contract.get("lot_size") if isinstance(contract, Mapping) else getattr(contract, "lot_size", None)
        value = default if value is None else int(value)
        if value <= 0:
            raise ValueError("lot_size must be positive")
        return value

    @staticmethod
    def _payload_price(record) -> float:
        for key in ("close", "ltp", "price", "last_price"):
            value = record.payload.get(key)
            if value is not None and float(value) > 0:
                return float(value)
        raise ValueError(f"historical record has no positive price: {record.instrument} @ {record.timestamp_ns}")

    def _resolve(self, *, exchange: str, underlying: str, as_of: datetime, mode: str):
        return self.contract_catalog.resolve(exchange=exchange, underlying=underlying, as_of=as_of.date(), mode=mode)

    def _records(self, *, source: str, instrument: str, timeframe: str, start_ns: int, end_ns: int) -> tuple:
        records = self.historical_catalog.records(source=source, instrument=instrument, timeframe=timeframe)
        filtered = tuple(r for r in records if start_ns <= r.timestamp_ns <= end_ns)
        if not filtered:
            raise LookupError(f"no historical records for {instrument} ({timeframe}) in requested range")
        return filtered

    def _sync_one(self, spot_records: tuple, future_records: tuple) -> tuple[CashFutureBar, ...]:
        spot = {r.timestamp_ns: self._payload_price(r) for r in spot_records}
        future = {r.timestamp_ns: self._payload_price(r) for r in future_records}
        timestamps = sorted(set(spot) & set(future))
        if not timestamps:
            raise LookupError("no synchronized spot/future timestamps")
        return tuple(CashFutureBar(ts, spot[ts], future[ts]) for ts in timestamps)

    def _sync_both(self, spot_records: tuple, current_records: tuple, near_records: tuple,
                   current_instrument: str, near_instrument: str) -> tuple[CashFutureMultiLegBar, ...]:
        spot = {r.timestamp_ns: self._payload_price(r) for r in spot_records}
        current = {r.timestamp_ns: self._payload_price(r) for r in current_records}
        near = {r.timestamp_ns: self._payload_price(r) for r in near_records}
        timestamps = sorted(set(spot) & set(current) & set(near))
        if not timestamps:
            raise LookupError("no synchronized spot/current/near timestamps")
        return tuple(CashFutureMultiLegBar(ts, spot[ts], {current_instrument: current[ts], near_instrument: near[ts]}) for ts in timestamps)

    def run(
        self, *, exchange: str, underlying: str, start: datetime, end: datetime, mode: str,
        source: str, timeframe: str = "1m", interval_ns: int = 60_000_000_000,
        queue: CashFutureDownloadQueue, spot_instrument: str, quantity: int,
        default_lot_size: int, entry_timestamp_ns: int, exit_timestamp_ns: int,
        entry_timestamps: tuple[int, ...] | None = None, exit_timestamps: tuple[int, ...] | None = None,
        spot_sessions: tuple = (), future_sessions: dict[str, tuple] | None = None,
        charges: Mapping[str, float] | None = None, run_id: str | None = None,
    ) -> CashFuturePipelineResult:
        normalized = mode.upper()
        if normalized not in {"CURRENT", "NEAR", "BOTH"}:
            raise ValueError("mode must be CURRENT, NEAR or BOTH")
        if end < start or entry_timestamp_ns >= exit_timestamp_ns:
            raise ValueError("invalid backtest range or entry/exit timestamps")
        entries = tuple(entry_timestamps or (entry_timestamp_ns,))
        exits = tuple(exit_timestamps or (exit_timestamp_ns,))
        if len(entries) != len(exits):
            raise ValueError("entry and exit timestamps must have equal length")
        if any(ts < start.timestamp_ns() for ts in entries) or any(ts > end.timestamp_ns() for ts in exits):
            raise ValueError("trade timestamps must be inside requested backtest range")

        readiness = self.readiness.require_complete(
            exchange=exchange, underlying=underlying, end=end, queue=queue, mode=normalized,
            interval_ns=interval_ns, spot_sessions=spot_sessions, future_sessions=future_sessions,
        )
        entry_dt = datetime.fromtimestamp(entries[0] / 1_000_000_000, tz=timezone.utc)
        spot_records = self._records(source=source, instrument=spot_instrument, timeframe=timeframe,
                                     start_ns=start.timestamp_ns(), end_ns=end.timestamp_ns())
        runner = CashFutureReplayRunner()

        if normalized == "BOTH":
            current = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode="CURRENT")
            near = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode="NEAR")
            current_instrument, near_instrument = self._instrument(current), self._instrument(near)
            current_lot, near_lot = self._lot_size(current, default_lot_size), self._lot_size(near, default_lot_size)
            if current_lot != near_lot:
                raise ValueError("CURRENT and NEAR lot sizes must match for BOTH replay")
            bars = self._sync_both(
                spot_records,
                self._records(source=source, instrument=current_instrument, timeframe=timeframe,
                              start_ns=start.timestamp_ns(), end_ns=end.timestamp_ns()),
                self._records(source=source, instrument=near_instrument, timeframe=timeframe,
                              start_ns=start.timestamp_ns(), end_ns=end.timestamp_ns()),
                current_instrument, near_instrument,
            )
            lock = CashFutureContractLock(mode="BOTH", contracts={"CURRENT": current_instrument, "NEAR": near_instrument})
            trades = runner.run_both(bars, entry_timestamps=entries, exit_timestamps=exits,
                                     contract_lock=lock, lot_size=current_lot, quantity=quantity, charges=charges)
            instruments = {"CURRENT": current_instrument, "NEAR": near_instrument}
        else:
            contract = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode=normalized)
            future_instrument = self._instrument(contract)
            bars = self._sync_one(
                spot_records,
                self._records(source=source, instrument=future_instrument, timeframe=timeframe,
                              start_ns=start.timestamp_ns(), end_ns=end.timestamp_ns()),
            )
            lock = CashFutureContractLock(mode=normalized, contracts={normalized: future_instrument})
            trades = runner.run(bars, entry_timestamps=entries, exit_timestamps=exits,
                                future_instrument=future_instrument, lot_size=self._lot_size(contract, default_lot_size),
                                quantity=quantity, charges=charges, contract_lock=lock, contract_leg=normalized)
            instruments = {normalized: future_instrument}

        persisted_trade_count = persisted_gross_pnl = persisted_net_pnl = 0.0
        if self.result_store is not None:
            if not run_id or not run_id.strip():
                raise ValueError("run_id is required when result_store is configured")
            self.result_store.append_many(run_id, trades)
            summary = self.result_store.summary(run_id)
            persisted_trade_count = summary.trade_count
            persisted_gross_pnl = summary.gross_pnl
            persisted_net_pnl = summary.net_pnl

        return CashFuturePipelineResult(
            normalized, readiness, instruments, bars, trades,
            int(persisted_trade_count), persisted_gross_pnl, persisted_net_pnl,
        )

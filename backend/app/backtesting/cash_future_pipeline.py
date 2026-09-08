"""End-to-end historical Cash-Future backtest orchestration.

The pipeline deliberately depends on injected contract/data catalogs so live broker
implementations are not embedded in the backtest engine. Readiness is checked before
any replay; historical contract identities are resolved from the replay date; prices
are synchronized by timestamp; and selected legs are locked for the trade lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .cash_future_readiness import CashFutureReadinessGate, CashFutureReadinessReport
from .cash_future_replay import (
    CashFutureBar,
    CashFutureBothReplayTrade,
    CashFutureContractLock,
    CashFutureMultiLegBar,
    CashFutureReplayRunner,
    CashFutureReplayTrade,
)
from .cash_future_download_queue import CashFutureDownloadQueue
from .cash_future_pnl import CashFutureTrade
from .historical_catalog import HistoricalCatalog


@dataclass(frozen=True)
class CashFuturePipelineResult:
    mode: str
    readiness: CashFutureReadinessReport
    contract_instruments: Mapping[str, str]
    bars: tuple[CashFutureBar | CashFutureMultiLegBar, ...]
    trade: CashFutureReplayTrade | CashFutureBothReplayTrade


class CashFutureBacktestPipeline:
    """Run readiness -> historical contract resolution -> sync -> locked replay."""

    def __init__(
        self,
        *,
        contract_catalog,
        historical_catalog: HistoricalCatalog,
        readiness: CashFutureReadinessGate | None = None,
    ) -> None:
        self.contract_catalog = contract_catalog
        self.historical_catalog = historical_catalog
        self.readiness = readiness or CashFutureReadinessGate(
            contract_catalog=contract_catalog,
            historical_catalog=historical_catalog,
        )

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
        if value is None:
            value = default
        value = int(value)
        if value <= 0:
            raise ValueError("lot_size must be positive")
        return value

    @staticmethod
    def _payload_price(record) -> float:
        payload = record.payload
        for key in ("close", "ltp", "price", "last_price"):
            value = payload.get(key)
            if value is not None:
                value = float(value)
                if value > 0:
                    return value
        raise ValueError(f"historical record has no positive price: {record.instrument} @ {record.timestamp_ns}")

    def _resolve(self, *, exchange: str, underlying: str, as_of: datetime, mode: str):
        day = as_of.astimezone(timezone.utc).astimezone(timezone.utc).date()
        return self.contract_catalog.resolve(exchange=exchange, underlying=underlying, as_of=day, mode=mode)

    def _records(self, *, source: str, instrument: str, timeframe: str) -> tuple:
        records = self.historical_catalog.records(source=source, instrument=instrument, timeframe=timeframe)
        if not records:
            raise LookupError(f"no historical records for {instrument} ({timeframe})")
        return records

    def _sync_one(self, *, spot_records: tuple, future_records: tuple, future_instrument: str) -> tuple[CashFutureBar, ...]:
        spot = {r.timestamp_ns: self._payload_price(r) for r in spot_records}
        future = {r.timestamp_ns: self._payload_price(r) for r in future_records}
        timestamps = sorted(set(spot).intersection(future))
        if not timestamps:
            raise LookupError("no synchronized spot/future timestamps")
        return tuple(CashFutureBar(ts, spot[ts], future[ts]) for ts in timestamps)

    def _sync_both(self, *, spot_records: tuple, current_records: tuple, near_records: tuple, current_instrument: str, near_instrument: str) -> tuple[CashFutureMultiLegBar, ...]:
        spot = {r.timestamp_ns: self._payload_price(r) for r in spot_records}
        current = {r.timestamp_ns: self._payload_price(r) for r in current_records}
        near = {r.timestamp_ns: self._payload_price(r) for r in near_records}
        timestamps = sorted(set(spot).intersection(current).intersection(near))
        if not timestamps:
            raise LookupError("no synchronized spot/current/near timestamps")
        return tuple(
            CashFutureMultiLegBar(ts, spot[ts], {current_instrument: current[ts], near_instrument: near[ts]})
            for ts in timestamps
        )

    def run(
        self,
        *,
        exchange: str,
        underlying: str,
        start: datetime,
        end: datetime,
        mode: str,
        source: str,
        timeframe: str = "1m",
        interval_ns: int = 60_000_000_000,
        queue: CashFutureDownloadQueue,
        spot_instrument: str,
        quantity: int,
        default_lot_size: int,
        entry_timestamp_ns: int,
        exit_timestamp_ns: int,
        charges: Mapping[str, float] | None = None,
    ) -> CashFuturePipelineResult:
        normalized = mode.upper()
        if normalized not in {"CURRENT", "NEAR", "BOTH"}:
            raise ValueError("mode must be CURRENT, NEAR or BOTH")
        if end < start:
            raise ValueError("end must not precede start")
        if entry_timestamp_ns >= exit_timestamp_ns:
            raise ValueError("entry_timestamp_ns must precede exit_timestamp_ns")

        readiness = self.readiness.require_complete(
            exchange=exchange,
            underlying=underlying,
            end=end,
            queue=queue,
            mode=normalized,
            interval_ns=interval_ns,
            spot_sessions=(),
            future_sessions=(),
        )

        entry_dt = datetime.fromtimestamp(entry_timestamp_ns / 1_000_000_000, tz=timezone.utc)
        spot_records = self._records(source=source, instrument=spot_instrument, timeframe=timeframe)

        if normalized == "BOTH":
            current = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode="CURRENT")
            near = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode="NEAR")
            current_instrument = self._instrument(current)
            near_instrument = self._instrument(near)
            current_records = self._records(source=source, instrument=current_instrument, timeframe=timeframe)
            near_records = self._records(source=source, instrument=near_instrument, timeframe=timeframe)
            bars = self._sync_both(
                spot_records=spot_records,
                current_records=current_records,
                near_records=near_records,
                current_instrument=current_instrument,
                near_instrument=near_instrument,
            )
            lock = CashFutureContractLock("BOTH", {"CURRENT": current_instrument, "NEAR": near_instrument})
            runner = CashFutureReplayRunner(charges=charges or {})
            trade = runner.run_both(
                bars,
                entry_timestamp_ns=entry_timestamp_ns,
                exit_timestamp_ns=exit_timestamp_ns,
                quantity=quantity,
                lot_size=self._lot_size(current, default_lot_size),
                contract_lock=lock,
            )
            instruments = {"CURRENT": current_instrument, "NEAR": near_instrument}
        else:
            contract = self._resolve(exchange=exchange, underlying=underlying, as_of=entry_dt, mode=normalized)
            future_instrument = self._instrument(contract)
            future_records = self._records(source=source, instrument=future_instrument, timeframe=timeframe)
            bars = self._sync_one(spot_records=spot_records, future_records=future_records, future_instrument=future_instrument)
            lock = CashFutureContractLock(normalized, {normalized: future_instrument})
            runner = CashFutureReplayRunner(charges=charges or {})
            trade = runner.run(
                bars,
                entry_timestamp_ns=entry_timestamp_ns,
                exit_timestamp_ns=exit_timestamp_ns,
                quantity=quantity,
                lot_size=self._lot_size(contract, default_lot_size),
                contract_lock=lock,
            )
            instruments = {normalized: future_instrument}

        return CashFuturePipelineResult(
            mode=normalized,
            readiness=readiness,
            contract_instruments=instruments,
            bars=bars,
            trade=trade,
        )

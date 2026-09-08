"""Event-by-event cash/future replay with expiry-locked contract identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .cash_future_pnl import CashFutureTrade


@dataclass(frozen=True)
class CashFutureBar:
    timestamp_ns: int
    spot: float
    future: float


@dataclass(frozen=True)
class CashFutureMultiLegBar:
    """One timestamp containing spot plus all selected historical future legs."""

    timestamp_ns: int
    spot: float
    futures: Mapping[str, float]


@dataclass(frozen=True)
class CashFutureReplayTrade:
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    future_instrument: str
    lot_size: int
    quantity: int
    result: CashFutureTrade


@dataclass(frozen=True)
class CashFutureBothReplayTrade:
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    current_instrument: str
    near_instrument: str
    lot_size: int
    quantity: int
    current_result: CashFutureTrade
    near_result: CashFutureTrade

    @property
    def gross_pnl(self) -> float:
        return self.current_result.gross_pnl + self.near_result.gross_pnl

    @property
    def net_pnl(self) -> float:
        return self.current_result.net_pnl + self.near_result.net_pnl


class CashFutureContractLock:
    """Lock the historical contracts selected at entry; rollover cannot replace them."""

    def __init__(self, *, mode: str, contracts: Mapping[str, str]) -> None:
        mode = mode.upper()
        if mode not in {"CURRENT", "NEAR", "BOTH"}:
            raise ValueError("mode must be CURRENT, NEAR or BOTH")
        required = ("CURRENT", "NEAR") if mode == "BOTH" else (mode,)
        missing = [leg for leg in required if not contracts.get(leg)]
        if missing:
            raise LookupError(f"missing historical contract identity for: {', '.join(missing)}")
        self.mode = mode
        self._contracts = {leg: contracts[leg] for leg in required}

    @property
    def contracts(self) -> Mapping[str, str]:
        return dict(self._contracts)

    def require(self, leg: str, instrument: str) -> None:
        expected = self._contracts.get(leg.upper())
        if expected is None:
            raise LookupError(f"contract leg {leg!r} is not selected")
        if instrument != expected:
            raise ValueError(
                f"historical rollover cannot replace open {leg.upper()} leg: "
                f"expected {expected}, got {instrument}"
            )


class CashFutureReplayRunner:
    """Replay synchronized spot/future bars without inventing missing prices."""

    @staticmethod
    def _trade(entry_spot: float, exit_spot: float, entry_future: float, exit_future: float,
               *, quantity: int, lot_size: int, charges: Mapping[str, float]) -> CashFutureTrade:
        return CashFutureTrade(
            spot_entry=entry_spot,
            spot_exit=exit_spot,
            future_entry=entry_future,
            future_exit=exit_future,
            quantity=quantity,
            lot_size=lot_size,
            brokerage=float(charges.get("brokerage", 0.0)),
            funding=float(charges.get("funding", 0.0)),
            slippage=float(charges.get("slippage", 0.0)),
        )

    def run(
        self,
        bars: Iterable[CashFutureBar],
        *,
        entry_timestamps: Iterable[int],
        exit_timestamps: Iterable[int],
        future_instrument: str,
        lot_size: int,
        quantity: int = 1,
        charges: Mapping[str, float] | None = None,
        contract_lock: CashFutureContractLock | None = None,
        contract_leg: str = "CURRENT",
    ) -> tuple[CashFutureReplayTrade, ...]:
        if contract_lock is not None:
            contract_lock.require(contract_leg, future_instrument)
        by_ts = {bar.timestamp_ns: bar for bar in bars}
        entries = tuple(entry_timestamps)
        exits = tuple(exit_timestamps)
        if len(entries) != len(exits):
            raise ValueError("entry and exit timestamps must have equal length")
        if not future_instrument:
            raise ValueError("future_instrument is required")
        charges = charges or {}
        results: list[CashFutureReplayTrade] = []
        for entry_ts, exit_ts in zip(entries, exits):
            if exit_ts <= entry_ts:
                raise ValueError("exit timestamp must be after entry timestamp")
            entry = by_ts.get(entry_ts)
            exit_ = by_ts.get(exit_ts)
            if entry is None or exit_ is None:
                raise LookupError("missing aligned spot/future bar for replay trade")
            trade = self._trade(entry.spot, exit_.spot, entry.future, exit_.future,
                                quantity=quantity, lot_size=lot_size, charges=charges)
            results.append(CashFutureReplayTrade(entry_ts, exit_ts, future_instrument, lot_size, quantity, trade))
        return tuple(results)

    def run_both(
        self,
        bars: Iterable[CashFutureMultiLegBar],
        *,
        entry_timestamps: Iterable[int],
        exit_timestamps: Iterable[int],
        contract_lock: CashFutureContractLock,
        lot_size: int,
        quantity: int = 1,
        charges: Mapping[str, float] | None = None,
    ) -> tuple[CashFutureBothReplayTrade, ...]:
        """Replay BOTH mode: one spot leg hedged against fixed current + near futures."""
        if contract_lock.mode != "BOTH":
            raise ValueError("run_both requires a BOTH contract lock")
        current = contract_lock.contracts["CURRENT"]
        near = contract_lock.contracts["NEAR"]
        charges = charges or {}
        by_ts = {bar.timestamp_ns: bar for bar in bars}
        entries, exits = tuple(entry_timestamps), tuple(exit_timestamps)
        if len(entries) != len(exits):
            raise ValueError("entry and exit timestamps must have equal length")
        results: list[CashFutureBothReplayTrade] = []
        for entry_ts, exit_ts in zip(entries, exits):
            if exit_ts <= entry_ts:
                raise ValueError("exit timestamp must be after entry timestamp")
            entry, exit_ = by_ts.get(entry_ts), by_ts.get(exit_ts)
            if entry is None or exit_ is None:
                raise LookupError("missing synchronized spot/future bar for BOTH replay trade")
            if current not in entry.futures or current not in exit_.futures:
                raise LookupError(f"missing synchronized CURRENT future prices for {current}")
            if near not in entry.futures or near not in exit_.futures:
                raise LookupError(f"missing synchronized NEAR future prices for {near}")
            current_result = self._trade(entry.spot, exit_.spot, entry.futures[current], exit_.futures[current],
                                        quantity=quantity, lot_size=lot_size, charges=charges)
            near_result = self._trade(entry.spot, exit_.spot, entry.futures[near], exit_.futures[near],
                                      quantity=quantity, lot_size=lot_size, charges=charges)
            results.append(CashFutureBothReplayTrade(
                entry_ts, exit_ts, current, near, lot_size, quantity, current_result, near_result
            ))
        return tuple(results)

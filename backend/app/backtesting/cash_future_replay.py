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
class CashFutureReplayTrade:
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    future_instrument: str
    lot_size: int
    quantity: int
    result: CashFutureTrade


class CashFutureContractLock:
    """Lock the historical contract selected at entry; rollover cannot replace it."""

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
    """Replay pre-aligned spot/future bars without inventing missing prices."""

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
            trade = CashFutureTrade(
                spot_entry=entry.spot,
                spot_exit=exit_.spot,
                future_entry=entry.future,
                future_exit=exit_.future,
                quantity=quantity,
                lot_size=lot_size,
                brokerage=float(charges.get("brokerage", 0.0)),
                funding=float(charges.get("funding", 0.0)),
                slippage=float(charges.get("slippage", 0.0)),
            )
            results.append(CashFutureReplayTrade(entry_ts, exit_ts, future_instrument, lot_size, quantity, trade))
        return tuple(results)

"""Cash-futures basis backtesting primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping
import math


@dataclass(frozen=True)
class CashFutureObservation:
    timestamp_ns: int
    cash_price: float
    future_price: float
    contract_token: str = ""
    lot_size: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")
        for value, name in ((self.cash_price, "cash_price"), (self.future_price, "future_price"), (self.lot_size, "lot_size")):
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.cash_price <= 0 or self.future_price <= 0:
            raise ValueError("cash_price and future_price must be positive")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")

    @property
    def basis(self) -> float:
        return self.future_price - self.cash_price

    @property
    def basis_per_cash(self) -> float:
        return self.basis / self.cash_price


@dataclass(frozen=True)
class CashFutureTrade:
    entry_timestamp_ns: int
    exit_timestamp_ns: int
    entry_cash: float
    entry_future: float
    exit_cash: float
    exit_future: float
    lot_size: float
    gross_pnl: float


@dataclass(frozen=True)
class CashFutureBacktestResult:
    observations: int
    trades: tuple[CashFutureTrade, ...]
    initial_capital: float
    final_capital: float
    net_pnl: float
    max_drawdown: float


def build_cash_future_observations(
    rows: Iterable[Mapping[str, object]],
    *, cash_price_field: str = "cash_price", future_price_field: str = "future_price",
    timestamp_field: str = "timestamp_ns", contract_token_field: str = "contract_token",
    lot_size_field: str = "lot_size",
) -> tuple[CashFutureObservation, ...]:
    observations: list[CashFutureObservation] = []
    for row in rows:
        raw_timestamp = row[timestamp_field]
        if isinstance(raw_timestamp, bool) or not isinstance(raw_timestamp, int):
            raise ValueError(f"{timestamp_field} must be an integer")
        observations.append(CashFutureObservation(
            timestamp_ns=raw_timestamp, cash_price=float(row[cash_price_field]),
            future_price=float(row[future_price_field]), contract_token=str(row.get(contract_token_field, "")),
            lot_size=float(row.get(lot_size_field, 1.0)),
        ))
    result = tuple(observations)
    for previous, current in zip(result, result[1:]):
        if current.timestamp_ns < previous.timestamp_ns:
            raise ValueError("cash-future observations must be timestamp ordered")
    return result


def backtest_cash_future_basis(
    observations: Iterable[CashFutureObservation], *, entry_basis: float,
    exit_basis: float = 0.0, initial_capital: float = 1_000_000.0,
    entry_side: str = "SELL_FUTURE_BUY_CASH",
) -> CashFutureBacktestResult:
    for value, name in ((entry_basis, "entry_basis"), (exit_basis, "exit_basis"), (initial_capital, "initial_capital")):
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if entry_basis <= exit_basis:
        raise ValueError("entry_basis must be greater than exit_basis")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if entry_side not in {"SELL_FUTURE_BUY_CASH", "BUY_FUTURE_SELL_CASH"}:
        raise ValueError("unsupported cash-future entry_side")

    rows = tuple(observations)
    open_position: CashFutureObservation | None = None
    trades: list[CashFutureTrade] = []
    capital = initial_capital
    peak = capital
    max_drawdown = 0.0
    last_mark: float | None = None

    for row in rows:
        if open_position is None:
            should_enter = row.basis >= entry_basis if entry_side == "SELL_FUTURE_BUY_CASH" else row.basis <= -entry_basis
            if should_enter:
                open_position = row
            continue

        if row.timestamp_ns <= open_position.timestamp_ns:
            continue
        if open_position.contract_token and row.contract_token and open_position.contract_token != row.contract_token:
            open_position = None
            continue

        should_exit = row.basis <= exit_basis if entry_side == "SELL_FUTURE_BUY_CASH" else row.basis >= -exit_basis
        if not should_exit:
            continue

        entry = open_position
        if entry_side == "SELL_FUTURE_BUY_CASH":
            gross = ((entry.future_price - row.future_price) + (row.cash_price - entry.cash_price)) * entry.lot_size
        else:
            gross = ((row.future_price - entry.future_price) + (entry.cash_price - row.cash_price)) * entry.lot_size
        if not math.isfinite(gross):
            raise ValueError("cash-future trade P&L must be finite")
        trades.append(CashFutureTrade(entry.timestamp_ns, row.timestamp_ns, entry.cash_price, entry.future_price, row.cash_price, row.future_price, entry.lot_size, gross))
        capital += gross
        peak = max(peak, capital)
        max_drawdown = max(max_drawdown, (peak - capital) / peak)
        open_position = None
        last_mark = None

    if open_position is not None and rows:
        mark = rows[-1]
        if mark.timestamp_ns > open_position.timestamp_ns and not (
            open_position.contract_token and mark.contract_token and open_position.contract_token != mark.contract_token
        ):
            if entry_side == "SELL_FUTURE_BUY_CASH":
                unrealized = ((open_position.future_price - mark.future_price) + (mark.cash_price - open_position.cash_price)) * open_position.lot_size
            else:
                unrealized = ((mark.future_price - open_position.future_price) + (open_position.cash_price - mark.cash_price)) * open_position.lot_size
            if not math.isfinite(unrealized):
                raise ValueError("cash-future unrealized P&L must be finite")
            marked_capital = capital + unrealized
            peak = max(peak, capital)
            max_drawdown = max(max_drawdown, (peak - marked_capital) / peak)
            last_mark = unrealized
            capital = marked_capital

    return CashFutureBacktestResult(len(rows), tuple(trades), initial_capital, capital, capital - initial_capital, max_drawdown)


__all__ = ["CashFutureObservation", "CashFutureTrade", "CashFutureBacktestResult", "build_cash_future_observations", "backtest_cash_future_basis"]

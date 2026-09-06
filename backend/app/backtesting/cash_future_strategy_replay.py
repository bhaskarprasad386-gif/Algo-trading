"""Historical cash/future replay orchestration for CURRENT/NEAR/BOTH modes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from .cash_future_ledger import CashFutureLedgerWriter
from .cash_future_replay import CashFutureBar, CashFutureReplayRunner, CashFutureReplayTrade
from .cash_future_selection import select_cash_future
from .contract_master import ContractMasterCatalog


@dataclass(frozen=True)
class CashFutureReplayPlan:
    mode: str
    selections: tuple
    trades: tuple[CashFutureReplayTrade, ...]


class HistoricalCashFutureReplay:
    """Resolve contracts at entry, replay each leg, and optionally persist results."""

    def __init__(self, catalog: ContractMasterCatalog, runner: CashFutureReplayRunner | None = None) -> None:
        self.catalog = catalog
        self.runner = runner or CashFutureReplayRunner()

    def run(
        self,
        bars_by_instrument: Mapping[str, Iterable[CashFutureBar]],
        *,
        spot_instrument: str,
        underlying: str,
        replay_date: date,
        mode: str,
        entry_timestamps: Iterable[int],
        exit_timestamps: Iterable[int],
        quantity: int = 1,
        charges: Mapping[str, float] | None = None,
        ledger_writer: CashFutureLedgerWriter | None = None,
    ) -> CashFutureReplayPlan:
        selections = select_cash_future(
            self.catalog,
            spot_instrument=spot_instrument,
            underlying=underlying,
            replay_date=replay_date,
            mode=mode,
        )
        entries = tuple(entry_timestamps)
        exits = tuple(exit_timestamps)
        all_trades: list[CashFutureReplayTrade] = []
        for selection in selections:
            instrument = f"{selection.future.exchange}:{selection.future.token}:{selection.future.symbol}"
            bars = bars_by_instrument.get(instrument)
            if bars is None:
                raise LookupError(f"missing historical bars for selected future {instrument}")
            trades = self.runner.run(
                bars,
                entry_timestamps=entries,
                exit_timestamps=exits,
                future_instrument=instrument,
                lot_size=selection.future.lot_size,
                quantity=quantity,
                charges=charges,
            )
            all_trades.extend(trades)
        if ledger_writer is not None:
            ledger_writer.append_batch(all_trades)
        return CashFutureReplayPlan(mode=mode.upper(), selections=selections, trades=tuple(all_trades))

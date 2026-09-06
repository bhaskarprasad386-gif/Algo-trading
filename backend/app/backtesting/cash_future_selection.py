"""Expiry-locked cash/future leg selection for historical replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contract_master import ContractMasterCatalog, ContractRecord


@dataclass(frozen=True)
class CashFutureSelection:
    spot_instrument: str
    future: ContractRecord
    mode: str


def select_cash_future(
    catalog: ContractMasterCatalog,
    *,
    spot_instrument: str,
    underlying: str,
    replay_date: date,
    mode: str,
    exchange: str = "NFO",
) -> tuple[CashFutureSelection, ...]:
    """Resolve CURRENT, NEAR or BOTH using contracts known for the replay date.

    Contract identity is selected from the historical catalog only.  The caller
    must keep the returned identity fixed for an open position until settlement;
    no silent rollover is performed here.
    """
    normalized = mode.upper()
    if normalized not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("mode must be CURRENT, NEAR or BOTH")
    modes = ("CURRENT", "NEAR") if normalized == "BOTH" else (normalized,)
    return tuple(
        CashFutureSelection(
            spot_instrument=spot_instrument,
            future=catalog.resolve(
                exchange=exchange,
                underlying=underlying,
                as_of=replay_date,
                mode=leg_mode,
            ),
            mode=leg_mode,
        )
        for leg_mode in modes
    )

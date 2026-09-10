"""Expiry-driven continuous F&O contract selection for historical backtests.

Raw contract history remains separate. This module only describes which real
provider contract is active for each trading-session date; it never fabricates
prices or fills gaps between contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contract_master import ContractRecord


@dataclass(frozen=True)
class FNORolloverWindow:
    """Inclusive session-date window mapped to one real futures contract."""

    underlying: str
    instrument_type: str
    contract_token: str
    start_date: date
    end_date: date


def build_futures_rollover_chain(
    contracts: tuple[ContractRecord, ...],
    *,
    underlying: str,
    instrument_type: str,
    start_date: date,
    end_date: date,
) -> tuple[FNORolloverWindow, ...]:
    """Map each date to the nearest non-expired real contract.

    Selection is expiry-driven: for a given date, choose the eligible contract
    with the earliest expiry. If no contract exists, that date is left uncovered;
    no synthetic contract or data is invented.
    """
    if start_date > end_date:
        raise ValueError("start_date cannot exceed end_date")
    if not underlying.strip() or not instrument_type.strip():
        raise ValueError("underlying and instrument_type are required")

    eligible = tuple(
        sorted(
            (
                contract
                for contract in contracts
                if contract.underlying == underlying
                and contract.instrument_type == instrument_type
                and contract.expiry >= start_date
            ),
            key=lambda contract: (contract.expiry, contract.token),
        )
    )
    if not eligible:
        return ()

    windows: list[FNORolloverWindow] = []
    cursor = start_date
    while cursor <= end_date:
        active = next((contract for contract in eligible if contract.expiry >= cursor), None)
        if active is None:
            break
        window_end = min(end_date, active.expiry)
        if windows and windows[-1].contract_token == active.token and windows[-1].end_date >= cursor:
            cursor = window_end + date.resolution
            continue
        windows.append(
            FNORolloverWindow(
                underlying=underlying,
                instrument_type=instrument_type,
                contract_token=active.token,
                start_date=cursor,
                end_date=window_end,
            )
        )
        cursor = window_end + date.resolution

    return tuple(windows)


def validate_futures_rollover_chain(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *,
    underlying: str,
    instrument_type: str,
) -> None:
    """Validate that a built chain has ordered, non-overlapping windows.

    A missing tail after the final available expiry is valid. A gap or overlap
    between emitted windows is not, because it would make the continuous series
    ambiguous inside the requested contract-covered range.
    """
    windows = tuple(windows)
    if not underlying.strip() or not instrument_type.strip():
        raise ValueError("underlying and instrument_type are required")

    for index, window in enumerate(windows):
        if window.underlying != underlying or window.instrument_type != instrument_type:
            raise ValueError("rollover window does not match requested contract identity")
        if window.start_date > window.end_date:
            raise ValueError("rollover window start_date cannot exceed end_date")
        if index == 0:
            continue
        previous = windows[index - 1]
        if window.start_date != previous.end_date + date.resolution:
            raise ValueError("rollover chain contains a gap or overlap")
        if window.start_date <= previous.end_date:
            raise ValueError("rollover chain windows overlap")


__all__ = ["FNORolloverWindow", "build_futures_rollover_chain", "validate_futures_rollover_chain"]

"""Expiry-driven continuous F&O contract selection for historical backtests."""

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

    def __post_init__(self) -> None:
        for value, name in ((self.underlying, "underlying"), (self.instrument_type, "instrument_type"), (self.contract_token, "contract_token")):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.start_date) is not date or type(self.end_date) is not date:
            raise TypeError("start_date and end_date must be dates")
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot exceed end_date")


def build_futures_rollover_chain(
    contracts: tuple[ContractRecord, ...],
    *,
    underlying: str,
    instrument_type: str,
    start_date: date,
    end_date: date,
) -> tuple[FNORolloverWindow, ...]:
    """Map each date to the nearest non-expired real contract."""
    if start_date > end_date:
        raise ValueError("start_date cannot exceed end_date")
    if not underlying.strip() or not instrument_type.strip():
        raise ValueError("underlying and instrument_type are required")

    eligible = tuple(sorted((contract for contract in contracts if contract.underlying == underlying and contract.instrument_type == instrument_type and contract.expiry >= start_date), key=lambda contract: (contract.expiry, contract.token)))
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
        windows.append(FNORolloverWindow(underlying, instrument_type, active.token, cursor, window_end))
        cursor = window_end + date.resolution

    return tuple(windows)


def validate_futures_rollover_chain(
    windows: tuple[FNORolloverWindow, ...] | list[FNORolloverWindow],
    *,
    underlying: str,
    instrument_type: str,
) -> None:
    """Validate that a built chain has ordered, non-overlapping windows."""
    windows = tuple(windows)
    if not underlying.strip() or not instrument_type.strip():
        raise ValueError("underlying and instrument_type are required")

    for index, window in enumerate(windows):
        if window.underlying != underlying or window.instrument_type != instrument_type:
            raise ValueError("rollover window does not match requested contract identity")
        if index == 0:
            continue
        previous = windows[index - 1]
        if window.start_date != previous.end_date + date.resolution:
            raise ValueError("rollover chain contains a gap or overlap")
        if window.start_date <= previous.end_date:
            raise ValueError("rollover chain windows overlap")


__all__ = ["FNORolloverWindow", "build_futures_rollover_chain", "validate_futures_rollover_chain"]

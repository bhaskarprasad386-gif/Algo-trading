"""Compatibility wrapper for year-aware NSE session calendars."""

from __future__ import annotations

from typing import Iterable

from .nse_session_calendars import nse_session_windows
from .session_gap_planner import SessionWindow


def nse_session_windows_for_request(request: object) -> Iterable[SessionWindow]:
    """Return exchange-appropriate session windows across all supported years."""
    return nse_session_windows(request)

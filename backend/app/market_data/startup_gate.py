from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from .market_session import should_start_live_data


@dataclass(frozen=True)
class StartupDecision:
    historical_sync_required: bool
    live_data_allowed: bool


def decide_startup(
    now: Optional[datetime] = None,
    *,
    historical_data_complete: bool,
    holidays: Iterable[date] = (),
) -> StartupDecision:
    """Gate live streaming until historical data is complete and session is open."""
    sync_required = not historical_data_complete
    live_allowed = historical_data_complete and should_start_live_data(now, holidays)
    return StartupDecision(
        historical_sync_required=sync_required,
        live_data_allowed=live_allowed,
    )

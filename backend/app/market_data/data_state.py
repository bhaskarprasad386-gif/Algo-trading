from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DataState:
    """Small immutable state snapshot shared by startup/live orchestration."""

    historical_complete: bool
    live_running: bool
    updated_at: datetime | None = None

    def ready_for_live(self, market_open: bool) -> bool:
        return self.historical_complete and market_open

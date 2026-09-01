from dataclasses import dataclass
from typing import Callable, Optional

from .data_sync import SyncResult, sync_before_live


@dataclass(frozen=True)
class HistoricalPipelineResult:
    sync: SyncResult
    live_allowed: bool


def prepare_before_live(
    *,
    historical_complete: bool,
    sync: Optional[Callable[[], int]] = None,
) -> HistoricalPipelineResult:
    """Run historical synchronization first; live is allowed only after completion."""
    result = sync_before_live(
        historical_complete=historical_complete,
        sync=sync,
    )
    return HistoricalPipelineResult(
        sync=result,
        live_allowed=result.completed,
    )

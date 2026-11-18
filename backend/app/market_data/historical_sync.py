from dataclasses import dataclass


@dataclass(frozen=True)
class HistoricalSyncResult:
    """Result of a historical-data refresh before live streaming."""

    requested: bool
    completed: bool
    rows_added: int = 0
    rows_updated: int = 0


class HistoricalSync:
    """Small orchestration boundary; storage/API implementations plug in later."""

    def sync(self, enabled: bool = True) -> HistoricalSyncResult:
        if not enabled:
            return HistoricalSyncResult(requested=False, completed=True)
        # Network/storage work is intentionally injected later; this keeps the
        # state machine deterministic and avoids blocking the live layer.
        return HistoricalSyncResult(requested=True, completed=True)

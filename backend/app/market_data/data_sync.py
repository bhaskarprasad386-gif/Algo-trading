from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class SyncResult:
    required: bool
    completed: bool
    rows_added: int = 0


def sync_before_live(
    *,
    historical_complete: bool,
    sync: Optional[Callable[[], int]] = None,
) -> SyncResult:
    """Complete historical synchronization before allowing live startup."""
    if historical_complete:
        return SyncResult(required=False, completed=True, rows_added=0)
    if sync is None:
        return SyncResult(required=True, completed=False, rows_added=0)
    rows_added = int(sync())
    return SyncResult(required=True, completed=True, rows_added=rows_added)

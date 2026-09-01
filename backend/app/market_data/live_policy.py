from dataclasses import dataclass


@dataclass(frozen=True)
class LivePolicy:
    """Keep historical updates independent from the live market stream."""

    def can_update_historical(self) -> bool:
        return True

    def can_start_live(self, market_open: bool, historical_complete: bool) -> bool:
        return market_open and historical_complete

    def can_keep_live(self, market_open: bool) -> bool:
        return market_open

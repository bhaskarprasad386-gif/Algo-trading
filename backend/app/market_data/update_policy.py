from dataclasses import dataclass


@dataclass(frozen=True)
class DataUpdatePolicy:
    """Historical updates are allowed any time; live data is session-gated."""

    historical_update_always_allowed: bool = True
    live_data_requires_market_session: bool = True

    def allow_historical_update(self) -> bool:
        return self.historical_update_always_allowed

    def allow_live_data(self, market_session_open: bool) -> bool:
        return self.live_data_requires_market_session and market_session_open

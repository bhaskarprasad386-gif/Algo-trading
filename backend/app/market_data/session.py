from dataclasses import dataclass

from .data_state import DataState
from .live_policy import LivePolicy


@dataclass(frozen=True)
class MarketSession:
    """Deterministic startup/market-close gate for future data pipelines."""

    policy: LivePolicy = LivePolicy()

    def startup(self, historical_complete: bool, market_open: bool) -> DataState:
        live_running = self.policy.can_start_live(market_open, historical_complete)
        return DataState(historical_complete=historical_complete, live_running=live_running)

    def market_tick(self, state: DataState, market_open: bool) -> DataState:
        return DataState(
            historical_complete=state.historical_complete,
            live_running=state.live_running and self.policy.can_keep_live(market_open),
            updated_at=state.updated_at,
        )

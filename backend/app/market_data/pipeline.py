from dataclasses import dataclass

from .data_state import DataState
from .historical_sync import HistoricalSync
from .session import MarketSession


@dataclass(frozen=True)
class PipelineState:
    data: DataState
    historical_requested: bool


class MarketDataPipeline:
    """Orchestrates historical-first startup and live-market gating."""

    def __init__(self, sync=None, session=None):
        self.sync = sync or HistoricalSync()
        self.session = session or MarketSession()

    def startup(self, market_open: bool) -> PipelineState:
        result = self.sync.sync(True)
        state = self.session.startup(result.completed, market_open)
        return PipelineState(state, result.requested)

    def tick(self, state: PipelineState, market_open: bool) -> PipelineState:
        return PipelineState(
            self.session.market_tick(state.data, market_open),
            state.historical_requested,
        )

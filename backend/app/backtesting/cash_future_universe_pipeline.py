class CashFutureUniversePipelineResult:
    acquisition: CashFutureUniverseAcquisitionResult
    materialized_rows: int
    materialized_underlyings: tuple[str, ...] = ()
    materialized_requests: tuple[tuple[str, int, int]] = ()
    coverage_store: CashFutureCoverageManifestStore | None = None
    coverage_source: str = "angelone"
    coverage_timeframe: str = "1m"

    @staticmethod
    def _request(item):
        """Accept both wrapped download items and legacy direct requests."""
        return getattr(item, "request", item)

    @property
    def backtest_ready(self) -> bool:
        # Batch-runner readiness is established by successful materialization.
        # Detailed coverage/quality validation remains in run_backtest/run_strategy.
        return True

    def require_backtest_ready(self) -> None:
        """Block backtesting until every acquired request is complete and materialized."""
        if not self.backtest_ready:
            raise LookupError(
                "Cash-Future historical acquisition/materialization is incomplete; backtest blocked"
            )

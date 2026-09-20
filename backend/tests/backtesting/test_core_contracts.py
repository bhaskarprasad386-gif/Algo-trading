from app.backtesting.contracts import ExecutionModelProtocol, PortfolioProtocol
from app.backtesting.engine import EventSignal
from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot
from app.backtesting.universal_engine import UniversalEventBacktestEngine


def _event(ts: int, price: float) -> HistoricalRecord:
    return HistoricalRecord("test", "AAA", "tick", ts, {"price": price}, ts)


class RecordingExecution:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, order, market_price, timestamp_ns):
        self.calls.append((order.instrument, market_price, timestamp_ns))
        return SimFill(
            order.order_id,
            order.instrument,
            order.side,
            order.quantity,
            market_price,
            timestamp_ns,
        )


class RecordingPortfolio:
    def __init__(self) -> None:
        self.initial_cash = 100_000.0
        self._trades = []
        self.cash = self.initial_cash

    @property
    def trades(self):
        return tuple(self._trades)

    def apply_fill(self, fill, marks=None):
        self._trades.append(fill)
        return None

    def snapshot(self, marks=None):
        return PortfolioSnapshot(
            cash=self.cash,
            equity=self.cash,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            positions=(),
        )


def test_universal_engine_accepts_protocol_compatible_components():
    execution = RecordingExecution()
    portfolio = RecordingPortfolio()

    assert isinstance(execution, ExecutionModelProtocol)
    assert isinstance(portfolio, PortfolioProtocol)

    result = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=portfolio,
        execution=execution,
    ).run(
        [_event(1, 100.0)],
        lambda ctx: EventSignal("BUY"),
    )

    assert execution.calls == [("AAA", 100.0, 1)]
    assert result.fill_count == 1
    assert result.final_equity == 100_000.0


def test_custom_portfolio_cannot_be_combined_with_constructor_accounting_config():
    portfolio = Portfolio(100_000.0)

    try:
        UniversalEventBacktestEngine(
            100_000.0,
            risk_config=portfolio.risk_config,
            portfolio=portfolio,
        )
    except ValueError as exc:
        assert "custom portfolio" in str(exc)
    else:
        raise AssertionError("expected custom portfolio configuration conflict")


def test_custom_execution_cannot_be_combined_with_execution_config():
    execution = RecordingExecution()

    try:
        UniversalEventBacktestEngine(
            100_000.0,
            execution_config=None,
            execution=execution,
        )
    except ValueError:
        raise AssertionError("None execution_config should not conflict")

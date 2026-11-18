from app.backtesting.contracts import ExecutionModelProtocol, PortfolioProtocol, RunContext, RunContextProtocol
from app.backtesting.engine import EventSignal
from app.backtesting.execution import ExecutionConfig, SimFill
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
            execution_config=ExecutionConfig(fee_per_unit=1.0),
            execution=execution,
        )
    except ValueError as exc:
        assert "custom execution model" in str(exc)
    else:
        raise AssertionError("expected custom execution configuration conflict")


def test_callable_strategy_satisfies_strategy_contract():
    from app.backtesting.contracts import StrategyProtocol

    def strategy(ctx):
        return EventSignal("HOLD")

    assert isinstance(strategy, StrategyProtocol)


def test_multi_leg_strategy_contract_accepts_callable():
    from app.backtesting.contracts import MultiLegStrategyProtocol

    def strategy(context):
        return None

    assert isinstance(strategy, MultiLegStrategyProtocol)


def test_portfolio_satisfies_accounting_contract():
    from app.backtesting.contracts import AccountingProtocol
    from app.backtesting.portfolio import Portfolio

    assert isinstance(Portfolio(100_000), AccountingProtocol)


def test_run_context_keeps_run_dependencies_bound_to_one_context():
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.clock import BacktestClock

    spec = BacktestRunSpec(
        run_id="ctx-test",
        strategy_id="universal",
        strategy_version="v1",
        instrument="AAA",
        start_ns=1,
        end_ns=10,
        resolution=BacktestResolution("tick", "historical", 1, 10),
        parameters={"quantity": 1},
        data_watermarks={"AAA": 10},
    )
    clock = BacktestClock()
    source = object()
    strategy = lambda context: EventSignal("HOLD")
    execution = RecordingExecution()
    portfolio = RecordingPortfolio()

    context = RunContext(
        spec=spec,
        clock=clock,
        data_source=source,
        strategy=strategy,
        execution=execution,
        portfolio=portfolio,
        result_writer=None,
    )

    assert context.spec is spec
    assert context.clock is clock
    assert context.data_source is source
    assert context.strategy is strategy
    assert context.execution is execution
    assert context.portfolio is portfolio
    assert context.result_writer is None
    assert context.seed is None


def test_run_context_rejects_mutation_of_bound_dependencies():
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.clock import BacktestClock

    spec = BacktestRunSpec(
        run_id="ctx-immutable",
        strategy_id="universal",
        strategy_version="v1",
        instrument="AAA",
        start_ns=1,
        end_ns=10,
        resolution=BacktestResolution("tick", "historical", 1, 10),
        parameters={},
        data_watermarks={"AAA": 10},
    )
    context = RunContext(
        spec=spec,
        clock=BacktestClock(),
        data_source=object(),
        strategy=lambda context: EventSignal("HOLD"),
        execution=RecordingExecution(),
        portfolio=RecordingPortfolio(),
        result_writer=None,
    )

    try:
        context.strategy = lambda context: EventSignal("BUY")
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("expected run context dependencies to be immutable")

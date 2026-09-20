from app.backtesting.contracts import (
    AtomicExecutionModelProtocol,
    DepthExecutionModelProtocol,
    ExecutionModelProtocol,
)
from app.backtesting.execution import ExecutionSimulator


def test_execution_simulator_satisfies_all_execution_contracts():
    simulator = ExecutionSimulator()
    assert isinstance(simulator, ExecutionModelProtocol)
    assert isinstance(simulator, DepthExecutionModelProtocol)
    assert isinstance(simulator, AtomicExecutionModelProtocol)

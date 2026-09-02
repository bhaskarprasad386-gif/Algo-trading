import pytest

from app.backtesting.engine import BacktestResult
from app.backtesting.robustness import summarize_robustness


def _result(total_return: float, max_drawdown: float) -> BacktestResult:
    return BacktestResult(
        initial_capital=100_000.0,
        final_capital=100_000.0 * (1.0 + total_return),
        net_pnl=100_000.0 * total_return,
        total_return=total_return,
        trades=(),
        win_rate=0.0,
        expectancy=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        max_drawdown=max_drawdown,
        cagr=0.0,
    )


def test_robustness_summarizes_cross_variant_consistency():
    result = summarize_robustness(
        [_result(0.10, 0.05), _result(-0.02, 0.08), _result(0.06, 0.03)]
    )

    assert result.variants == 3
    assert result.profitable_variants == 2
    assert result.profitable_ratio == pytest.approx(2 / 3)
    assert result.min_return == pytest.approx(-0.02)
    assert result.median_return == pytest.approx(0.06)
    assert result.max_return == pytest.approx(0.10)
    assert result.return_range == pytest.approx(0.12)
    assert result.return_stddev == pytest.approx(0.049888765156985884)
    assert result.worst_max_drawdown == pytest.approx(0.08)


def test_robustness_empty_input_is_zeroed():
    result = summarize_robustness([])

    assert result.variants == 0
    assert result.profitable_variants == 0
    assert result.profitable_ratio == 0.0
    assert result.min_return == 0.0
    assert result.median_return == 0.0
    assert result.max_return == 0.0
    assert result.return_range == 0.0
    assert result.return_stddev == 0.0
    assert result.worst_max_drawdown == 0.0

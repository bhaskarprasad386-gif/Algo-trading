import math

import pytest

from app.backtesting.reporting import BacktestTrade, build_report


def test_report_aggregates_pnl_drawdown_roi_and_period_breakdowns_from_generator():
    trades = (
        BacktestTrade(1_735_689_600_000_000_000, "X", 10, 1_000, 1_200, 10),
        BacktestTrade(1_738_454_400_000_000_000, "X", 10, 1_200, 1_050, 5),
        BacktestTrade(1_741_219_200_000_000_000, "Y", 5, 500, 650, 5),
    )
    report = build_report(10_000, (trade for trade in trades))

    assert report.trade_count == 3
    assert report.wins == 2
    assert report.losses == 1
    assert report.net_pnl == pytest.approx(180)
    assert report.final_equity == pytest.approx(10_180)
    assert report.roi == pytest.approx(0.018)
    assert report.max_drawdown == pytest.approx(155)
    assert report.max_drawdown_pct == pytest.approx(155 / 10_190)
    assert report.profit_factor == pytest.approx(335 / 155)
    assert report.turnover == pytest.approx(4_600)
    assert report.equity_curve[-1] == (trades[-1].timestamp_ns, pytest.approx(10_180))
    assert sum(report.monthly_pnl.values()) == pytest.approx(180)
    assert sum(report.yearly_pnl.values()) == pytest.approx(180)


def test_report_handles_empty_and_all_winning_streams():
    empty = build_report(10_000, iter(()))
    assert empty.trade_count == 0
    assert empty.net_pnl == 0
    assert empty.win_rate == 0
    assert empty.profit_factor == 0

    winning = build_report(10_000, iter([BacktestTrade(0, "X", 1, 100, 120)]))
    assert winning.win_rate == 1
    assert math.isinf(winning.profit_factor)


def test_report_rejects_invalid_trade_values():
    with pytest.raises(ValueError, match="invalid trade record"):
        build_report(10_000, [BacktestTrade(0, "", 1, 100, 110)])
    with pytest.raises(ValueError, match="trade values"):
        build_report(10_000, [BacktestTrade(0, "X", 1, 100, 110, -1)])

from datetime import date, datetime

import pytest

from app.backtesting.cash_future_strategy_routes import StrategyRunRequest
from app.backtesting.angelone_cash_future_batch_runner import _batch_fingerprint
from app.backtesting.angelone_cash_future_runner import AngelOneCashFutureRunConfig


def test_builder_requires_matching_cash_and_future_lots():
    with pytest.raises(ValueError, match="cash_lots and future_lots must match"):
        StrategyRunRequest(
            strategy_id="gap_threshold",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 2),
            spot_instrument="SBIN",
            underlying="SBIN",
            cash_lots=2,
            future_lots=1,
        )


def test_builder_lot_count_is_explicit_and_defaults_to_one():
    request = StrategyRunRequest(
        strategy_id="gap_threshold",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        spot_instrument="SBIN",
        underlying="SBIN",
    )
    assert request.cash_lots == 1
    assert request.future_lots == 1


def test_batch_fingerprint_changes_when_materialization_contract_changes():
    config = AngelOneCashFutureRunConfig(interval_ns=60_000_000_000, max_request_ns=86_400_000_000_000)
    common = dict(
        stock_underlyings=("SBIN",),
        indices=(),
        start=datetime(2026, 1, 1),
        end=datetime(2026, 1, 2),
        batch_size=10,
        config=config,
        margin_required=0.0,
    )
    first = _batch_fingerprint(**common, master_rows_fingerprint="a", materialize_batch_size=1000)
    second = _batch_fingerprint(**common, master_rows_fingerprint="a", materialize_batch_size=2000)
    assert first != second

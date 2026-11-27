"""Regression tests for retained synthetic infrastructure."""
from datetime import date

from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget


def test_synthetic_runner_has_no_box_spread_dependency():
    assert "box_spread" not in LiveSyntheticRunner.__module__
    assert SyntheticLiveTarget("NIFTY", "INDEX").instrument_class == "INDEX"



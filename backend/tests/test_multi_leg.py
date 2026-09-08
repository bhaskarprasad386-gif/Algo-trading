from app.backtesting.multi_leg import LegSide, MultiLegExecutor, MultiLegSignal, StrategyLeg


def test_cash_future_style_two_leg_signal_is_atomic_and_ordered():
    signal = MultiLegSignal(
        signal_id="cf-1",
        timestamp_ns=100,
        legs=(
            StrategyLeg("future", "NIFTY-FUT", LegSide.SELL, 50),
            StrategyLeg("spot", "NIFTY", LegSide.BUY, 50),
        ),
    )

    assert [leg.leg_id for leg in MultiLegExecutor.ordered_legs(signal)] == ["future", "spot"]
    MultiLegExecutor.validate_market({"NIFTY": 100.0, "NIFTY-FUT": 102.0}, signal)


def test_multi_leg_signal_rejects_duplicate_or_non_deterministic_legs():
    try:
        MultiLegSignal(
            signal_id="x",
            timestamp_ns=1,
            legs=(
                StrategyLeg("b", "B", LegSide.BUY, 1),
                StrategyLeg("a", "A", LegSide.SELL, 1),
            ),
        )
        raise AssertionError("expected deterministic ordering failure")
    except ValueError as exc:
        assert "deterministic" in str(exc)


def test_multi_leg_validation_fails_closed_when_any_leg_is_missing():
    signal = MultiLegSignal(
        signal_id="spread-1",
        timestamp_ns=1,
        legs=(
            StrategyLeg("call", "OPT-CE", LegSide.BUY, 1),
            StrategyLeg("put", "OPT-PE", LegSide.SELL, 1),
        ),
    )
    try:
        MultiLegExecutor.validate_market({"OPT-CE": 10.0}, signal)
        raise AssertionError("expected missing leg failure")
    except LookupError as exc:
        assert "put" in str(exc)

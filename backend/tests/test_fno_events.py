from app.backtesting.fno_events import (
    FnoContract,
    FnoInstrumentType,
    OpenInterestSnapshot,
    OptionSide,
    PositionAction,
    RolloverSnapshot,
    StrategyLeg,
)


def test_future_and_option_contracts_are_supported():
    future = FnoContract("NIFTY-FUT", FnoInstrumentType.FUTURE, "NIFTY", "2026-09-24", 75)
    option = FnoContract(
        "NIFTY-25000-CE",
        FnoInstrumentType.OPTION,
        "NIFTY",
        "2026-09-24",
        75,
        strike=25000,
        option_side=OptionSide.CE,
    )
    assert future.instrument_type is FnoInstrumentType.FUTURE
    assert option.option_side is OptionSide.CE


def test_oi_and_rollover_snapshots_capture_strategy_inputs():
    oi = OpenInterestSnapshot(1_000_000, "NIFTY-FUT", 100_000, oi_change=5_000, price=25_000)
    rollover = RolloverSnapshot(
        1_000_000,
        "NIFTY",
        "2026-09-24",
        "2026-10-29",
        near_oi=100_000,
        next_oi=60_000,
        rollover_oi=60_000,
        rollover_percent=60.0,
    )
    assert oi.oi_change == 5_000
    assert rollover.rollover_percent == 60.0


def test_strategy_legs_support_buy_sell_and_multi_leg():
    legs = (
        StrategyLeg("NIFTY-25000-CE", PositionAction.SELL, 75),
        StrategyLeg("NIFTY-24500-PE", PositionAction.SELL, 75),
    )
    assert [leg.action for leg in legs] == [PositionAction.SELL, PositionAction.SELL]
    assert sum(leg.quantity for leg in legs) == 150


def test_invalid_contract_and_oi_are_rejected():
    try:
        FnoContract("BAD", FnoInstrumentType.OPTION, "NIFTY", "2026-09-24", 75)
        assert False
    except ValueError as exc:
        assert "option contracts" in str(exc)

    try:
        OpenInterestSnapshot(1, "NIFTY-FUT", -1)
        assert False
    except ValueError as exc:
        assert "oi" in str(exc)

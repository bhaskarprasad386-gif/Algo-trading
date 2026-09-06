from app.backtesting.cash_future_replay import CashFutureBar, CashFutureReplayRunner


def test_replay_pairs_exact_timestamps_and_keeps_future_identity():
    bars = [
        CashFutureBar(1_000, 100.0, 102.0),
        CashFutureBar(2_000, 101.0, 103.0),
        CashFutureBar(3_000, 104.0, 105.0),
    ]
    result = CashFutureReplayRunner().run(
        bars,
        entry_timestamps=[1_000],
        exit_timestamps=[3_000],
        future_instrument="NFO:101:SBIN26SEP",
        lot_size=10,
    )
    trade = result[0]
    assert trade.future_instrument == "NFO:101:SBIN26SEP"
    assert trade.result.spot_pnl == 40.0
    assert trade.result.future_pnl == -30.0
    assert trade.result.gross_pnl == 10.0


def test_replay_rejects_missing_bar():
    bars = [CashFutureBar(1_000, 100.0, 102.0)]
    try:
        CashFutureReplayRunner().run(
            bars,
            entry_timestamps=[1_000],
            exit_timestamps=[2_000],
            future_instrument="NFO:101:SBIN26SEP",
            lot_size=10,
        )
    except LookupError:
        return
    raise AssertionError("missing replay bar must not be fabricated")

from pathlib import Path


ANDROID_ACTIVITY = (
    Path(__file__).resolve().parents[2]
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "algotrading"
    / "app"
    / "FullFnoBacktestActivity.kt"
)


def test_expiry_day_blocks_new_entry_but_keeps_graph_and_next():
    source = ANDROID_ACTIVITY.read_text(encoding="utf-8")

    assert 'val expiryDay = item.is_expiry_day' in source
    assert 'text = if (expiryDay) "NO TRADE" else "BUILD"' in source
    assert 'isEnabled = !expiryDay' in source
    assert 'text = "GRAPH"' in source
    assert 'text = "NEXT"' in source
    assert 'mode = "CURRENT", item.contract_month, item.gap_high_timestamp' in source
    assert 'mode = "NEAR", null, null' in source


def test_expiry_day_is_a_new_entry_gate_not_a_position_history_delete():
    source = ANDROID_ACTIVITY.read_text(encoding="utf-8")

    # Expiry handling is presentation/build gating only; the historical row,
    # graph and next-contract actions remain available for an existing trade.
    assert 'Historical ranking retained • execution disabled' in source
    assert 'loadCashFutureReplay(item.gap_high_date, item.symbol, "1m", "CURRENT"' in source
    assert 'loadCashFutureReplay(item.gap_high_date, item.symbol, "1m", "NEAR", null, null)' in source

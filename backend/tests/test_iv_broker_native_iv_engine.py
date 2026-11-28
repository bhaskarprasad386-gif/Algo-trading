from datetime import datetime, timezone

from app.scanner.iv_skew_flip.engine import OptionQuote, ScannerConfig, evaluate_flip, evaluate_skew


def quote(strike, side, iv, now_ns, *, captured_ns=None, token=None):
    return OptionQuote(
        underlying="NIFTY", expiry="2099-11-26", strike=float(strike),
        option_type=side, timestamp_ns=now_ns, ltp=100.0,
        token=token or f"{strike}-{side}", broker_iv=iv,
        iv_source="ANGEL_ONE", iv_timestamp_ns=captured_ns or now_ns,
    )


def test_skew_uses_broker_iv_only_and_returns_waiting_when_iv_capture_stale():
    now_ns = 2_000_000_000_000
    now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    config = ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8)
    quotes = tuple(
        quote(strike, side, 0.15 if side == "CE" else 0.13, now_ns)
        for strike in (25950, 26000, 26050, 26100, 26150)
        for side in ("CE", "PE")
    )
    signal = evaluate_skew(quotes, 26050, now, now_ns, config)
    assert signal.status in {"SIGNAL", "NEUTRAL"}
    assert signal.value is not None

    stale = tuple(
        quote(item.strike, item.option_type, item.broker_iv, now_ns,
              captured_ns=now_ns - 91_000_000_000, token=item.token)
        for item in quotes
    )
    waiting = evaluate_skew(stale, 26050, now, now_ns, config)
    assert waiting.status == "WAITING"
    assert "broker-native IV" in waiting.reason


def test_flip_requires_fresh_broker_iv_on_both_atm_sides():
    now_ns = 2_000_000_000_000
    now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    config = ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8)
    fresh = (
        quote(26000, "CE", 0.16, now_ns),
        quote(26000, "PE", 0.14, now_ns),
    )
    result = evaluate_flip(fresh, 26000, now, now_ns, config, previous_atm_diff=-0.2)
    assert result.status in {"SIGNAL", "NEUTRAL"}
    assert result.value is not None

    stale = (
        quote(26000, "CE", 0.16, now_ns, captured_ns=now_ns - 91_000_000_000),
        fresh[1],
    )
    waiting = evaluate_flip(stale, 26000, now, now_ns, config, previous_atm_diff=-0.2)
    assert waiting.status == "WAITING"

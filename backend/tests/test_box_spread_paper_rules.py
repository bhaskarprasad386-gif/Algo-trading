from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.scanner import box_spread_paper_rule_routes as rule_routes
from app.scanner.box_spread_paper_rules import (
    BoxSpreadPaperEntryQuotaReservation,
    validate_rule_config, entry_matches, exit_matches, compare, select_entry_candidates,
)


def base_config():
    return {
        "entry": {
            "symbols": ["NIFTY", "SENSEX"], "direction": "LONG",
            "instrument_class": "INDEX", "expiry_type": "WEEKLY",
            "metric": "executable_edge", "operator": ">=", "threshold": 20,
            "lots": 5,
        },
        "exit": {
            "metric": "executable_edge", "operator": "<=", "threshold": 1,
            "hold_to_expiry": False,
        },
        "monitor": {"interval_seconds": 20},
    }


def test_custom_rule_accepts_entry_20_and_exit_at_1_with_20_second_monitor():
    config = validate_rule_config(base_config())
    assert config["entry"]["threshold"] == 20
    assert config["entry"]["lots"] == 5
    assert config["exit"]["threshold"] == 1
    assert config["monitor"]["interval_seconds"] == 20


def test_range_entry_quota_defaults_to_two_trades_per_fifteen_minute_window():
    config = validate_rule_config(base_config())
    assert config["entry"]["max_trades_per_interval"] == 2
    assert config["monitor"]["entry_window_minutes"] == 15


def test_range_entry_quota_is_customizable():
    raw = base_config()
    raw["entry"]["max_trades_per_interval"] = 4
    raw["monitor"]["entry_window_minutes"] = 30
    config = validate_rule_config(raw)
    assert config["entry"]["max_trades_per_interval"] == 4
    assert config["monitor"]["entry_window_minutes"] == 30


@pytest.mark.parametrize("limit", [0, 21, -1])
def test_range_entry_quota_rejects_invalid_trade_limits(limit):
    raw = base_config()
    raw["entry"]["max_trades_per_interval"] = limit
    with pytest.raises(ValueError, match="max_trades_per_interval"):
        validate_rule_config(raw)


@pytest.mark.parametrize("minutes", [0, 241, -1])
def test_entry_window_rejects_invalid_durations(minutes):
    raw = base_config()
    raw["monitor"]["entry_window_minutes"] = minutes
    with pytest.raises(ValueError, match="entry_window_minutes"):
        validate_rule_config(raw)


def test_entry_range_inclusively_matches_min_and_max_for_edge_and_gross_profit():
    raw = base_config()
    raw["entry"].update({"range_enabled": True, "range_min": 20, "range_max": 25})
    config = validate_rule_config(raw)
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY",
        "direction": "LONG", "executable_edge": 20, "gross_pnl": 24.99,
    }
    assert entry_matches(row, config) == (True, "entry_range_met")
    row["executable_edge"] = 25
    assert entry_matches(row, config) == (True, "entry_range_met")
    row["executable_edge"] = 25.01
    assert entry_matches(row, config) == (False, "entry_outside_selected_range")

    raw["entry"].update({"metric": "gross_pnl", "range_min": 100, "range_max": 250})
    config = validate_rule_config(raw)
    row["gross_pnl"] = 100
    assert entry_matches(row, config) == (True, "entry_range_met")
    row["gross_pnl"] = 250
    assert entry_matches(row, config) == (True, "entry_range_met")
    row["gross_pnl"] = 99.99
    assert entry_matches(row, config) == (False, "entry_outside_selected_range")


@pytest.mark.parametrize(("minimum", "maximum"), [(10, 9), (float("nan"), 10), (0, float("inf"))])
def test_entry_range_rejects_invalid_bounds(minimum, maximum):
    raw = base_config()
    raw["entry"].update({"range_enabled": True, "range_min": minimum, "range_max": maximum})
    with pytest.raises(ValueError, match="entry range"):
        validate_rule_config(raw)


def test_entry_selection_rejects_empty_or_expiry_incompatible_indices():
    raw = base_config()
    raw["entry"]["symbols"] = []
    with pytest.raises(ValueError, match="at least one index"):
        validate_rule_config(raw)
    raw = base_config()
    raw["entry"]["symbols"] = ["BANKNIFTY"]
    with pytest.raises(ValueError, match="not supported for the selected expiry"):
        validate_rule_config(raw)


def test_custom_rule_entry_filters_symbol_direction_and_edge():
    config = validate_rule_config(base_config())
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY",
        "direction": "LONG", "executable_edge": 20.25,
    }
    assert entry_matches(row, config) == (True, "entry_conditions_met")
    row["executable_edge"] = 19.99
    assert entry_matches(row, config) == (False, "entry_threshold_not_met")
    row["symbol"] = "BANKNIFTY"
    assert entry_matches(row, config) == (False, "symbol_not_selected")


@pytest.mark.parametrize(("edge", "expected"), [(1.0, True), (0.0, True), (1.01, False), (-0.2, True)])
def test_custom_exit_edge_threshold(edge, expected):
    config = validate_rule_config(base_config())
    matched, reason, observed = exit_matches({"executable_edge": edge}, config)
    assert matched is expected
    assert observed == edge
    assert reason == ("exit_threshold_met" if expected else "exit_threshold_not_met")


def test_custom_rule_hold_to_expiry_can_be_selected_without_exit_threshold():
    raw = base_config()
    raw["exit"] = {"hold_to_expiry": True}
    config = validate_rule_config(raw)
    assert config["exit"]["hold_to_expiry"] is True


@pytest.mark.parametrize("interval", [0, 4, 3601, -20])
def test_custom_rule_rejects_unsafe_monitor_interval(interval):
    raw = base_config()
    raw["monitor"]["interval_seconds"] = interval
    with pytest.raises(ValueError):
        validate_rule_config(raw)


@pytest.mark.parametrize("metric", ["ltp", "spot_price", "__dict__"])
def test_custom_rule_rejects_unsupported_metrics(metric):
    raw = base_config()
    raw["entry"]["metric"] = metric
    with pytest.raises(ValueError):
        validate_rule_config(raw)


def test_compare_supports_inclusive_and_strict_thresholds():
    assert compare(1.0, "<=", 1.0)
    assert not compare(1.0, "<", 1.0)
    assert compare(20.0, ">=", 20.0)
    assert not compare(20.0, ">", 20.0)

def test_exit_order_type_defaults_to_limit_and_market_is_supported():
    config = validate_rule_config(base_config())
    assert config["exit"]["order_type"] == "LIMIT"

    raw = base_config()
    raw["exit"]["order_type"] = "market"
    config = validate_rule_config(raw)
    assert config["exit"]["order_type"] == "MARKET"


@pytest.mark.parametrize("order_type", ["STOP", "IOC", ""])
def test_exit_order_type_rejects_unsupported_values(order_type):
    raw = base_config()
    raw["exit"]["order_type"] = order_type
    with pytest.raises(ValueError, match="exit.order_type"):
        validate_rule_config(raw)


@pytest.mark.parametrize("metric", ["gross_profit_per_lot", "net_profit_per_lot"])
def test_per_lot_profit_exit_metrics_use_server_calculated_override(metric):
    raw = base_config()
    raw["exit"] = {
        "metric": metric, "operator": ">=",
        "threshold": 100, "hold_to_expiry": False,
    }
    config = validate_rule_config(raw)
    assert config["exit"]["metric"] == metric
    assert exit_matches({}, config, metric_override=100.01) == (
        True, "exit_threshold_met", 100.01,
    )
    assert exit_matches({}, config, metric_override=99.99) == (
        False, "exit_threshold_not_met", 99.99,
    )
    assert exit_matches({}, config) == (
        False, "exit_metric_unavailable", None,
    )


def test_remaining_edge_exit_uses_current_executable_edge_as_remaining_points():
    raw = base_config()
    raw["exit"] = {
        "metric": "remaining_edge_per_lot", "operator": "<=",
        "threshold": 2, "hold_to_expiry": False,
    }
    config = validate_rule_config(raw)
    # Entered at 20 points; at 2 points remaining, 18 points have been captured.
    assert exit_matches({"executable_edge": 2}, config) == (
        True, "exit_threshold_met", 2.0,
    )
    assert exit_matches({"executable_edge": 2.5}, config) == (
        False, "exit_threshold_not_met", 2.5,
    )
    assert exit_matches({}, config) == (
        False, "exit_metric_unavailable", None,
    )


def test_position_net_pnl_exit_threshold_uses_server_calculated_override():
    raw = base_config()
    raw["exit"] = {
        "metric": "position_net_pnl", "operator": ">=",
        "threshold": 500, "hold_to_expiry": False,
    }
    config = validate_rule_config(raw)
    assert config["exit"]["metric"] == "position_net_pnl"
    assert exit_matches({}, config, metric_override=500.01) == (
        True, "exit_threshold_met", 500.01,
    )
    assert exit_matches({}, config, metric_override=499.99) == (
        False, "exit_threshold_not_met", 499.99,
    )
    assert exit_matches({}, config) == (
        False, "exit_metric_unavailable", None,
    )



def test_range_entry_selects_only_two_highest_edge_candidates():
    raw = base_config()
    raw["entry"].update({"range_enabled": True, "range_min": 20, "range_max": 40})
    config = validate_rule_config(raw)
    rows = [
        {"symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY", "direction": "LONG", "executable_edge": value, "low_strike": i, "high_strike": i + 100}
        for i, value in enumerate((21, 39, 35, 30), start=1)
    ]
    selected, skipped = select_entry_candidates(rows, config)
    assert [row["executable_edge"] for row in selected] == [39, 35]
    assert [reason for _, reason in skipped] == ["range_trade_limit_reached", "range_trade_limit_reached"]


def test_range_entry_selects_only_two_highest_gross_profit_candidates():
    raw = base_config()
    raw["entry"].update({"metric": "gross_pnl", "range_enabled": True, "range_min": 100, "range_max": 500})
    config = validate_rule_config(raw)
    rows = [
        {"symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY", "direction": "LONG", "gross_pnl": value, "low_strike": i, "high_strike": i + 100}
        for i, value in enumerate((110, 480, 350, 250), start=1)
    ]
    selected, skipped = select_entry_candidates(rows, config)
    assert [row["gross_pnl"] for row in selected] == [480, 350]
    assert len(skipped) == 2
    assert all(reason == "range_trade_limit_reached" for _, reason in skipped)


def test_non_range_entry_keeps_all_matching_candidates():
    config = validate_rule_config(base_config())
    rows = [
        {"symbol": "NIFTY", "instrument_class": "INDEX", "expiry_type": "WEEKLY", "direction": "LONG", "executable_edge": value}
        for value in (21, 25, 30)
    ]
    selected, skipped = select_entry_candidates(rows, config)
    assert len(selected) == 3
    assert skipped == []



def test_interval_quota_reservations_are_atomic_across_workers(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'box-paper-quota.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperEntryQuotaReservation.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(rule_routes, "SessionLocal", factory)
    start = datetime(2026, 10, 30, 4, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    barrier = Barrier(2)

    def reserve(candidate):
        barrier.wait(timeout=5)
        return rule_routes._reserve_rule_entry_quota_slot(
            7, "default", start, end, 1, candidate,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ("candidate-a", "candidate-b")))
        assert sorted(value is not None for value in results) == [False, True]
        with factory() as session:
            reservations = session.query(BoxSpreadPaperEntryQuotaReservation).all()
        assert len(reservations) == 1
        assert reservations[0].slot == 1
    finally:
        engine.dispose()


def test_quota_reservation_blocks_same_candidate_in_later_window(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'box-paper-quota-idempotency.db'}")
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperEntryQuotaReservation.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(rule_routes, "SessionLocal", factory)
    start = datetime(2026, 10, 30, 4, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    next_start = end
    next_end = next_start + timedelta(minutes=15)

    try:
        first = rule_routes._reserve_rule_entry_quota_slot(
            7, "default", start, end, 2, "same-candidate",
        )
        replay = rule_routes._reserve_rule_entry_quota_slot(
            7, "default", next_start, next_end, 2, "same-candidate",
        )
        assert first == 1
        assert replay is None
        with factory() as session:
            assert session.query(BoxSpreadPaperEntryQuotaReservation).count() == 1
    finally:
        engine.dispose()



def test_interval_quota_never_reserves_more_than_configured_slots(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'box-paper-quota-multi-slot.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperEntryQuotaReservation.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(rule_routes, "SessionLocal", factory)
    start = datetime(2026, 10, 30, 4, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    barrier = Barrier(4)

    def reserve(candidate):
        barrier.wait(timeout=5)
        return rule_routes._reserve_rule_entry_quota_slot(
            9, "default", start, end, 2, candidate,
        )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, ("candidate-a", "candidate-b", "candidate-c", "candidate-d")))
        assert sum(value is not None for value in results) == 2
        with factory() as session:
            reservations = session.query(BoxSpreadPaperEntryQuotaReservation).all()
        assert len(reservations) == 2
        assert {item.slot for item in reservations} == {1, 2}
    finally:
        engine.dispose()


def test_automatic_rule_exit_skips_unequal_open_legs_without_writing(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.scanner import box_spread_paper_routes as paper
    from app.scanner import live_box_spread_routes

    config = validate_rule_config(base_config())
    config["exit"]["metric"] = "position_net_pnl"
    trade = SimpleNamespace(id=43, expiry="2026-11-05")
    row = {"executable_edge": 0.0}
    records = []

    monkeypatch.setattr(rule_routes, "_open_rule_trades", lambda *_args: [trade])
    monkeypatch.setattr(rule_routes, "_record", lambda *args, **kwargs: records.append((args, kwargs)))
    monkeypatch.setattr(paper, "_load_trade_request", lambda *_args: object())
    monkeypatch.setattr(paper, "_resolve", lambda *_args: SimpleNamespace(direction="LONG"))
    monkeypatch.setattr(paper, "_matching_live_row", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(
        rule_routes, "_signed_open_quantities",
        lambda *_args: {"LOW_CE": 5, "HIGH_CE": -5, "HIGH_PE": 5, "LOW_PE": -4},
    )
    monkeypatch.setattr(
        paper._ledger, "get_trade_summary",
        lambda *_args, **_kwargs: pytest.fail("unequal positions must not be marked or priced"),
    )
    monkeypatch.setattr(
        paper, "_execute_exit_basket_from_row",
        lambda *_args, **_kwargs: pytest.fail("automatic exit must not write an unequal basket"),
    )
    monkeypatch.setattr(paper, "_paper_execution_enabled", False)
    monkeypatch.setattr(live_box_spread_routes, "monitor_snapshot", lambda **_kwargs: {
        "fresh": True, "data": [row], "entry_data": [],
    })

    asyncio.run(rule_routes._run_rule_cycle(7, "default", config, rule_enabled=False))

    exit_records = [args for args, _ in records if len(args) > 3 and args[2] == "EXIT_CHECK"]
    assert any(args[3] == "SKIPPED" for args in exit_records)
    assert any("incomplete_or_unequal_four_leg_exposure" in args[4] for args in exit_records)
    assert not any(args[3] == "EXIT_RECORDED" for args in exit_records)


def test_automatic_rule_exit_uses_shared_four_leg_executor_even_when_entry_rule_disabled(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.scanner import box_spread_paper_routes as paper
    from app.scanner import live_box_spread_routes

    config = validate_rule_config(base_config())
    config["exit"]["order_type"] = "MARKET"
    trade = SimpleNamespace(id=42, expiry="2026-11-05")
    row = {"executable_edge": 0.0}
    calls = []
    records = []

    monkeypatch.setattr(rule_routes, "_open_rule_trades", lambda *_args: [trade])
    monkeypatch.setattr(rule_routes, "_record", lambda *args, **kwargs: records.append((args, kwargs)))
    monkeypatch.setattr(paper, "_load_trade_request", lambda *_args: object())
    monkeypatch.setattr(paper, "_resolve", lambda *_args: SimpleNamespace(direction="LONG"))
    monkeypatch.setattr(paper, "_matching_live_row", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(
        rule_routes, "_signed_open_quantities",
        lambda *_args: {"LOW_CE": 5, "HIGH_CE": -5, "HIGH_PE": 5, "LOW_PE": -5},
    )
    monkeypatch.setattr(
        paper, "_execute_exit_basket_from_row",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "CLOSED"},
    )
    monkeypatch.setattr(paper, "_paper_execution_enabled", False)
    monkeypatch.setattr(live_box_spread_routes, "monitor_snapshot", lambda **_kwargs: {
        "fresh": True, "data": [row], "entry_data": [],
    })

    asyncio.run(rule_routes._run_rule_cycle(7, "default", config, rule_enabled=False))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:3] == (42, "default", row)
    assert kwargs == {"order_type": "MARKET", "fill_id": "rule-exit-7-42"}
    assert any(args[3] == "EXIT_RECORDED" for args, _ in records if len(args) > 3)



def test_automatic_rule_exit_skips_when_shared_four_leg_executor_rejects_depth(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app.scanner import box_spread_paper_routes as paper
    from app.scanner import live_box_spread_routes

    config = validate_rule_config(base_config())
    trade = SimpleNamespace(id=44, expiry="2026-11-05")
    row = {"executable_edge": 0.0}
    records = []
    attempts = []

    monkeypatch.setattr(rule_routes, "_open_rule_trades", lambda *_args: [trade])
    monkeypatch.setattr(rule_routes, "_record", lambda *args, **kwargs: records.append((args, kwargs)))
    monkeypatch.setattr(paper, "_load_trade_request", lambda *_args: object())
    monkeypatch.setattr(paper, "_resolve", lambda *_args: SimpleNamespace(direction="LONG"))
    monkeypatch.setattr(paper, "_matching_live_row", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(
        rule_routes, "_signed_open_quantities",
        lambda *_args: {"LOW_CE": 5, "HIGH_CE": -5, "HIGH_PE": 5, "LOW_PE": -5},
    )

    def reject_depth(*args, **kwargs):
        attempts.append((args, kwargs))
        raise HTTPException(
            status_code=409,
            detail="insufficient executable depth for HIGH_CE; no legs were exited",
        )

    monkeypatch.setattr(paper, "_execute_exit_basket_from_row", reject_depth)
    monkeypatch.setattr(paper, "_paper_execution_enabled", False)
    monkeypatch.setattr(live_box_spread_routes, "monitor_snapshot", lambda **_kwargs: {
        "fresh": True, "data": [row], "entry_data": [],
    })

    asyncio.run(rule_routes._run_rule_cycle(7, "default", config, rule_enabled=False))

    assert len(attempts) == 1
    exit_records = [args for args, _ in records if len(args) > 3 and args[2] == "EXIT_CHECK"]
    assert any(args[3] == "SKIPPED" for args in exit_records)
    assert any("shared_four_leg_exit_rejected" in args[4] for args in exit_records)
    assert not any(args[3] == "EXIT_RECORDED" for args in exit_records)

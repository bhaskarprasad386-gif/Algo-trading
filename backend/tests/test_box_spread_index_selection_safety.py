import pytest
from fastapi import HTTPException

from app import main


def test_index_deselection_is_blocked_while_paper_position_is_open(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(main, "_box_spread_open_paper_symbols", lambda: {"BANKNIFTY"})
    saved = []
    monkeypatch.setattr(main, "_save_box_spread_index_selection", lambda symbols, weekly_only_symbols=(): saved.append((tuple(symbols), tuple(weekly_only_symbols))))

    with pytest.raises(HTTPException) as error:
        main.set_box_spread_index_selection(main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY"]))

    assert error.value.status_code == 409
    assert error.value.detail["blocked_indices"] == ["BANKNIFTY"]
    assert saved == []


def test_index_deselection_fails_closed_if_paper_ledger_cannot_be_checked(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))

    def unavailable():
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(main, "_box_spread_open_paper_symbols", unavailable)
    saved = []
    monkeypatch.setattr(main, "_save_box_spread_index_selection", lambda symbols: saved.append(tuple(symbols)))

    with pytest.raises(HTTPException) as error:
        main.set_box_spread_index_selection(main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY"]))

    assert error.value.status_code == 503
    assert saved == []


def test_index_deselection_is_allowed_after_all_paper_legs_are_flat(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(main, "_box_spread_open_paper_symbols", lambda: set())
    saved = []
    monkeypatch.setattr(main, "_save_box_spread_index_selection", lambda symbols, weekly_only_symbols=(): saved.append((tuple(symbols), tuple(weekly_only_symbols))) or tuple(symbols))

    result = main.set_box_spread_index_selection(main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY"]))

    assert result["status"] == "success"
    assert result["symbols"] == ["NIFTY"]
    assert saved == [(('NIFTY',), ())]



def test_saved_index_selection_read_failure_does_not_fall_back_to_all_or_default(monkeypatch):
    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("database unavailable")

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(main, "SessionLocal", lambda: BrokenSession())

    with pytest.raises(RuntimeError, match="index selection is unavailable"):
        main._get_box_spread_index_selection()

def test_index_selection_post_route_uses_save_handler():
    matching = [
        route for route in main.app.routes
        if getattr(route, "path", None) == "/api/v1/scanner/box-spread/index-selection"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]

    assert len(matching) == 1
    assert matching[0].endpoint is main.set_box_spread_index_selection



def test_selection_fails_closed_when_active_runner_has_no_handoff_supervisor(monkeypatch):
    import threading
    from types import SimpleNamespace

    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY",))
    monkeypatch.setattr(
        main,
        "live_box_spread_runner",
        SimpleNamespace(stop_event=threading.Event(), master=object()),
    )
    monkeypatch.setattr(main, "live_box_spread_supervisor", None)
    saved = []
    monkeypatch.setattr(
        main,
        "_save_box_spread_index_selection",
        lambda symbols: saved.append(tuple(symbols)),
    )

    with pytest.raises(HTTPException) as error:
        main.set_box_spread_index_selection(
            main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY", "SENSEX"])
        )

    assert error.value.status_code == 503
    assert saved == []

def test_spot_index_universe_follows_active_runner_during_staged_handoff(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("BANKEX", "SENSEX"))
    monkeypatch.setattr(
        main,
        "live_box_spread_runner",
        SimpleNamespace(targets=[
            SimpleNamespace(underlying="NIFTY"),
            SimpleNamespace(underlying="BANKNIFTY"),
            SimpleNamespace(underlying="NIFTY"),
        ]),
    )

    assert main._get_box_spread_active_index_symbols() == ("BANKNIFTY", "NIFTY")


def test_spot_index_universe_uses_saved_selection_before_runner_starts(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("SENSEX", "BANKEX"))
    monkeypatch.setattr(main, "live_box_spread_runner", None)

    assert main._get_box_spread_active_index_symbols() == ("SENSEX", "BANKEX")

def test_live_results_continue_using_old_active_indices_while_new_selection_stages(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        low=SimpleNamespace(underlying="NIFTY", timestamp_ns=1),
        high=SimpleNamespace(timestamp_ns=1),
        executable_edge=12.0,
    )
    published = []
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("BANKEX",))
    monkeypatch.setattr(main, "_get_box_spread_active_index_symbols", lambda: ("NIFTY",))
    monkeypatch.setattr(main, "_fresh_live_box_spread_results", lambda rows: tuple(rows))
    monkeypatch.setattr(main, "_box_spread_result_key", lambda item: ("NIFTY",))
    monkeypatch.setattr(main, "publish_live_box_spread_opportunities", lambda rows: published.extend(rows))
    monkeypatch.setattr(main, "live_box_spread_latest_results", ())

    main._update_live_box_spread_results((row,))

    assert main.live_box_spread_latest_results == (row,)
    assert published == [row]

def test_box_spread_index_universe_and_presets_cover_all_six_supported_indices():
    expected = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

    assert set(main.OPTION_STRATEGY_INDEX_SYMBOLS) == expected
    assert set(main.BOX_SPREAD_INDEX_PRESETS["ALL"]) == expected
    assert set(main.BOX_SPREAD_INDEX_PRESETS["NIFTY_SENSEX"]) == {"NIFTY", "SENSEX"}
    assert set(main.BOX_SPREAD_INDEX_PRESETS["NIFTY_SENSEX_BANKNIFTY"]) == {
        "NIFTY", "SENSEX", "BANKNIFTY"
    }


def test_box_spread_target_builder_uses_only_selected_indices_and_correct_exchange_segments():
    from types import SimpleNamespace

    expiry = "31DEC2099"
    instruments = []
    for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
        instruments.append({
            "exch_seg": "NFO", "instrumenttype": "OPTIDX",
            "name": symbol, "expiry": expiry,
        })
    for symbol in ("SENSEX", "BANKEX"):
        instruments.append({
            "exch_seg": "BFO", "instrumenttype": "OPTIDX",
            "name": symbol, "expiry": expiry,
        })
    # Wrong-exchange and non-index contracts must never satisfy an index target.
    instruments.extend([
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "SENSEX", "expiry": expiry},
        {"exch_seg": "BFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": expiry},
        {"exch_seg": "NFO", "instrumenttype": "OPTSTK", "name": "BANKNIFTY", "expiry": expiry},
    ])
    master = SimpleNamespace(instruments=instruments)

    targets, _ = main._box_spread_targets_from_master(master, ["SENSEX", "NIFTY", "BANKEX"])

    assert {(target.underlying, target.instrument_class) for target in targets} == {
        ("NIFTY", "INDEX"), ("SENSEX", "INDEX"), ("BANKEX", "INDEX")
    }
    assert len(targets) == 3
    assert all(target.expiry == expiry for target in targets)


def test_box_spread_all_index_selection_normalizes_to_exact_six_supported_symbols():
    selected = main._normalize_box_spread_index_symbols([
        "nifty", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "sensex", "BANKEX", "NIFTY"
    ])

    assert set(selected) == {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    assert len(selected) == 6



def test_weekly_only_target_builder_never_adds_monthly_contracts(monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    weekly_raw = "31DEC2099"
    monthly_raw = "30DEC2099"
    weekly_day = date(2099, 12, 31)
    monthly_day = date(2099, 12, 30)
    instruments = [
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": weekly_raw},
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": monthly_raw},
    ]
    monkeypatch.setattr(
        main,
        "classify_index_expiry_dates",
        lambda symbol, dates: {weekly_day: "WEEKLY", monthly_day: "MONTHLY"},
    )

    weekly_targets, _ = main._box_spread_targets_from_master(
        SimpleNamespace(instruments=instruments), ["NIFTY"], ["NIFTY"]
    )
    standard_targets, _ = main._box_spread_targets_from_master(
        SimpleNamespace(instruments=instruments), ["NIFTY"]
    )

    assert [(target.expiry, target.weekly_only) for target in weekly_targets] == [(weekly_raw, True)]
    assert {target.expiry for target in standard_targets} == {weekly_raw, monthly_raw}
    assert all(target.weekly_only is False for target in standard_targets)


def test_weekly_only_selection_is_limited_to_selected_nifty_or_sensex():
    with pytest.raises(ValueError, match="weekly-only mode is supported only"):
        main._save_box_spread_index_selection(["BANKNIFTY", "FINNIFTY"], ["FINNIFTY"])


def test_weekly_only_selection_must_also_be_selected():
    with pytest.raises(ValueError, match="weekly-only indices must also be included"):
        main._save_box_spread_index_selection(["SENSEX"], ["NIFTY"])

def test_sensex_weekly_only_target_builder_excludes_monthly_and_marks_target(monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    weekly_raw = "31DEC2099"
    monthly_raw = "30DEC2099"
    weekly_day = date(2099, 12, 31)
    monthly_day = date(2099, 12, 30)
    instruments = [
        {"exch_seg": "BFO", "instrumenttype": "OPTIDX", "name": "SENSEX", "expiry": weekly_raw},
        {"exch_seg": "BFO", "instrumenttype": "OPTIDX", "name": "SENSEX", "expiry": monthly_raw},
    ]
    monkeypatch.setattr(
        main,
        "classify_index_expiry_dates",
        lambda symbol, dates: {weekly_day: "WEEKLY", monthly_day: "MONTHLY"},
    )

    targets, expiry_types = main._box_spread_targets_from_master(
        SimpleNamespace(instruments=instruments), ["SENSEX"], ["SENSEX"]
    )

    assert [(target.underlying, target.expiry, target.weekly_only) for target in targets] == [
        ("SENSEX", weekly_raw, True)
    ]
    assert expiry_types[("SENSEX", monthly_raw)] == "MONTHLY"


def test_weekly_only_without_classified_weekly_expiry_stays_spot_only(monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    monthly_raw = "30DEC2099"
    monthly_day = date(2099, 12, 30)
    instruments = [
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": monthly_raw},
    ]
    monkeypatch.setattr(
        main,
        "classify_index_expiry_dates",
        lambda symbol, dates: {monthly_day: "MONTHLY"},
    )

    targets, _ = main._box_spread_targets_from_master(
        SimpleNamespace(instruments=instruments), ["NIFTY"], ["NIFTY"]
    )

    assert len(targets) == 1
    assert targets[0].expiry is None
    assert targets[0].weekly_only is True


def test_midcpnifty_is_excluded_from_box_spread_live_targets_only():
    from types import SimpleNamespace

    expiry = "31DEC2099"
    instruments = [
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": symbol, "expiry": expiry}
        for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
    ]

    targets, _ = main._box_spread_targets_from_master(
        SimpleNamespace(instruments=instruments),
        ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
    )

    assert {target.underlying for target in targets} == {
        "NIFTY", "BANKNIFTY", "FINNIFTY"
    }
    assert "MIDCPNIFTY" in main.OPTION_STRATEGY_INDEX_SYMBOLS

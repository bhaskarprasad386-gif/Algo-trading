import pytest
from fastapi import HTTPException

from app import main


def test_index_deselection_no_longer_depends_on_retired_paper_ledger(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(main, "live_box_spread_runner", None)
    monkeypatch.setattr(main, "live_box_spread_supervisor", None)
    saved = []
    monkeypatch.setattr(
        main,
        "_save_box_spread_index_selection",
        lambda symbols, weekly_only_symbols=(): saved.append((tuple(symbols), tuple(weekly_only_symbols))) or tuple(symbols),
    )

    result = main.set_box_spread_index_selection(main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY"]))

    assert result["status"] == "success"
    assert result["symbols"] == ["NIFTY"]
    assert saved == [(("NIFTY",), ())]

def test_legacy_saved_selection_is_clamped_to_nifty_and_banknifty(monkeypatch):
    from types import SimpleNamespace

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def get(self, *_args):
            return SimpleNamespace(
                symbols_json='["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"]'
            )

    monkeypatch.setattr(main, "SessionLocal", lambda: Session())
    assert main._get_box_spread_index_selection() == ("BANKNIFTY", "NIFTY")


def test_legacy_selection_with_no_locked_indices_falls_back_only_to_locked_defaults(monkeypatch):
    from types import SimpleNamespace

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def get(self, *_args):
            return SimpleNamespace(symbols_json='["SENSEX", "BANKEX"]')

    monkeypatch.setattr(main, "SessionLocal", lambda: Session())
    assert main._get_box_spread_index_selection() == ("NIFTY", "BANKNIFTY")


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
            main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY", "BANKNIFTY"])
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
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(main, "live_box_spread_runner", None)

    assert main._get_box_spread_active_index_symbols() == ("NIFTY", "BANKNIFTY")

def test_live_results_continue_using_old_active_indices_while_new_selection_stages(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(
        low=SimpleNamespace(underlying="NIFTY", timestamp_ns=1),
        high=SimpleNamespace(timestamp_ns=1),
        executable_edge=12.0,
    )
    published = []
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("BANKNIFTY",))
    monkeypatch.setattr(main, "_get_box_spread_active_index_symbols", lambda: ("NIFTY",))
    monkeypatch.setattr(main, "_fresh_live_box_spread_results", lambda rows: tuple(rows))
    monkeypatch.setattr(main, "_box_spread_result_key", lambda item: ("NIFTY",))
    monkeypatch.setattr(main, "publish_live_box_spread_opportunities", lambda rows: published.extend(rows))
    monkeypatch.setattr(main, "live_box_spread_latest_results", ())

    main._update_live_box_spread_results((row,))

    assert main.live_box_spread_latest_results == (row,)
    assert published == [row]

def test_box_spread_index_universe_and_presets_are_locked_to_nifty_and_banknifty():
    assert set(main.OPTION_STRATEGY_INDEX_SYMBOLS) == {
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"
    }
    assert set(main.BOX_SPREAD_LIVE_INDEX_UNIVERSE) == {"NIFTY", "BANKNIFTY"}
    assert set(main.BOX_SPREAD_INDEX_PRESETS["ALL"]) == {"NIFTY", "BANKNIFTY"}
    assert set(main.BOX_SPREAD_INDEX_PRESETS["NIFTY_BANKNIFTY"]) == {"NIFTY", "BANKNIFTY"}


def test_box_spread_target_builder_uses_only_selected_indices_and_correct_exchange_segments(monkeypatch):
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
    from datetime import date
    parsed_expiry = date(2099, 12, 31)
    monkeypatch.setattr(
        main,
        "classify_index_expiry_dates",
        lambda symbol, dates: {
            parsed_expiry: "WEEKLY" if symbol == "NIFTY" else "MONTHLY"
        },
    )

    targets, _ = main._box_spread_targets_from_master(master, ["NIFTY", "BANKNIFTY"])

    assert {(target.underlying, target.instrument_class) for target in targets} == {
        ("NIFTY", "INDEX"), ("BANKNIFTY", "INDEX")
    }
    assert len(targets) == 2
    assert all(target.expiry == expiry for target in targets)


def test_box_spread_live_selection_is_locked_to_nifty_and_banknifty():
    selected = main._normalize_box_spread_index_symbols(["nifty", "BANKNIFTY", "NIFTY"])
    assert selected == ("BANKNIFTY", "NIFTY")
    assert main.BOX_SPREAD_DEFAULT_INDEX_SELECTION == ("NIFTY", "BANKNIFTY")


@pytest.mark.parametrize("symbol", ["FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"])
def test_box_spread_live_selection_rejects_unlocked_indices(symbol):
    with pytest.raises(ValueError, match="locked to NIFTY and BANKNIFTY"):
        main._normalize_box_spread_index_symbols(["NIFTY", symbol])



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
    assert [(target.expiry, target.weekly_only) for target in standard_targets] == [(weekly_raw, False)]


def test_weekly_only_selection_is_limited_to_nifty():
    with pytest.raises(ValueError, match="locked to NIFTY and BANKNIFTY"):
        main._save_box_spread_index_selection(["NIFTY", "BANKNIFTY", "FINNIFTY"], ["NIFTY"])


def test_weekly_only_selection_must_also_be_selected():
    with pytest.raises(ValueError, match="weekly-only indices must also be included"):
        main._save_box_spread_index_selection(["BANKNIFTY"], ["NIFTY"])

def test_sensex_is_rejected_from_box_spread_live_targets():
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="locked to NIFTY and BANKNIFTY"):
        main._box_spread_targets_from_master(SimpleNamespace(instruments=[]), ["SENSEX"], ["SENSEX"])


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


def test_midcpnifty_and_other_indices_are_rejected_from_box_spread_live_targets():
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="locked to NIFTY and BANKNIFTY"):
        main._box_spread_targets_from_master(
            SimpleNamespace(instruments=[]), ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
        )


def test_box_spread_page_push_is_coalesced_at_250ms_not_one_second(monkeypatch):
    clock = {"now": 10.0}
    published = []
    monkeypatch.setattr(main.time_module, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(main, "_last_box_page_push_at", 0.0)
    monkeypatch.setattr(main, "live_box_spread_latest_results", ())
    monkeypatch.setattr(main, "_get_box_spread_active_index_symbols", lambda: ())
    monkeypatch.setattr(main, "_fresh_live_box_spread_results", lambda rows: tuple(rows))
    monkeypatch.setattr(main, "_box_page_push_snapshot", lambda: {"opportunities": []})
    monkeypatch.setattr(main, "publish_page_update", lambda *args: published.append(("public", args)))
    monkeypatch.setattr(main, "_publish_credit_butterfly_condor_pages", lambda: None)
    monkeypatch.setattr(main, "publish_live_box_spread_opportunities", lambda rows: None)

    main._update_live_box_spread_results(())
    assert len(published) == 1
    clock["now"] += 0.10
    main._update_live_box_spread_results(())
    assert len(published) == 1
    clock["now"] += 0.16
    main._update_live_box_spread_results(())
    assert len(published) == 2


def test_box_paper_trading_http_and_private_websocket_routes_are_not_mounted():
    paths = [
        getattr(route, "path", "")
        for route in main.app.routes
    ]
    assert not any(path.startswith("/api/v1/scanner/box-spread-paper") for path in paths)

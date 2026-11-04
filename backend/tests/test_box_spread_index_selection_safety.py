import pytest
from fastapi import HTTPException

from app import main


def test_index_deselection_is_blocked_while_paper_position_is_open(monkeypatch):
    monkeypatch.setattr(main, "_get_box_spread_index_selection", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(main, "_box_spread_open_paper_symbols", lambda: {"BANKNIFTY"})
    saved = []
    monkeypatch.setattr(main, "_save_box_spread_index_selection", lambda symbols: saved.append(tuple(symbols)))

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
    monkeypatch.setattr(main, "_save_box_spread_index_selection", lambda symbols: saved.append(tuple(symbols)) or tuple(symbols))

    result = main.set_box_spread_index_selection(main.BoxSpreadIndexSelectionRequest(symbols=["NIFTY"]))

    assert result["status"] == "success"
    assert result["symbols"] == ["NIFTY"]
    assert saved == [("NIFTY",)]



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


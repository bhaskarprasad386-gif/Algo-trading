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

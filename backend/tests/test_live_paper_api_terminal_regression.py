"""Regression coverage for live-paper terminal API responses and ledger safety."""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade
from app import live_paper_routes as routes


def _enable(db, user_id=1, amount=100000):
    db.add(GlobalPaperSetting(
        user_id=user_id,
        enabled=True,
        paper_amount=amount,
        emergency_stop=False,
    ))
    db.commit()


def _trade(db, *, event_id="API-TERMINAL", user_id=1, expiry="2026-10-30"):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future",
        symbol="API",
        event_id=event_id,
        direction="LONG",
        expiry=expiry,
        earliest_expiry=expiry,
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        legs=[{"instrument": "CASH", "side": "BUY", "price": 100.0}],
        metadata={"exchange": "NFO", "contract": "API"},
        user_id=user_id,
    )
    assert created is True
    return trade


def test_manual_close_api_returns_terminal_trade_with_realized_pnl(db_session, monkeypatch):
    _enable(db_session)
    trade = _trade(db_session)

    # Keep the API test deterministic: no live scanner dependency.
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)
    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)

    LivePaperTradeService().mark(db_session, trade, edge=8.0, pnl_override=-20.0)
    response = routes.close(trade.id, db_session)

    assert response["status"] == "success"
    row = response["trade"]
    assert row["id"] == trade.id
    assert row["status"] == "COMPLETED"
    assert row["exit_reason"] == "MANUAL"
    assert row["unrealized_pnl"] == -20.0
    assert row["realized_pnl"] == -20.0
    assert row["pnl_pct"] == round(-20.0 / 30000.0 * 100.0, 8)
    assert row["capital_used"] == 30000.0
    assert row["lots"] == 1
    assert row["lot_size"] == 10
    assert row["legs"] == [{"instrument": "CASH", "side": "BUY", "price": 100.0}]


def test_expiry_detail_api_returns_completed_terminal_state(db_session, monkeypatch):
    _enable(db_session)
    trade = _trade(db_session, event_id="API-EXPIRY", expiry="2026-10-04")
    LivePaperTradeService().mark(db_session, trade, edge=5.0, pnl_override=125.0)

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)

    row = routes.detail(trade.id, db_session)

    assert row["id"] == trade.id
    assert row["status"] == "COMPLETED"
    assert row["exit_reason"] == "EXPIRY_CLOSE"
    assert row["realized_pnl"] == 125.0
    assert row["unrealized_pnl"] == 125.0
    assert row["pnl_pct"] == round(125.0 / 30000.0 * 100.0, 8)
    assert row["closed_at"] is not None
    assert row["last_mark_at"] is not None


def test_detail_rejects_malformed_persisted_ledger_before_json_payload(db_session, monkeypatch):
    _enable(db_session)
    trade = _trade(db_session, event_id="API-MALFORMED")
    trade.legs_json = "{not-json"
    db_session.commit()

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)

    with pytest.raises(HTTPException) as exc:
        routes.detail(trade.id, db_session)

    assert exc.value.status_code == 409
    assert exc.value.detail == "live paper trade ledger row is malformed"


def test_close_rejects_malformed_persisted_ledger_without_mutation(db_session, monkeypatch):
    _enable(db_session)
    trade = _trade(db_session, event_id="API-CLOSE-MALFORMED")
    trade.metadata_json = json.dumps({"exchange": "NOT-A-REAL-EXCHANGE"})
    db_session.commit()

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)

    with pytest.raises(HTTPException) as exc:
        routes.close(trade.id, db_session)

    assert exc.value.status_code == 409
    assert exc.value.detail == "live paper trade ledger row is malformed"

    db_session.refresh(trade)
    assert trade.status == "ONGOING"
    assert trade.closed_at is None
    assert trade.exit_reason is None


def test_manual_close_race_response_refreshes_terminal_row(db_session, monkeypatch):
    _enable(db_session)
    trade = _trade(db_session, event_id="API-CLOSE-RACE")

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)

    # Simulate the competing close after the route has resolved the row but
    # before its final conditional close. Service.close() must refresh the
    # losing route's ORM object and return the already-terminal state.
    service = LivePaperTradeService()
    loaded = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == trade.id,
        LivePaperTrade.status == "ONGOING",
    ).one()
    service.close(db_session, loaded, "EXPIRY_CLOSE")

    response = routes.close(trade.id, db_session)

    assert response["status"] == "success"
    assert response["trade"]["status"] == "COMPLETED"
    assert response["trade"]["exit_reason"] == "EXPIRY_CLOSE"
    assert response["trade"]["realized_pnl"] == response["trade"]["unrealized_pnl"]


def test_payload_preserves_terminal_accounting_and_identity_fields(db_session):
    _enable(db_session)
    trade = _trade(db_session, event_id="API-PAYLOAD")
    service = LivePaperTradeService()
    service.mark(db_session, trade, edge=12.0, pnl_override=75.0)
    service.close(db_session, trade, "MANUAL")

    result = routes.payload(trade)

    expected = {
        "id", "strategy", "symbol", "event_id", "direction",
        "expiry", "earliest_expiry", "lot_size", "lots",
        "entry_edge", "current_edge", "capital_used",
        "unrealized_pnl", "realized_pnl", "pnl_pct",
        "legs", "status", "exit_reason", "opened_at",
        "closed_at", "last_mark_at",
    }
    assert set(result) == expected
    assert result["strategy"] == "cash-future"
    assert result["symbol"] == "API"
    assert result["event_id"] == "API-PAYLOAD"
    assert result["direction"] == "LONG"
    assert result["entry_edge"] == 10.0
    assert result["current_edge"] == 12.0
    assert result["capital_used"] == 30000.0
    assert result["realized_pnl"] == 75.0
    assert result["unrealized_pnl"] == 75.0
    assert result["pnl_pct"] == round(75.0 / 30000.0 * 100.0, 8)
    assert result["status"] == "COMPLETED"
    assert result["exit_reason"] == "MANUAL"
    assert result["closed_at"] == result["last_mark_at"]

"""Regression coverage for live-paper API user isolation and aggregate accounting."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app import live_paper_routes as routes
from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting


def _enable(db, user_id, amount=100000):
    db.add(GlobalPaperSetting(
        user_id=user_id,
        enabled=True,
        paper_amount=amount,
        emergency_stop=False,
    ))
    db.commit()


def _trade(db, *, user_id, event_id, pnl=0.0, expiry="2026-10-30"):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future",
        symbol=f"USER{user_id}",
        event_id=event_id,
        direction="LONG",
        expiry=expiry,
        earliest_expiry=expiry,
        lot_size=10,
        lots=1,
        edge=10,
        capital_used=30000,
        legs=[{"instrument": "CASH", "side": "BUY", "price": 100.0}],
        metadata={"exchange": "NFO", "contract": event_id},
        user_id=user_id,
    )
    assert created is True
    if pnl:
        LivePaperTradeService().mark(db, trade, edge=8.0, pnl_override=pnl)
    return trade


def test_detail_and_manual_close_cannot_cross_user_boundary(db_session, monkeypatch):
    _enable(db_session, 1)
    _enable(db_session, 2)
    user1 = _trade(db_session, user_id=1, event_id="U1-PRIVATE")
    user2 = _trade(db_session, user_id=2, event_id="U2-PRIVATE")

    monkeypatch.setattr(routes, "current_user_id", lambda db: 2)
    monkeypatch.setattr(routes.service, "close_expired", lambda db: [])

    with pytest.raises(HTTPException) as detail_exc:
        routes.detail(user1.id, db_session)
    assert detail_exc.value.status_code == 404

    with pytest.raises(HTTPException) as close_exc:
        routes.close(user1.id, db_session)
    assert close_exc.value.status_code == 404

    db_session.refresh(user1)
    db_session.refresh(user2)
    assert user1.status == "ONGOING"
    assert user2.status == "ONGOING"


def test_status_aggregate_contains_only_current_users_terminal_and_open_pnl(db_session, monkeypatch):
    _enable(db_session, 1)
    _enable(db_session, 2)

    u1_open = _trade(db_session, user_id=1, event_id="U1-OPEN", pnl=-25.0)
    u1_done = _trade(db_session, user_id=1, event_id="U1-DONE", pnl=75.0)
    LivePaperTradeService().close(db_session, u1_done, "MANUAL")

    u2_open = _trade(db_session, user_id=2, event_id="U2-OPEN", pnl=999.0)
    u2_done = _trade(db_session, user_id=2, event_id="U2-DONE", pnl=-999.0)
    LivePaperTradeService().close(db_session, u2_done, "MANUAL")

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    monkeypatch.setattr(routes.service, "close_expired", lambda db: [])
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)

    result = routes.status(db_session)

    assert result["ongoing_count"] == 1
    assert result["completed_count"] == 1
    assert [x["id"] for x in result["ongoing"]] == [u1_open.id]
    assert [x["id"] for x in result["completed"]] == [u1_done.id]
    assert result["ongoing_pnl"] == -25.0
    assert result["completed_pnl"] == 75.0
    assert u2_open.id not in [x["id"] for x in result["ongoing"]]
    assert u2_done.id not in [x["id"] for x in result["completed"]]


def test_refresh_closed_count_is_user_scoped_even_when_expiry_processing_is_global(db_session, monkeypatch):
    _enable(db_session, 1)
    _enable(db_session, 2)
    u1 = _trade(db_session, user_id=1, event_id="U1-EXPIRY", expiry="2026-10-04")
    u2 = _trade(db_session, user_id=2, event_id="U2-EXPIRY", expiry="2026-10-04")

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)
    original = LivePaperTradeService.close_expired
    monkeypatch.setattr(
        routes.service,
        "close_expired",
        lambda db, now=None: original(db, now=datetime(2026, 10, 4, 15, 30)),
    )

    result = routes.refresh(db_session)

    assert result["closed_count"] == 1
    assert [x["id"] for x in result["completed"]] == [u1.id]
    assert result["ongoing"] == []
    db_session.refresh(u1)
    db_session.refresh(u2)
    assert u1.status == "COMPLETED"
    assert u2.status == "COMPLETED"


def test_refresh_and_status_do_not_leak_other_users_pnl_after_terminal_close(db_session, monkeypatch):
    _enable(db_session, 1)
    _enable(db_session, 2)
    u1 = _trade(db_session, user_id=1, event_id="U1-PNL", pnl=-100.0)
    u2 = _trade(db_session, user_id=2, event_id="U2-PNL", pnl=-5000.0)
    LivePaperTradeService().close(db_session, u1, "MANUAL")
    LivePaperTradeService().close(db_session, u2, "MANUAL")

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    monkeypatch.setattr(routes.service, "close_expired", lambda db: [])
    monkeypatch.setattr(routes, "_refresh_marks", lambda db, trades: None)

    status_result = routes.status(db_session)
    refresh_result = routes.refresh(db_session)

    assert status_result["ongoing_count"] == 0
    assert status_result["completed_count"] == 1
    assert status_result["completed_pnl"] == -100.0
    assert refresh_result["closed_count"] == 0
    assert [x["id"] for x in refresh_result["completed"]] == [u1.id]


def test_detail_returns_terminal_row_only_for_its_owner(db_session, monkeypatch):
    _enable(db_session, 1)
    _enable(db_session, 2)
    owner = _trade(db_session, user_id=1, event_id="OWNER-DONE", pnl=40.0)
    LivePaperTradeService().close(db_session, owner, "MANUAL")

    monkeypatch.setattr(routes.service, "close_expired", lambda db: [])

    monkeypatch.setattr(routes, "current_user_id", lambda db: 1)
    owned = routes.detail(owner.id, db_session)
    assert owned["status"] == "COMPLETED"
    assert owned["realized_pnl"] == 40.0

    monkeypatch.setattr(routes, "current_user_id", lambda db: 2)
    with pytest.raises(HTTPException) as exc:
        routes.detail(owner.id, db_session)
    assert exc.value.status_code == 404

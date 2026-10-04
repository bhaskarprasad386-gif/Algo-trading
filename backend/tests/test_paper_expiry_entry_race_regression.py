"""Regression coverage for expiry-release and new-entry serialization."""

from datetime import datetime

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db):
    db.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=30000,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=60000, max_simultaneous_positions=1,
        max_loss=100,
    ))
    db.commit()


def _event(event_id="AFTER-EXPIRY-RACE"):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol=event_id,
        timestamp_ns=1,
        message="expiry-race",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-04",
                "earliest_expiry": "2026-10-04",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 30000,
            },
        },
    )


def _expired_trade(db):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future",
        symbol="EXPIRING",
        event_id="EXPIRING-AT-BOUNDARY",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True
    return trade


def test_expiry_release_is_serialized_before_new_entry_risk_read(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    svc = LivePaperTradeService()

    # This is the exact boundary where an entry and expiry monitor can arrive
    # together. Expiry must release the ongoing reservation before the next
    # risk gate can consume the freed 30k.
    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"
    assert trade.exit_reason == "EXPIRY_CLOSE"

    assert AlertService().dispatch(
        db_session, _event()
    ) == 0

    ongoing = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "ONGOING",
    ).all()
    assert len(ongoing) == 1
    assert ongoing[0].event_id == "AFTER-EXPIRY-RACE"
    assert ongoing[0].capital_used == 30000

    # The first allocation remains part of today's daily usage, so the second
    # entry is only possible because max_daily_capital was deliberately set to
    # 60k. There is exactly one ongoing 30k reservation and no double reserve.
    assert sum(
        float(row.capital_used)
        for row in db_session.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    ) == 30000


def test_expiry_loss_is_realized_before_next_entry_max_loss_gate(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    svc = LivePaperTradeService()
    svc.mark(db_session, trade, edge=0.0, pnl_override=-100.0)

    closed = svc.close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"
    assert trade.realized_pnl == -100.0

    # max_loss=100 is now exactly consumed by the expiry loss. The next entry
    # must be rejected even though the global 30k ongoing reservation was freed.
    assert AlertService().dispatch(
        db_session, _event("AFTER-EXPIRY-LOSS")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "AFTER-EXPIRY-LOSS",
    ).count() == 0


def test_expiry_commit_false_does_not_commit_caller_pending_work(db_session):
    _setup(db_session)
    trade = _expired_trade(db_session)
    setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    setting.paper_amount = 45000

    closed = LivePaperTradeService().close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
        commit=False,
    )
    assert len(closed) == 1
    assert trade.status == "COMPLETED"

    # The expiry mutation and the caller's unrelated setting mutation are still
    # inside the caller-owned transaction. A rollback must undo both together.
    db_session.rollback()

    verify = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == trade.id
    ).one()
    verify_setting = db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one()
    assert verify.status == "ONGOING"
    assert verify_setting.paper_amount == 30000


def test_expiry_commit_true_persists_all_expiry_transitions_in_one_boundary(db_session):
    _setup(db_session)
    db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).update({GlobalPaperSetting.paper_amount: 60000})
    db_session.commit()
    first = _expired_trade(db_session)
    second, created = LivePaperTradeService().enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="EXPIRING-2",
        event_id="EXPIRING-AT-BOUNDARY-2",
        direction="LONG",
        expiry="2026-10-04",
        earliest_expiry="2026-10-04",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=30000,
        user_id=1,
    )
    assert created is True

    closed = LivePaperTradeService().close_expired(
        db_session,
        now=datetime(2026, 10, 4, 15, 30),
        commit=True,
    )
    assert {trade.id for trade in closed} == {first.id, second.id}

    db_session.rollback()
    verify = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.status == "COMPLETED",
    ).all()
    assert {trade.id for trade in verify} == {first.id, second.id}

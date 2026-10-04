from datetime import datetime, timezone

from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def _enable(db, user_id=1, amount=50_000):
    db.add(
        GlobalPaperSetting(
            user_id=user_id,
            enabled=True,
            paper_amount=amount,
            emergency_stop=False,
        )
    )
    db.commit()


def test_paper_ledger_invariants_reconcile_reservation_pnl_and_percentage(db_session):
    _enable(db_session, amount=50_000)
    svc = LivePaperTradeService()

    first, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="AAA",
        event_id="LEDGER-1",
        direction="LONG",
        expiry="2026-10-06",
        lot_size=1,
        lots=2,
        edge=100,
        capital_used=60_000,
        user_id=1,
    )
    assert created is True
    assert first.lots == 1
    assert first.capital_used == 30_000

    reserved = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.capital_used).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    )
    assert reserved == 30_000
    assert float(db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one().paper_amount) - reserved == 20_000

    svc.mark(db_session, first, edge=90)
    assert first.unrealized_pnl == -10.0
    assert first.pnl_pct == round((-10.0 / 30_000) * 100.0, 8)

    second, created = svc.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="BBB",
        event_id="LEDGER-2",
        direction="LONG",
        expiry="2026-10-06",
        lot_size=1,
        lots=1,
        edge=50,
        capital_used=20_000,
        user_id=1,
    )
    assert created is True
    assert second.capital_used == 20_000

    reserved = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.capital_used).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    )
    assert reserved == 50_000

    svc.mark(db_session, second, edge=45)
    assert second.unrealized_pnl == -5.0
    assert second.pnl_pct == round((-5.0 / 20_000) * 100.0, 8)

    first = svc.close(db_session, first, "MANUAL")
    assert first.status == "COMPLETED"
    assert first.realized_pnl == -10.0
    assert first.unrealized_pnl == -10.0
    assert first.pnl_pct == round((-10.0 / 30_000) * 100.0, 8)

    reserved = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.capital_used).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    )
    assert reserved == 20_000
    assert float(db_session.query(GlobalPaperSetting).filter(
        GlobalPaperSetting.user_id == 1
    ).one().paper_amount) - reserved == 30_000

    ongoing_pnl = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.unrealized_pnl).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).all()
    )
    completed_pnl = sum(
        float(row[0] or 0.0)
        for row in db_session.query(LivePaperTrade.realized_pnl).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "COMPLETED",
        ).all()
    )
    assert ongoing_pnl == -5.0
    assert completed_pnl == -10.0
    assert ongoing_pnl + completed_pnl == -15.0

    assert first.opened_at < first.closed_at
    assert first.closed_at == first.last_mark_at
    assert first.closed_at.tzinfo is None
    assert isinstance(first.closed_at, datetime)
    assert first.closed_at.replace(tzinfo=timezone.utc).tzinfo == timezone.utc

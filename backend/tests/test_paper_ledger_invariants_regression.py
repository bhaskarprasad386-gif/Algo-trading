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


def test_malformed_persisted_trade_is_excluded_from_accounting_and_marking(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    malformed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="BAD",
        event_id="BAD-PERSISTED", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        entry_edge=10, current_edge=10, capital_used=float("inf"),
        unrealized_pnl=-100, realized_pnl=0, pnl_pct=float("nan"),
        status="ONGOING",
    )
    db_session.add(malformed)
    db_session.commit()

    svc = LivePaperTradeService()
    assert svc.ongoing(db_session, 1) == []
    assert svc.completed(db_session, 1) == []
    marked = svc.mark(db_session, malformed, edge=20, pnl_override=100)
    assert marked.unrealized_pnl == -100
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == malformed.id
    ).one().unrealized_pnl == -100

    valid, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="GOOD",
        event_id="GOOD-PERSISTED", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        edge=10, capital_used=50_000, user_id=1,
    )
    assert created is False
    assert valid is None

    closed = svc.close(db_session, malformed, "MANUAL")
    assert closed.status == "ONGOING"
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.id == malformed.id
    ).one().status == "ONGOING"

def test_mark_rejects_non_finite_or_negative_updates(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="GOOD",
        event_id="MARK-VALIDATION", direction="LONG", expiry="2026-10-30",
        lot_size=1, lots=1, edge=10, capital_used=20_000, user_id=1,
    )
    assert created is True

    before = (trade.current_edge, trade.unrealized_pnl, trade.capital_used)
    svc.mark(db_session, trade, edge=float("nan"), pnl_override=100)
    svc.mark(db_session, trade, edge=-1, pnl_override=100)
    svc.mark(db_session, trade, edge=20, pnl_override=float("inf"))
    svc.mark(db_session, trade, edge=20, capital_used=float("nan"))
    after = (trade.current_edge, trade.unrealized_pnl, trade.capital_used)
    assert after == before

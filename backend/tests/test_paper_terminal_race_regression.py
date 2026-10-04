"""Regression coverage for stale mark/close ordering and terminal-state integrity."""

from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def _enable(db, user_id=1, amount=100000):
    db.add(GlobalPaperSetting(
        user_id=user_id,
        enabled=True,
        paper_amount=amount,
        emergency_stop=False,
    ))
    db.commit()


def _trade(db, event_id="RACE-TRADE"):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future",
        symbol="RACE",
        event_id=event_id,
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=10.0,
        capital_used=30000,
        legs=[{"side": "BUY", "price": 100.0}],
        metadata={"exchange": "NFO"},
        user_id=1,
    )
    assert created is True
    return trade


def test_stale_mark_after_close_cannot_reopen_or_rewrite_terminal_accounting(db_session):
    _enable(db_session)
    trade = _trade(db_session, "STALE-MARK-AFTER-CLOSE")

    stale = db_session.query(LivePaperTrade).get(trade.id)
    service = LivePaperTradeService()
    service.mark(db_session, trade, edge=5.0, pnl_override=-100.0)
    service.close(db_session, trade, "MANUAL")

    # Deliberately use the stale pre-close object. The conditional UPDATE must
    # observe the terminal DB row and refresh rather than resurrecting it.
    stale.current_edge = 9999.0
    result = service.mark(db_session, stale, edge=999.0, pnl_override=99999.0)

    assert result.status == "COMPLETED"
    assert result.exit_reason == "MANUAL"
    assert result.realized_pnl == -100.0
    assert result.unrealized_pnl == -100.0
    assert result.current_edge == 5.0


def test_stale_close_after_mark_preserves_latest_marked_pnl(db_session):
    _enable(db_session)
    trade = _trade(db_session, "STALE-CLOSE-AFTER-MARK")

    stale = db_session.query(LivePaperTrade).get(trade.id)
    service = LivePaperTradeService()

    service.mark(db_session, trade, edge=14.0, pnl_override=250.0)
    result = service.close(db_session, stale, "MANUAL")

    assert result.status == "COMPLETED"
    assert result.exit_reason == "MANUAL"
    assert result.unrealized_pnl == 250.0
    assert result.realized_pnl == 250.0
    assert result.current_edge == 14.0
    assert result.pnl_pct == round(250.0 / 30000.0 * 100.0, 8)


def test_terminal_event_duplicate_cannot_consume_new_capital_or_create_second_row(db_session):
    _enable(db_session, amount=30000)
    trade = _trade(db_session, "TERMINAL-DUPLICATE")
    service = LivePaperTradeService()
    service.mark(db_session, trade, edge=7.0, pnl_override=-50.0)
    service.close(db_session, trade, "EXPIRY_CLOSE")

    replay, created = service.enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="RACE",
        event_id="TERMINAL-DUPLICATE",
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=30.0,
        capital_used=30000,
        legs=[{"side": "BUY", "price": 999.0}],
        metadata={"exchange": "NFO"},
        user_id=1,
    )

    assert replay is None
    assert created is False
    rows = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "TERMINAL-DUPLICATE",
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"
    assert rows[0].capital_used == 30000.0
    assert rows[0].realized_pnl == -50.0


def test_expiry_close_and_mark_sequentially_leave_single_terminal_accounting_state(db_session):
    _enable(db_session)
    trade = _trade(db_session, "EXPIRY-MARK-ORDER")

    service = LivePaperTradeService()
    service.mark(db_session, trade, edge=16.0, pnl_override=400.0)
    closed = service.close_expired(
        db_session,
        now=__import__("datetime").datetime(2026, 10, 30, 15, 30),
    )

    assert len(closed) == 1
    db_session.refresh(trade)
    assert trade.status == "COMPLETED"
    assert trade.exit_reason == "EXPIRY_CLOSE"
    assert trade.realized_pnl == 400.0
    assert trade.unrealized_pnl == 400.0
    assert trade.pnl_pct == round(400.0 / 30000.0 * 100.0, 8)

    # A later duplicate mark is terminal and must not alter the persisted P&L.
    service.mark(db_session, trade, edge=1.0, pnl_override=-9999.0)
    db_session.refresh(trade)
    assert trade.status == "COMPLETED"
    assert trade.realized_pnl == 400.0
    assert trade.unrealized_pnl == 400.0

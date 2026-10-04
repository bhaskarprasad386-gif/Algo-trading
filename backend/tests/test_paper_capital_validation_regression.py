from app.models import GlobalPaperSetting, LivePaperTrade
from app.auto.live_paper import LivePaperTradeService


def _setup(db):
    db.add(GlobalPaperSetting(
        user_id=1,
        enabled=True,
        paper_amount=100_000,
        emergency_stop=False,
    ))
    db.commit()


def test_new_paper_trade_rejects_zero_capital_and_does_not_reserve(db_session):
    _setup(db_session)
    trade, created = LivePaperTradeService().enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="ZERO",
        event_id="ZERO-CAPITAL",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=0,
        user_id=1,
    )
    assert trade is None
    assert created is False
    assert db_session.query(LivePaperTrade).count() == 0


def test_negative_capital_cannot_be_clamped_into_a_free_paper_trade(db_session):
    _setup(db_session)
    trade, created = LivePaperTradeService().enter_or_mark(
        db_session,
        strategy_id="cash-future",
        symbol="NEGATIVE",
        event_id="NEGATIVE-CAPITAL",
        direction="LONG",
        expiry="2026-10-30",
        lot_size=10,
        lots=1,
        edge=5,
        capital_used=-50_000,
        user_id=1,
    )
    assert trade is None
    assert created is False
    assert db_session.query(LivePaperTrade).count() == 0

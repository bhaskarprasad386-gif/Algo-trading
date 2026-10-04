"""Regression coverage for serialized mark/close/max-loss paper accounting."""

from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
from app.notifications.common import AlertEvent, AlertService


def _setup(db):
    db.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=60000,
        emergency_stop=False,
    ))
    db.add(AlertRule(
        user_id=1, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=90000, max_simultaneous_positions=5,
        max_loss=100,
    ))
    db.commit()


def _trade(db, event_id="MARK-CLOSE-RACE"):
    trade, created = LivePaperTradeService().enter_or_mark(
        db,
        strategy_id="cash-future", symbol="RACE", event_id=event_id,
        direction="LONG", expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, edge=10, capital_used=30000,
        legs=[{"side": "BUY", "price": 100}],
        metadata={"exchange": "NFO"}, user_id=1,
    )
    assert created is True
    return trade


def _event(event_id):
    return AlertEvent(
        strategy_id="cash-future", event_id=event_id, symbol=event_id,
        timestamp_ns=1, message="race",
        metadata={"gross_profit": 1000, "paper_trade": {
            "direction": "LONG", "expiry": "2026-10-30",
            "earliest_expiry": "2026-10-30", "lot_size": 10,
            "lots": 1, "edge": 5, "capital_used": 30000,
        }},
    )


def test_close_serializes_against_latest_mark_and_realizes_latest_pnl(db_session):
    _setup(db_session)
    trade = _trade(db_session)
    mark_session = db_session
    close_session = Session = sessionmaker(bind=db_session.get_bind())()

    try:
        mark_trade = mark_session.query(LivePaperTrade).get(trade.id)
        close_trade = close_session.query(LivePaperTrade).get(trade.id)

        service = LivePaperTradeService()
        service.mark(mark_session, mark_trade, edge=15, pnl_override=250)
        # The close's per-user serialization lock must see the committed mark
        # before taking the terminal snapshot.
        mark_session.commit()

        service.close(close_session, close_trade, "MANUAL")
        close_session.refresh(close_trade)

        assert close_trade.status == "COMPLETED"
        assert close_trade.realized_pnl == 250
        assert close_trade.unrealized_pnl == 250
        assert close_trade.current_edge == 15
    finally:
        close_session.close()


def test_stale_mark_after_terminal_close_cannot_change_realized_loss(db_session):
    _setup(db_session)
    trade = _trade(db_session, "STALE-MARK-TERMINAL")
    service = LivePaperTradeService()

    stale_session = sessionmaker(bind=db_session.get_bind())()
    stale_db = stale_session()
    try:
        stale = stale_db.query(LivePaperTrade).get(trade.id)
        service.close(db_session, trade, "MANUAL")
        db_session.refresh(trade)

        service.mark(stale_db, stale, edge=1, pnl_override=-999)
        stale_db.commit()
        stale_db.refresh(stale)

        verify = db_session.query(LivePaperTrade).get(trade.id)
        assert verify.status == "COMPLETED"
        assert verify.realized_pnl == 0
        assert verify.unrealized_pnl == 0
    finally:
        stale_db.close()


def test_expiry_boundary_loss_is_visible_to_max_loss_before_new_entry(db_session):
    _setup(db_session)
    trade = _trade(db_session, "EXPIRY-LOSS-RISK")
    service = LivePaperTradeService()

    service.mark(db_session, trade, edge=0, pnl_override=-100)
    db_session.commit()

    closed = service.close_expired(
        db_session, now=datetime(2026, 10, 30, 15, 30)
    )
    assert len(closed) == 1

    assert AlertService().dispatch(
        db_session, _event("BLOCK-AFTER-EXPIRY-LOSS")
    ) == 0

    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id == "BLOCK-AFTER-EXPIRY-LOSS",
    ).count() == 0


def test_concurrent_new_entry_never_bypasses_exact_realized_loss_boundary(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mark-close-risk.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base = __import__("app.core.database", fromlist=["Base"]).Base
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    try:
        _setup(setup)
        trade = _trade(setup, "SEEDED-LOSS")
        LivePaperTradeService().mark(setup, trade, edge=0, pnl_override=-100)
        setup.commit()
        LivePaperTradeService().close(setup, trade, "MANUAL")
    finally:
        setup.close()

    results, errors = [], []

    def worker():
        db = Session()
        try:
            results.append(AlertService().dispatch(
                db, _event("CONCURRENT-LOSS-ENTRY")
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    import threading
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    verify = Session()
    try:
        assert errors == []
        assert verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.event_id == "CONCURRENT-LOSS-ENTRY",
        ).count() == 0
        assert verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
            LivePaperTrade.status == "ONGOING",
        ).count() == 0
    finally:
        verify.close()
        engine.dispose()


def test_manual_close_still_works_for_legacy_trade_without_global_setting(db_session):
    # Risk-gated entries require GlobalPaperSetting, but a legacy persisted
    # trade may predate that setting. Terminal/manual close must remain usable.
    trade = LivePaperTrade(
        user_id=1,
        strategy_id="cash-future",
        symbol="LEGACY",
        event_id="LEGACY-CLOSE",
        direction="LONG",
        expiry="2026-10-30",
        earliest_expiry="2026-10-30",
        lot_size=10,
        lots=1,
        entry_edge=10,
        current_edge=10,
        capital_used=30000,
        unrealized_pnl=-25,
        realized_pnl=0,
        pnl_pct=round(-25 / 30000 * 100, 8),
        legs_json='[{"side":"BUY","price":100}]',
        metadata_json='{"exchange":"NFO"}',
        status="ONGOING",
    )
    from app.auto.live_paper import _now
    trade.opened_at = _now()
    trade.last_mark_at = trade.opened_at
    db_session.add(trade)
    db_session.commit()

    result = LivePaperTradeService().close(db_session, trade, "MANUAL")

    assert result.status == "COMPLETED"
    assert result.exit_reason == "MANUAL"
    assert result.realized_pnl == -25
    assert result.unrealized_pnl == -25

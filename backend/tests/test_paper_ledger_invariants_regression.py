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


def test_malformed_persisted_trade_lifecycle_is_excluded(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    malformed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="TIME-BAD",
        event_id="TIME-BAD", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-31", lot_size=1, lots=1,
        entry_edge=10, current_edge=10, capital_used=10_000,
        unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
        status="ONGOING",
        opened_at=datetime(2026, 10, 4, 10, 0),
        last_mark_at=datetime(2026, 10, 4, 9, 59),
        closed_at=None,
    )
    db_session.add(malformed)
    db_session.commit()

    svc = LivePaperTradeService()
    assert svc.ongoing(db_session, 1) == []
    assert svc.close_expired(
        db_session, now=datetime(2026, 10, 30, 16, 0)
    ) == []

    valid, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="GOOD-TIME",
        event_id="GOOD-TIME", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        edge=10, capital_used=10_000, user_id=1,
    )
    assert valid is None
    assert created is False


def test_completed_trade_requires_consistent_terminal_timestamps(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    malformed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="CLOSED-BAD",
        event_id="CLOSED-BAD", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        entry_edge=10, current_edge=10, capital_used=10_000,
        unrealized_pnl=-5, realized_pnl=-5, pnl_pct=-0.05,
        status="COMPLETED",
        opened_at=datetime(2026, 10, 4, 10, 0),
        last_mark_at=datetime(2026, 10, 4, 10, 30),
        closed_at=datetime(2026, 10, 4, 10, 15),
    )
    db_session.add(malformed)
    db_session.commit()

    svc = LivePaperTradeService()
    assert svc.completed(db_session, 1) == []
    closed = svc.close(db_session, malformed, "MANUAL")
    assert closed.status == "COMPLETED"
    assert closed.closed_at == datetime(2026, 10, 4, 10, 15)


def test_malformed_persisted_json_is_excluded_from_accounting_and_expiry(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    malformed_rows = [
        ("JSON-BAD", "{", '{"exchange":"NFO"}'),
        ("JSON-LEGS-SCALAR", '{"side":"BUY","price":10}', '{"exchange":"NFO"}'),
        ("JSON-LEG-BAD-SIDE", '[{"side":"HOLD","price":10}]', '{"exchange":"NFO"}'),
        ("JSON-LEG-NAN", '[{"side":"BUY","price":NaN}]', '{"exchange":"NFO"}'),
        ("JSON-META-LIST", '[]', '[]'),
        ("JSON-META-BAD-EXCHANGE", '[]', '{"exchange":"UNKNOWN"}'),
    ]
    for event_id, legs_json, metadata_json in malformed_rows:
        db_session.add(LivePaperTrade(
            user_id=1, strategy_id="calendar-spread", symbol=event_id,
            event_id=event_id, direction="LONG", expiry="2026-10-12",
            earliest_expiry="2026-10-12", lot_size=1, lots=1,
            entry_edge=10, current_edge=10, capital_used=10_000,
            unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
            legs_json=legs_json, metadata_json=metadata_json,
            status="ONGOING",
            opened_at=datetime(2026, 10, 4, 10, 0),
            last_mark_at=datetime(2026, 10, 4, 10, 0),
        ))
    db_session.commit()

    svc = LivePaperTradeService()
    assert svc.ongoing(db_session, 1) == []
    assert svc.close_expired(
        db_session, now=datetime(2026, 10, 12, 23, 30)
    ) == []
    for row in db_session.query(LivePaperTrade).all():
        assert row.status == "ONGOING"


def test_valid_mcx_metadata_still_uses_mcx_expiry_boundary(db_session):
    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100_000, emergency_stop=False,
    ))
    db_session.commit()
    svc = LivePaperTradeService()
    trade, created = svc.enter_or_mark(
        db_session, strategy_id="calendar-spread", symbol="GOLD",
        event_id="VALID-MCX-META", direction="LONG", expiry="2026-10-12",
        earliest_expiry="2026-10-12", lot_size=1, lots=1, edge=4,
        capital_used=50_000, metadata={"exchange": "MCX"}, user_id=1,
    )
    assert created is True
    assert svc.close_expired(
        db_session, now=datetime(2026, 10, 12, 23, 29, 59)
    ) == []
    assert trade.status == "ONGOING"
    closed = svc.close_expired(
        db_session, now=datetime(2026, 10, 12, 23, 30)
    )
    assert [row.id for row in closed] == [trade.id]


def test_executable_pnl_rejects_malformed_persisted_legs():
    from app.main import _executable_paper_pnl
    from types import SimpleNamespace
    cases = [
        "{",
        '{"side":"BUY","price":10}',
        '[1]',
        '[{"side":"HOLD","price":10}]',
        '[{"side":"BUY","price":NaN}]',
        '[{"side":"BUY","price":Infinity}]',
    ]
    row = {"cash_bid": 103.0, "cash_ask": 104.0}
    for legs_json in cases:
        trade = LivePaperTrade(
            lot_size=10, lots=1, legs_json=legs_json,
        )
        assert _executable_paper_pnl(trade, row) is None


def test_malformed_persisted_identity_is_excluded_and_cannot_consume_risk_state(db_session):
    _enable(db_session, amount=100_000)
    svc = LivePaperTradeService()
    cases = [
        {"strategy_id": "", "symbol": "AAA", "event_id": "ID-1", "direction": "LONG"},
        {"strategy_id": "cash-future", "symbol": "", "event_id": "ID-2", "direction": "LONG"},
        {"strategy_id": "cash-future", "symbol": "AAA", "event_id": "", "direction": "LONG"},
        {"strategy_id": "cash-future", "symbol": "AAA", "event_id": "ID-4", "direction": "HOLD"},
        {"strategy_id": "cash-future", "symbol": "AAA", "event_id": "ID-5", "direction": "LONG", "strategy_over": "x" * 129},
    ]
    for index, values in enumerate(cases):
        strategy_id = values["strategy_id"]
        if values.get("strategy_over"):
            strategy_id = "x" * 129
        db_session.add(LivePaperTrade(
            user_id=1,
            strategy_id=strategy_id,
            symbol=values["symbol"],
            event_id=values["event_id"],
            direction=values["direction"],
            expiry="2026-10-30",
            earliest_expiry="2026-10-30",
            lot_size=1, lots=1, entry_edge=10, current_edge=10,
            capital_used=10_000, unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
            status="ONGOING",
            opened_at=datetime(2026, 10, 4, 10, 0),
            last_mark_at=datetime(2026, 10, 4, 10, 0),
        ))
    db_session.commit()

    assert svc.ongoing(db_session, 1) == []
    valid, created = svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="GOOD",
        event_id="GOOD-ID", direction="LONG", expiry="2026-10-30",
        earliest_expiry="2026-10-30", lot_size=1, lots=1,
        edge=10, capital_used=10_000, user_id=1,
    )
    assert valid is None
    assert created is False


def test_expiry_cannot_precede_opening_trade_date(db_session):
    _enable(db_session, amount=100_000)
    malformed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="EXPIRY-BEFORE-OPEN",
        event_id="EXPIRY-BEFORE-OPEN", direction="LONG",
        expiry="2026-10-03", earliest_expiry="2026-10-03",
        lot_size=1, lots=1, entry_edge=10, current_edge=10,
        capital_used=10_000, unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
        status="ONGOING",
        opened_at=datetime(2026, 10, 4, 10, 0),
        last_mark_at=datetime(2026, 10, 4, 10, 0),
    )
    db_session.add(malformed)
    db_session.commit()
    svc = LivePaperTradeService()
    assert svc.ongoing(db_session, 1) == []
    assert svc.close_expired(
        db_session, now=datetime(2026, 10, 4, 16, 0)
    ) == []
    assert svc.enter_or_mark(
        db_session, strategy_id="cash-future", symbol="GOOD",
        event_id="EXPIRY-GOOD", direction="LONG",
        expiry="2026-10-10", earliest_expiry="2026-10-10",
        lot_size=1, lots=1, edge=10, capital_used=10_000, user_id=1,
    )[0] is None


def test_persisted_ledger_rejects_aware_datetimes(db_session):
    _enable(db_session, amount=100_000)
    malformed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="AWARE-TIME",
        event_id="AWARE-TIME", direction="LONG",
        expiry="2026-10-10", earliest_expiry="2026-10-10",
        lot_size=1, lots=1, entry_edge=10, current_edge=10,
        capital_used=10_000, unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
        status="ONGOING",
        opened_at=datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc),
        last_mark_at=datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(malformed)
    db_session.commit()
    svc = LivePaperTradeService()
    assert svc.ongoing(db_session, 1) == []
    assert svc.close_expired(
        db_session, now=datetime(2026, 10, 10, 15, 30, tzinfo=timezone.utc)
    ) == []

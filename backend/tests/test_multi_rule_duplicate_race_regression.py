"""Regression coverage for same-user multi-rule duplicate/new-event races."""

from datetime import datetime
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.auto.live_paper import LivePaperTradeService
from app.models import AlertRule, LivePaperTrade
from app.models.global_paper_setting import GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService


def _event(event_id, edge=9):
    return AlertEvent(
        strategy_id="cash-future",
        event_id=event_id,
        symbol="AAA" if event_id == "MULTI-RULE-SEED" else "BBB",
        timestamp_ns=1,
        message="paper",
        observed_at=datetime.utcnow(),
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": edge,
                "capital_used": 30000,
            },
        },
    )


def test_same_user_multi_rule_duplicate_mark_and_new_event_are_serialized(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'multi-rule-duplicate-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=60000, emergency_stop=False,
    ))
    setup.add_all([
        AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=30000, max_simultaneous_positions=5, max_loss=0,
            priority=20,
        ),
        AlertRule(
            user_id=1, strategy_id="cash-future", min_gross_profit=0,
            mobile_number="", whatsapp_enabled=False, enabled=True,
            max_daily_capital=60000, max_simultaneous_positions=5, max_loss=0,
            priority=10,
        ),
    ])
    seed = LivePaperTrade(
        user_id=1, strategy_id="cash-future", symbol="AAA",
        event_id="MULTI-RULE-SEED", direction="LONG",
        expiry="2026-10-30", earliest_expiry="2026-10-30",
        lot_size=10, lots=1, entry_edge=5, current_edge=5,
        capital_used=30000, unrealized_pnl=0, realized_pnl=0, pnl_pct=0,
        status="ONGOING", opened_at=datetime.utcnow(),
        last_mark_at=datetime.utcnow(), legs_json="[]", metadata_json="{}",
    )
    setup.add(seed)
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    errors = []
    results = []

    def duplicate_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            results.append(AlertService().dispatch(db, _event("MULTI-RULE-SEED", edge=9)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def new_event_worker():
        db = Session()
        try:
            barrier.wait(timeout=5)
            results.append(AlertService().dispatch(db, _event("MULTI-RULE-NEW", edge=11)))
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=duplicate_worker),
        threading.Thread(target=new_event_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2

    verify = Session()
    try:
        rows = verify.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == 1,
        ).all()
        assert len(rows) == 1
        assert rows[0].event_id == "MULTI-RULE-SEED"
        assert rows[0].status == "ONGOING"
        assert rows[0].current_edge == 9
        assert rows[0].capital_used == 30000
    finally:
        verify.close()
        engine.dispose()

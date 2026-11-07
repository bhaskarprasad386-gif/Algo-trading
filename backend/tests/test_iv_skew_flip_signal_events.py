from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.iv_signal_event import IVSignalEvent
import app.scanner.iv_skew_flip.runtime as runtime_module
from app.scanner.iv_skew_flip.engine import ScannerConfig
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime


def test_early_move_event_history_persists_transition_and_is_idempotent(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[IVSignalEvent.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(runtime_module, "SessionLocal", factory)

    result = {
        "state": "PRE_MOVE",
        "direction": "BULLISH",
        "evidence_at_ns": 1_000_000_000,
        "updated_at_ns": 1_000_000_000,
        "source_timestamp_ns": 999_000_000,
        "spot_timestamp_ns": 998_000_000,
        "volume_ratio": 5.2,
        "premium_change_pct": 0.9,
        "iv_change_points": 0.2,
        "spot_change_pct": 0.01,
        "contracts_confirming": 2,
        "qualifying_tokens": ["ce-1", "ce-2"],
        "reason": "multi-contract pressure before spot move",
    }
    config = ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8)

    IVSkewFlipRuntime._persist_early_move_event(
        "NIFTY_WEEKLY", config, 24000.0, result,
    )
    IVSkewFlipRuntime._persist_early_move_event(
        "NIFTY_WEEKLY", config, 24000.0, result,
    )

    with factory() as session:
        rows = session.query(IVSignalEvent).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.state == "PRE_MOVE"
        assert row.direction == "BULLISH"
        assert row.spot_price == 24000.0
        assert row.source_timestamp_ns == 999_000_000
        assert row.spot_timestamp_ns == 998_000_000
        assert row.qualifying_tokens == '["ce-1", "ce-2"]'
        assert row.contracts_confirming == 2

    engine.dispose()


def test_early_move_event_history_ignores_non_event_states(monkeypatch):
    class ForbiddenSession:
        def __call__(self):
            raise AssertionError("non-event state must not open a DB session")

    monkeypatch.setattr(runtime_module, "SessionLocal", ForbiddenSession())
    IVSkewFlipRuntime._persist_early_move_event(
        "NIFTY_WEEKLY",
        ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        24000.0,
        {"state": "WATCH", "direction": None},
    )

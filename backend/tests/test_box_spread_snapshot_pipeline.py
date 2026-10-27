from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import LiveBoxSpreadSnapshot
from app.scanner.live_box_spread_pipeline import LiveBoxSpreadPipeline


def make_result(timestamp_ns):
    def quote(strike):
        return SimpleNamespace(
            underlying="NIFTY",
            instrument_class="INDEX",
            expiry=20261103,
            strike=strike,
            timestamp_ns=timestamp_ns,
            call_ltp=10.0,
            call_bid=9.5,
            call_ask=10.5,
            put_ltp=12.0,
            put_bid=11.5,
            put_ask=12.5,
            lot_size=65,
        )

    return SimpleNamespace(
        low=quote(22600),
        high=quote(23300),
        direction="LONG",
        executable_edge=5.2,
        edge_per_lot=338.0,
        gross_pnl=338.0,
        strike_distance=700.0,
    )


class RepeatingScanner:
    def __init__(self, result):
        self.result = result

    def observe(self, payload):
        return [self.result]


class NoopAlerts:
    def persist(self, db, results):
        pass

    def notify_users(self, db, results):
        pass


def test_pipeline_deduplicates_same_quote_timestamp_but_keeps_new_observations():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        first_timestamp = 1_793_084_708_000_000_000
        pipeline = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(first_timestamp)),
            session_factory=Session,
            alerts=NoopAlerts(),
        )
        pipeline.observe({})
        pipeline.observe({})

        with Session() as db:
            count = db.query(func.count(LiveBoxSpreadSnapshot.id)).scalar()
            assert count == 1

        pipeline.scanner.result = make_result(first_timestamp + 1_000_000_000)
        pipeline.observe({})

        with Session() as db:
            count = db.query(func.count(LiveBoxSpreadSnapshot.id)).scalar()
            assert count == 2
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_pipeline_retries_snapshot_persistence_after_transient_database_failure():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    calls = {"count": 0}

    def flaky_session_factory():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary database outage")
        return Session()

    try:
        pipeline = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(1_793_084_708_000_000_000)),
            session_factory=flaky_session_factory,
            alerts=NoopAlerts(),
        )
        pipeline.observe({})

        with Session() as db:
            count = db.query(func.count(LiveBoxSpreadSnapshot.id)).scalar()
            assert count == 1
        assert calls["count"] >= 3  # failed snapshot attempt, successful retry, alert session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

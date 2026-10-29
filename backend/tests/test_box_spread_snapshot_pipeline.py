from datetime import datetime, timezone

import pytest
from types import SimpleNamespace

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import LiveBoxSpreadSnapshot
from app.scanner.live_box_spread_pipeline import LiveBoxSpreadPipeline
from app.scanner import live_box_spread_routes as box_spread_routes


def make_result(timestamp_ns, low_strike=22600, high_strike=23300, direction="LONG"):
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
        low=quote(low_strike),
        high=quote(high_strike),
        direction=direction,
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



class MultipleResultsScanner:
    def __init__(self, results):
        self.results = results

    def observe(self, payload):
        return self.results


def test_pipeline_persists_every_opportunity_returned_by_one_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    base_timestamp = 1_793_084_708_000_000_000
    opportunities = [
        make_result(base_timestamp, 22600, 23300, "LONG"),
        make_result(base_timestamp + 1_000_000, 22700, 23200, "SHORT"),
        make_result(base_timestamp + 2_000_000, 22800, 23100, "LONG"),
    ]

    try:
        pipeline = LiveBoxSpreadPipeline(
            scanner=MultipleResultsScanner(opportunities),
            session_factory=Session,
            alerts=NoopAlerts(),
        )
        returned = pipeline.observe({})

        with Session() as db:
            rows = db.query(LiveBoxSpreadSnapshot).order_by(LiveBoxSpreadSnapshot.low_strike).all()
            assert len(returned) == len(opportunities)
            assert len(rows) == len(opportunities)
            assert {(row.low_strike, row.high_strike, row.direction) for row in rows} == {
                (22600, 23300, "LONG"),
                (22700, 23200, "SHORT"),
                (22800, 23100, "LONG"),
            }
        assert (tmp_path / "pending.jsonl").read_text(encoding="utf-8") == ""
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_pipeline_persists_weekly_expiry_classification(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    box_spread_routes.set_expiry_types({("NIFTY", "03NOV2026"): "WEEKLY"})
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        pipeline = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(1_793_084_708_000_000_000)),
            session_factory=Session,
            alerts=NoopAlerts(),
        )
        pipeline.observe({})

        with Session() as db:
            row = db.query(LiveBoxSpreadSnapshot).one()
            payload = __import__("json").loads(row.payload_json)
            assert payload["expiry_type"] == "WEEKLY"
    finally:
        box_spread_routes.set_expiry_types({})
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_pipeline_surfaces_durable_spool_enqueue_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    from app.scanner import live_box_spread_pipeline as pipeline_module

    def fail_enqueue(rows):
        raise OSError("disk is unavailable")

    monkeypatch.setattr(pipeline_module, "_append_spool", fail_enqueue)
    pipeline = LiveBoxSpreadPipeline(
        scanner=RepeatingScanner(make_result(1_793_084_708_000_000_000)),
        session_factory=None,
        alerts=NoopAlerts(),
    )

    with pytest.raises(RuntimeError, match="could not be durably queued"):
        pipeline.observe({})

def test_pipeline_deduplicates_same_quote_timestamp_but_keeps_new_observations(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
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


def test_pipeline_retries_snapshot_persistence_after_transient_database_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
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


def test_disk_spool_survives_database_outage_and_replays_when_database_returns(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def unavailable_database():
        raise RuntimeError("database unavailable")

    try:
        first = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(1_793_084_708_000_000_000)),
            session_factory=unavailable_database,
            alerts=NoopAlerts(),
        )
        first.observe({})
        spool = tmp_path / "pending.jsonl"
        assert spool.exists()
        assert spool.read_text(encoding="utf-8").count("\n") == 1

        recovered = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(1_793_084_709_000_000_000)),
            session_factory=Session,
            alerts=NoopAlerts(),
        )
        recovered.observe({})

        with Session() as db:
            assert db.query(func.count(LiveBoxSpreadSnapshot.id)).scalar() == 2
        assert spool.read_text(encoding="utf-8") == ""
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_pipeline_retains_snapshots_older_than_deduplication_window(tmp_path, monkeypatch):
    """Old saved opportunities must remain available to complete-history analysis."""
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    old_observed_at = datetime(2020, 1, 2, 4, 0, 0, tzinfo=timezone.utc)

    try:
        with Session() as db:
            db.add(
                LiveBoxSpreadSnapshot(
                    observed_at=old_observed_at,
                    timestamp_ns=123,
                    symbol="NIFTY",
                    expiry=20261103,
                    low_strike=22000,
                    high_strike=23000,
                    direction="LONG",
                    payload_json='{"executable_edge": 12.5}',
                )
            )
            db.commit()

        pipeline = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(make_result(1_793_084_708_000_000_000)),
            session_factory=Session,
            alerts=NoopAlerts(),
        )
        pipeline.observe({})

        with Session() as db:
            rows = db.query(LiveBoxSpreadSnapshot).order_by(LiveBoxSpreadSnapshot.observed_at).all()
            assert len(rows) == 2
            assert rows[0].observed_at.year == 2020
            assert rows[0].payload_json == '{"executable_edge": 12.5}'
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

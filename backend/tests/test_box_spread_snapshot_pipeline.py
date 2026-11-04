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
            call_bid_qty=11,
            call_ask_qty=12,
            put_ltp=12.0,
            put_bid=11.5,
            put_ask=12.5,
            put_bid_qty=13,
            put_ask_qty=14,
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
        max_executable_lots=2,
        tradability_status="TRADABLE",
        depth_edge_per_unit=5.2,
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
            assert row.expiry_type == "WEEKLY"
            assert payload["low_call_bid_qty"] == 11
            assert payload["low_call_ask_qty"] == 12
            assert payload["low_put_bid_qty"] == 13
            assert payload["low_put_ask_qty"] == 14
            assert payload["high_call_bid_qty"] == 11
            assert payload["high_put_ask_qty"] == 14
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


def test_snapshot_payload_persists_all_four_leg_timestamps_and_quote_skew(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    base = 1_793_084_708_000_000_000
    result = make_result(base)
    result.low.call_timestamp_ns = base
    result.low.put_timestamp_ns = base + 100_000_000
    result.high.call_timestamp_ns = base + 200_000_000
    result.high.put_timestamp_ns = base + 300_000_000
    result.depth_legs = (
        {
            "role": "LOW_CE", "side": "ASK", "timestamp_ns": base,
            "depth_status": "DEPTH_PARTIAL", "vwap": 10.5,
            "available_quantity": 130, "selected_quantity": 130,
            "bid_depth_levels": [{"price": 10.0, "quantity": 130}],
            "ask_depth_levels": [{"price": 10.5, "quantity": 130}],
            "depth_levels": [{"price": 10.5, "quantity": 130}],
        },
    )
    pipeline = LiveBoxSpreadPipeline(
        scanner=RepeatingScanner(result), session_factory=None, alerts=NoopAlerts(),
    )

    pipeline.observe({})

    saved = json.loads((tmp_path / "pending.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert saved["low_call_timestamp_ns"] == base
    assert saved["low_put_timestamp_ns"] == base + 100_000_000
    assert saved["high_call_timestamp_ns"] == base + 200_000_000
    assert saved["high_put_timestamp_ns"] == base + 300_000_000
    assert saved["last_update_timestamp_ns"] == base + 300_000_000
    assert saved["timestamp_skew_ms"] == 300.0
    assert saved["depth_legs"][0]["timestamp_ns"] == base
    assert saved["depth_legs"][0]["bid_depth_levels"] == [{"price": 10.0, "quantity": 130}]
    assert saved["depth_legs"][0]["ask_depth_levels"] == [{"price": 10.5, "quantity": 130}]



def test_snapshot_payload_rejects_explicit_zero_leg_timestamp(tmp_path, monkeypatch):
    """An explicit malformed leg timestamp must not silently fall back to strike time."""
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    result = make_result(1_793_084_708_000_000_000)
    result.low.call_timestamp_ns = 0
    pipeline = LiveBoxSpreadPipeline(
        scanner=RepeatingScanner(result), session_factory=None, alerts=NoopAlerts(),
    )

    with pytest.raises(RuntimeError, match="could not be durably queued"):
        pipeline.observe({})

    assert not (tmp_path / "pending.jsonl").exists()



def test_corrupt_spool_records_are_quarantined_without_blocking_valid_rows(tmp_path, monkeypatch):
    import json
    from app.scanner import live_box_spread_pipeline as pipeline_module

    spool = tmp_path / "pending.jsonl"
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(spool))

    def valid_row(symbol, expiry, timestamp):
        return {
            "symbol": symbol, "expiry": expiry, "low_strike": 22000,
            "high_strike": 23000, "direction": "LONG",
            "observed_at": "2026-11-01T09:15:00Z",
            "last_update_timestamp_ns": timestamp,
        }

    valid_one = valid_row("NIFTY", 20261103, 100)
    valid_two = valid_row("SENSEX", 20261105, 200)
    corrupt_json = '{"symbol": "BROKEN",\n'
    non_object = '[]\n'
    incomplete = json.dumps({"symbol": "PARTIAL"}) + "\n"
    invalid_snapshot = json.dumps({
        "symbol": "NIFTY", "expiry": "not-an-expiry",
        "low_strike": 22000, "high_strike": 23000, "direction": "LONG",
        "observed_at": "2026-11-01T09:15:00Z",
        "last_update_timestamp_ns": 1,
    }) + "\n"
    spool.write_text(
        json.dumps(valid_one) + "\n" + corrupt_json + non_object
        + incomplete + invalid_snapshot + json.dumps(valid_two) + "\n",
        encoding="utf-8",
    )

    rows = pipeline_module._read_spool()

    assert rows == [valid_one, valid_two]
    assert spool.read_text(encoding="utf-8").splitlines() == [
        json.dumps(valid_one), json.dumps(valid_two),
    ]
    quarantine = tmp_path / "pending.jsonl.corrupt"
    assert quarantine.read_text(encoding="utf-8").splitlines() == [
        corrupt_json.rstrip("\n"), non_object.rstrip("\n"),
        incomplete.rstrip("\n"), invalid_snapshot.rstrip("\n"),
    ]



def test_unqualified_depth_result_never_reaches_live_snapshots_or_alerts(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_BOX_SPREAD_SPOOL_PATH", str(tmp_path / "pending.jsonl"))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    rejected = make_result(1_793_084_708_000_000_000)
    rejected.tradability_status = "NOT_TRADABLE"
    rejected.max_executable_lots = 0
    rejected.depth_edge_per_unit = 0
    published = []
    alert_calls = []

    class RecordingAlerts(NoopAlerts):
        def persist(self, db, results):
            alert_calls.extend(results)

        def notify_users(self, db, results):
            alert_calls.extend(results)

    try:
        pipeline = LiveBoxSpreadPipeline(
            scanner=RepeatingScanner(rejected),
            session_factory=Session,
            alerts=RecordingAlerts(),
            on_results=published.extend,
        )
        assert pipeline.observe({}) == ()
        assert published == []
        assert alert_calls == []
        with Session() as db:
            assert db.query(func.count(LiveBoxSpreadSnapshot.id)).scalar() == 0
        spool = tmp_path / "pending.jsonl"
        assert not spool.exists() or spool.read_text(encoding="utf-8") == ""
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

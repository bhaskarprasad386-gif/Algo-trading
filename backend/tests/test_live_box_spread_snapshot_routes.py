from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import LiveBoxSpreadSnapshot
from app.scanner import live_box_spread_routes as routes


def test_snapshot_endpoint_reports_total_and_supports_stable_pagination(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.utcnow()

    try:
        monkeypatch.setattr(routes, "_expiry_types", {})
        with Session() as db:
            for index in range(3):
                payload = {"sequence": index}
                if index == 0:
                    payload["expiry_type"] = "WEEKLY"
                db.add(LiveBoxSpreadSnapshot(
                    observed_at=now - timedelta(seconds=index),
                    timestamp_ns=1_790_000_000_000_000_000 + index,
                    symbol="NIFTY",
                    expiry=20261103,
                    low_strike=22000 + index * 100,
                    high_strike=23000 + index * 100,
                    direction="LONG",
                    payload_json=json.dumps(payload),
                ))
            db.commit()

        monkeypatch.setattr(routes, "SessionLocal", Session)
        first = routes.snapshots(days=1, limit=2, offset=0, expiry_type=None)
        second = routes.snapshots(days=1, limit=2, offset=2, expiry_type=None)

        assert first["total_count"] == 3
        assert first["count"] == 2
        assert first["offset"] == 0
        assert first["has_more"] is True
        assert second["total_count"] == 3
        assert second["count"] == 1
        assert second["offset"] == 2
        assert second["has_more"] is False
        assert [item["sequence"] for item in first["data"] + second["data"]] == [0, 1, 2]
        assert first["data"][0]["expiry_type"] == "WEEKLY"
        assert first["expiry_counts"] == {"WEEKLY": 1, "MONTHLY": 0, "UNKNOWN": 2}

        weekly = routes.snapshots(days=1, limit=2, offset=0, expiry_type="WEEKLY")
        unknown = routes.snapshots(days=1, limit=2, offset=0, expiry_type="UNKNOWN")
        assert weekly["total_count"] == 1
        assert [item["sequence"] for item in weekly["data"]] == [0]
        assert unknown["total_count"] == 2
        assert [item["sequence"] for item in unknown["data"]] == [1, 2]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_snapshot_endpoint_applies_symbol_filter_before_pagination(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.utcnow()

    try:
        monkeypatch.setattr(routes, "_expiry_types", {})
        with Session() as db:
            for index, symbol in enumerate(("SENSEX", "NIFTY", "SENSEX", "NIFTY")):
                db.add(LiveBoxSpreadSnapshot(
                    observed_at=now - timedelta(seconds=index),
                    timestamp_ns=1_790_000_000_000_000_000 + index,
                    symbol=symbol,
                    expiry=20261103,
                    low_strike=22000 + index * 100,
                    high_strike=23000 + index * 100,
                    direction="LONG",
                    payload_json=json.dumps({"sequence": index, "symbol": symbol}),
                ))
            db.commit()

        monkeypatch.setattr(routes, "SessionLocal", Session)
        nifty = routes.snapshots(days=1, limit=1, offset=0, expiry_type=None, symbol="NIFTY")
        nifty_next = routes.snapshots(days=1, limit=1, offset=1, expiry_type=None, symbol="NIFTY")
        sensex = routes.snapshots(days=1, limit=5, offset=0, expiry_type=None, symbol="SENSEX")

        assert nifty["symbol"] == "NIFTY"
        assert nifty["total_count"] == 2
        assert nifty["count"] == 1
        assert nifty["data"][0]["symbol"] == "NIFTY"
        assert nifty["data"][0]["sequence"] == 1
        assert nifty_next["total_count"] == 2
        assert nifty_next["data"][0]["sequence"] == 3
        assert sensex["total_count"] == 2
        assert [row["symbol"] for row in sensex["data"]] == ["SENSEX", "SENSEX"]
        assert sensex["expiry_counts"] == {"WEEKLY": 0, "MONTHLY": 0, "UNKNOWN": 2}
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_snapshot_endpoint_uses_indexed_expiry_and_keeps_legacy_null_rows(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.utcnow()

    try:
        monkeypatch.setattr(routes, "_expiry_types", {})
        with Session() as db:
            db.add(LiveBoxSpreadSnapshot(
                observed_at=now,
                timestamp_ns=1_790_000_000_000_000_001,
                symbol="NIFTY",
                expiry=20261103,
                low_strike=22000,
                high_strike=23000,
                direction="LONG",
                expiry_type="WEEKLY",
                payload_json='{"sequence": "indexed"}',
            ))
            db.add(LiveBoxSpreadSnapshot(
                observed_at=now - timedelta(seconds=1),
                timestamp_ns=1_790_000_000_000_000_002,
                symbol="NIFTY",
                expiry=20261103,
                low_strike=22100,
                high_strike=23100,
                direction="LONG",
                expiry_type=None,
                payload_json='{"sequence": "legacy", "expiry_type": "WEEKLY"}',
            ))
            db.commit()

        monkeypatch.setattr(routes, "SessionLocal", Session)
        weekly = routes.snapshots(days=1, limit=10, offset=0, expiry_type="WEEKLY")

        assert weekly["total_count"] == 2
        assert weekly["expiry_counts"] == {"WEEKLY": 2, "MONTHLY": 0, "UNKNOWN": 0}
        assert [row["sequence"] for row in weekly["data"]] == ["indexed", "legacy"]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

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
        first = routes.snapshots(days=1, limit=2, offset=0)
        second = routes.snapshots(days=1, limit=2, offset=2)

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

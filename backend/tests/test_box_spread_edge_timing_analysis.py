import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import LiveBoxSpreadSnapshot
from app.scanner import live_box_spread_routes as routes


def test_edge_timing_analysis_uses_complete_saved_history_and_ist(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(routes, "SessionLocal", Session)

    with Session() as db:
        # 04:00 UTC = 09:30 IST on Monday. Five different Mondays qualify for average analysis.
        for index, edge in enumerate((2.0, 3.0, 4.0, 5.0, 20.0)):
            db.add(
                LiveBoxSpreadSnapshot(
                    observed_at=datetime(2026, 10, 26, 4, index, tzinfo=timezone.utc) - timedelta(days=7 * index),
                    timestamp_ns=index + 1,
                    symbol="NIFTY",
                    expiry=20261103,
                    low_strike=22300,
                    high_strike=23500,
                    direction="LONG",
                    payload_json=json.dumps(
                        {"symbol": "NIFTY", "executable_edge": edge, "edge_per_lot": edge * 65}
                    ),
                )
            )
        # Seven observations in one Tuesday session must not count as seven independent
        # trading dates for a reliable average window.
        for index, edge in enumerate((6.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0)):
            db.add(
                LiveBoxSpreadSnapshot(
                    observed_at=datetime(2026, 10, 27, 4, 30 + index, tzinfo=timezone.utc),
                    timestamp_ns=100 + index,
                    symbol="BANKNIFTY",
                    expiry=20261027,
                    low_strike=50000,
                    high_strike=51000,
                    direction="SHORT",
                    payload_json=json.dumps(
                        {"symbol": "BANKNIFTY", "executable_edge": edge, "edge_per_lot": edge * 30}
                    ),
                )
            )
        # Invalid/non-positive observations remain in total saved count, but are
        # excluded from positive-edge timing statistics.
        db.add(
            LiveBoxSpreadSnapshot(
                observed_at=datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc),
                timestamp_ns=999,
                symbol="NIFTY",
                expiry=20261103,
                low_strike=22300,
                high_strike=23500,
                direction="LONG",
                payload_json=json.dumps({"executable_edge": 0}),
            )
        )
        db.commit()

    result = routes.edge_timing_analysis()

    assert result["source"] == "all_saved_snapshots"
    assert result["timezone"] == "Asia/Kolkata"
    assert result["total_saved_snapshots"] == 13
    assert result["positive_edge_observations"] == 12
    assert result["best_max_edge_time"]["key"] == "09:30"
    assert result["best_max_edge_time"]["max_edge"] == 20.0
    assert result["best_average_edge_time"]["key"] == "09:30"
    assert result["best_average_edge_time"]["count"] == 5
    assert result["best_average_edge_time"]["distinct_dates"] == 5
    assert result["best_average_edge_day"]["key"] == "Monday"
    assert result["best_average_edge_day"]["count"] == 5
    assert result["best_average_edge_day"]["distinct_dates"] == 5
    tuesday_window = next(row for row in result["by_time"] if row["key"] == "10:00")
    assert tuesday_window["count"] == 7
    assert tuesday_window["distinct_dates"] == 1
    assert result["best_average_edge_time"]["key"] != "10:00"
    assert "historical observations" in result["conclusion"]

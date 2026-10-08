from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.live_cash_future_scanner_result import LiveCashFutureScannerResult
from app.models import LiveCashFutureAlertHistory
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner


def _signal(scanner: LiveCashFutureScanner, timestamp_ns: int):
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "ask": 100.0, "source_timestamp_ns": timestamp_ns,
    })
    return scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0,
        "source_timestamp_ns": timestamp_ns,
    })


def test_live_scanner_result_persists_and_cleans_up_after_30_days(monkeypatch, tmp_path):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_RESULT_RETENTION_DAYS", 30)
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-results.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_factory():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    scanner = LiveCashFutureScanner()
    first = _signal(scanner, 1_000_000_000)
    assert first is not None
    scanner._persist_result(session_factory, first)

    with Session() as db:
        row = db.query(LiveCashFutureScannerResult).one()
        assert row.observed_at.isoformat().startswith("1970-01-01T05:30:01")
        row.observed_at = row.observed_at - timedelta(days=91)
        db.commit()

    second = _signal(scanner, 2_000_000_000)
    assert second is not None
    scanner._last_result_cleanup = 0.0
    scanner._persist_result(session_factory, second)

    with Session() as db:
        rows = db.query(LiveCashFutureScannerResult).all()
        assert len(rows) == 1
        assert rows[0].timestamp_ns == second.timestamp_ns
        assert rows[0].gap_pct == second.gap_pct


def test_live_scanner_alert_history_is_retained_for_90_days(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from datetime import datetime, timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.models import LiveCashFutureAlertHistory

    engine = create_engine(f"sqlite:///{tmp_path / 'alert-history.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_factory():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    scanner = LiveCashFutureScanner()
    signal = _signal(scanner, 3_000_000_000)
    assert signal is not None
    signal = replace(signal, alert_event="NEW", lot_size=100, alert_lots=1, gross_profit=80.0, net_profit=80.0)
    scanner._persist_alert(session_factory, signal)

    with Session() as db:
        row = db.query(LiveCashFutureAlertHistory).one()
        assert row.timestamp_ns == signal.timestamp_ns
        assert row.gap == signal.gap
        assert row.observed_at.isoformat().startswith("1970-01-01T05:30:03")
        row.observed_at = row.observed_at - timedelta(days=91)
        db.commit()

    recovered = replace(signal, timestamp_ns=4_000_000_000, alert_event="RECOVERY")
    scanner._persist_alert(session_factory, recovered)

    with Session() as db:
        rows = db.query(LiveCashFutureAlertHistory).order_by(LiveCashFutureAlertHistory.timestamp_ns).all()
        assert len(rows) == 1
        assert rows[0].timestamp_ns == recovered.timestamp_ns
        assert rows[0].event == "RECOVERY"


def test_live_pair_diagnostic_persistence_uses_source_observation_time(tmp_path):
    from contextlib import contextmanager
    from datetime import datetime

    engine = create_engine("sqlite:///" + str(tmp_path / "pair-diagnostic.sqlite3"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_factory():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    scanner = LiveCashFutureScanner()
    source_ns = 4_000_000_000
    event = {
        "symbol": "ABC", "contract_month": "CURRENT", "timestamp_ns": source_ns,
        "status": "NO_SIGNAL", "reason_codes": ["PAIR_CREATED"],
        "observation_ref": "ABC:CURRENT:4000000000",
        "gap": 0.5, "gap_pct": 0.5, "liquidity_qty": None,
    }
    cash = {"ltp": 100.0, "bid": 99.9, "ask": 100.0}
    future = {"ltp": 100.5, "bid": 100.5, "ask": 100.6}
    scanner._persist_pair_event(session_factory, event, cash, future, 100)

    with Session() as db:
        row = db.query(LiveCashFutureScannerResult).one()
        from zoneinfo import ZoneInfo
        assert row.observed_at == datetime.fromtimestamp(source_ns / 1_000_000_000, ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        assert row.symbol == "ABC"
        assert row.lifecycle == "NO_SIGNAL"

def test_live_alert_history_route_returns_persisted_alerts(tmp_path):
    from datetime import datetime
    from app.scanner.auto_routes import cash_future_live_alert_history

    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-routes.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(LiveCashFutureAlertHistory(
            observed_at=datetime.now(), timestamp_ns=1_000_000_000,
            symbol="ABC", contract_month="CURRENT", event="NEW",
            cash_ask=100.0, future_bid=101.0, gap=1.0, gap_pct=1.0,
            lot_size=100, alert_lots=2, gross_profit=200.0,
            estimated_cost=0.0, net_profit=200.0, net_gap_pct=1.0,
            annualized_gap_pct=10.0, liquidity_qty=500.0, stable_observations=3,
        ))
        db.commit()
        response = cash_future_live_alert_history(days=30, limit=10, db=db)

    assert response["mode"] == "live-alert-history"
    assert response["count"] == 1
    assert response["data"][0]["event"] == "NEW"
    assert response["data"][0]["gross_profit"] == 200.0


def test_live_fast_scanner_route_uses_process_local_snapshot(monkeypatch):
    from app.scanner.auto_routes import cash_future_live_fast_scanner

    class StubScanner:
        def health(self):
            return {"last_observation_at": "2026-10-09T09:15:01+05:30"}

        def snapshot(self, *, max_age_seconds, limit):
            assert max_age_seconds == 5.0
            assert limit == 10
            return [{"symbol": "ABC", "contract_month": "CURRENT", "gap_pct": 1.2}]

    monkeypatch.setattr("app.main.live_cash_future_scanner", StubScanner())
    response = cash_future_live_fast_scanner(max_age_seconds=5.0, limit=10)
    assert response["mode"] == "live-fast"
    assert response["data"][0]["symbol"] == "ABC"


def test_live_scanner_history_route_excludes_expired_rows(tmp_path):
    from datetime import datetime

    from app.scanner.auto_routes import cash_future_live_scanner_history

    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-history-route.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(LiveCashFutureScannerResult(
            symbol="OLD", contract_month="CURRENT", timestamp_ns=1,
            observed_at=datetime.now() - timedelta(days=31),
            cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
            cash_day_high=100, cash_day_low=100,
            future_day_high=101, future_day_low=101,
            estimated_cost=0, net_gap=1, net_gap_pct=1,
            stable_observations=1, lifecycle="NEW",
            reason_codes="STABLE", observation_ref="OLD:CURRENT:1",
        ))
        db.add(LiveCashFutureScannerResult(
            symbol="NEW", contract_month="CURRENT", timestamp_ns=2,
            observed_at=datetime.now(),
            cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
            cash_day_high=100, cash_day_low=100,
            future_day_high=101, future_day_low=101,
            estimated_cost=0, net_gap=1, net_gap_pct=1,
            stable_observations=1, lifecycle="NEW",
            reason_codes="STABLE", observation_ref="NEW:CURRENT:2",
        ))
        db.commit()
        response = cash_future_live_scanner_history(days=1, limit=10, db=db)

    assert response["count"] == 1
    assert response["data"][0]["symbol"] == "NEW"


def test_live_scanner_history_returns_latest_100_unique_pairs_for_today(tmp_path):
    from datetime import datetime

    from app.scanner.auto_routes import cash_future_live_scanner_history

    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-history-limit.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now()
    with Session() as db:
        for index in range(105):
            db.add(LiveCashFutureScannerResult(
                symbol=f"SYM{index:03d}", contract_month="CURRENT", timestamp_ns=index + 1,
                observed_at=now, cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
                cash_day_high=100, cash_day_low=100, future_day_high=101, future_day_low=101,
                estimated_cost=0, net_gap=1, net_gap_pct=1, stable_observations=1,
                lifecycle="NO_SIGNAL", reason_codes="PAIR_CREATED", observation_ref=f"SYM{index:03d}:CURRENT:{index + 1}",
            ))
        db.commit()
        response = cash_future_live_scanner_history(days=1, limit=100, db=db)

    assert response["count"] == 100
    assert len(response["data"]) == 100


def test_live_scanner_history_resets_at_next_calendar_day(monkeypatch, tmp_path):
    from datetime import datetime

    from app.scanner.auto_routes import cash_future_live_scanner_history

    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-history-day.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(LiveCashFutureScannerResult(
            symbol="YESTERDAY", contract_month="CURRENT", timestamp_ns=1,
            observed_at=datetime.now() - timedelta(days=1),
            cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
            cash_day_high=100, cash_day_low=100, future_day_high=101, future_day_low=101,
            estimated_cost=0, net_gap=1, net_gap_pct=1, stable_observations=1,
            lifecycle="NO_SIGNAL", reason_codes="PAIR_CREATED", observation_ref="YESTERDAY:CURRENT:1",
        ))
        db.commit()
        response = cash_future_live_scanner_history(days=1, limit=100, db=db)

    assert response["count"] == 0
    assert response["data"] == []


def test_live_scanner_history_and_alert_routes_are_registered():
    from app.main import app

    paths = app.openapi().get("paths", {})
    assert "/api/v1/scanner/cash-future/live/history" in paths
    assert "/api/v1/scanner/cash-future/live/alerts" in paths


def test_live_scanner_pair_diagnostic_exposes_invalid_future_book():
    import time

    scanner = LiveCashFutureScanner()
    timestamp_ns = time.time_ns()
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "ask": 100.0, "source_timestamp_ns": timestamp_ns,
    })
    result = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 102.0, "ask": 101.0,
        "source_timestamp_ns": timestamp_ns,
    })

    assert result is None
    diagnostics = scanner.pair_snapshot(max_age_seconds=120, limit=10)
    assert diagnostics[0]["status"] == "INVALID_FUTURE_BOOK"
    assert "INVALID_FUTURE_BOOK" in diagnostics[0]["reason_codes"]


def test_live_scanner_history_keeps_100_unique_pairs_when_one_pair_is_very_active(tmp_path):
    from datetime import datetime

    from app.scanner.auto_routes import cash_future_live_scanner_history

    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-history-adversarial.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now()
    with Session() as db:
        for index in range(1000):
            db.add(LiveCashFutureScannerResult(
                symbol="HOT", contract_month="CURRENT", timestamp_ns=index + 1,
                observed_at=now, cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
                cash_day_high=100, cash_day_low=100, future_day_high=101, future_day_low=101,
                estimated_cost=0, net_gap=1, net_gap_pct=1, stable_observations=1,
                lifecycle="NO_SIGNAL", reason_codes="PAIR_CREATED", observation_ref=f"HOT:CURRENT:{index + 1}",
            ))
        for index in range(100):
            db.add(LiveCashFutureScannerResult(
                symbol=f"OTHER{index:03d}", contract_month="CURRENT", timestamp_ns=2000 + index,
                observed_at=now, cash_ltp=100, future_ltp=101, gap=1, gap_pct=1,
                cash_day_high=100, cash_day_low=100, future_day_high=101, future_day_low=101,
                estimated_cost=0, net_gap=1, net_gap_pct=1, stable_observations=1,
                lifecycle="NO_SIGNAL", reason_codes="PAIR_CREATED", observation_ref=f"OTHER{index:03d}:CURRENT:{index}",
            ))
        db.commit()
        response = cash_future_live_scanner_history(days=1, limit=100, db=db)

    assert response["count"] == 100
    assert len({row["symbol"] for row in response["data"]}) == 100
    assert "HOT" in {row["symbol"] for row in response["data"]}

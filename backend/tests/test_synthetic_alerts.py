from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import LiveSyntheticAlertHistory
from app.notifications.synthetic_alerts import SyntheticAlertService


def _result(timestamp_ns=2_000_000_000):
    option = SimpleNamespace(
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry=20260930,
        strike=100.0,
        timestamp_ns=timestamp_ns,
        call_bid=4.0,
        call_ask=5.0,
        put_bid=4.0,
        put_ask=5.0,
    )
    future = SimpleNamespace(bid=115.0, ask=116.0, lot_size=50)
    return SimpleNamespace(
        option=option,
        future=future,
        direction="LONG",
        executable_edge=1.0,
        edge_per_lot=50.0,
        gross_pnl=50.0,
    )


def test_synthetic_alert_persists_and_deduplicates(db_session):
    service = SyntheticAlertService()
    result = _result()

    assert service.persist(db_session, (result,)) == 1
    assert service.persist(db_session, (result,)) == 0
    assert db_session.query(LiveSyntheticAlertHistory).count() == 1


def test_synthetic_alert_retention_cleanup_runs_without_new_results(db_session, monkeypatch):
    service = SyntheticAlertService()
    old = LiveSyntheticAlertHistory(
        observed_at=datetime.utcnow() - timedelta(days=31),
        timestamp_ns=1,
        symbol="NIFTY",
        instrument_class="INDEX",
        expiry=20260930,
        strike=100.0,
        direction="LONG",
        executable_edge=1.0,
        edge_per_lot=50.0,
        gross_pnl=50.0,
        lot_size=50,
    )
    db_session.add(old)
    db_session.commit()

    monkeypatch.setattr("app.notifications.synthetic_alerts.settings.LIVE_CASH_FUTURE_RESULT_RETENTION_DAYS", 30)
    assert service.persist(db_session, ()) == 0
    assert db_session.query(LiveSyntheticAlertHistory).count() == 0

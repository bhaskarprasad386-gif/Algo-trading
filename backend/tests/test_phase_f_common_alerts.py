from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import LiveCalendarSpreadAlertHistory
from app.notifications.common import AlertEvent, AlertService
from app.notifications.calendar_spread_alerts import CalendarSpreadAlertService
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def test_common_alert_contract_has_stable_dedup_key():
    event = AlertEvent("cash-future", "ABC:CURRENT", "ABC", 123, "x")
    assert event.dedup_key == ("cash-future", "ABC", "ABC:CURRENT", 123)

def test_common_alert_service_is_safe_noop_without_provider(db_session):
    service = AlertService()
    event = AlertEvent("box-spread", "X", "X", 1, "message")
    assert service.dispatch(db_session, event) == 0
    assert service.configured_channels == ()

def test_calendar_alert_persistence_is_90_day_date_safe(db_session, monkeypatch):
    service = CalendarSpreadAlertService()
    signal = SimpleNamespace(
        qualifies=True, underlying="NIFTY", exchange="NFO",
        near_contract_month="2026-10-29", far_contract_month="2026-11-26",
        timestamp_ns=100, direction="LONG_NEAR_SHORT_FAR",
        edge_long=5.0, edge_short=-5.0, gap_points=5.0,
        gross_profit=250.0, lot_size=50,
        near_bid=100.0, near_ask=101.0, far_bid=106.0, far_ask=107.0,
    )
    old = LiveCalendarSpreadAlertHistory(
        observed_at=datetime.utcnow() - timedelta(days=91), timestamp_ns=1,
        underlying="OLD", exchange="NFO", near_contract_month="A", far_contract_month="B",
        direction="LONG_NEAR_SHORT_FAR", edge_long=1.0, edge_short=-1.0,
        gap_points=1.0, gross_profit=50.0, lot_size=50,
    )
    db_session.add(old); db_session.commit()
    monkeypatch.setattr("app.core.config.settings.LIVE_CALENDAR_SPREAD_RESULT_RETENTION_DAYS", 90)
    assert service.persist(db_session, signal) is True
    assert service.persist(db_session, signal) is False
    rows = db_session.query(LiveCalendarSpreadAlertHistory).all()
    assert len(rows) == 1
    assert rows[0].underlying == "NIFTY"

def test_strategy_alert_events_expose_metric_metadata_for_rule_matching():
    from app.notifications.synthetic_alerts import SyntheticAlertService
    from app.notifications.box_spread_alerts import BoxSpreadAlertService

    captured = []
    class CaptureAlerts:
        def dispatch(self, _db, event):
            captured.append(event)
            return 0

    synthetic = SyntheticAlertService()
    synthetic._alerts = CaptureAlerts()
    synthetic.notify_users(object(), [SimpleNamespace(
        option=SimpleNamespace(underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29", strike=25000.0, timestamp_ns=1, call_bid=10.0, call_ask=11.0, put_bid=9.0, put_ask=10.0),
        future=SimpleNamespace(bid=100.0, ask=101.0, lot_size=50), direction="LONG",
        executable_edge=5.0, edge_per_lot=250.0, gross_pnl=250.0,
    )])

    box = BoxSpreadAlertService()
    box._alerts = CaptureAlerts()
    leg = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29", strike=25000.0,
        timestamp_ns=2, call_bid=10.0, call_ask=11.0, put_bid=9.0, put_ask=10.0, lot_size=50,
    )
    high = SimpleNamespace(**{**leg.__dict__, "strike": 25100.0, "call_bid": 5.0, "call_ask": 6.0, "put_bid": 4.0, "put_ask": 5.0})
    box.notify_users(object(), [SimpleNamespace(
        low=leg, high=high, direction="LONG", executable_edge=7.0, edge_per_lot=350.0,
        gross_pnl=350.0, strike_distance=100,
    )])

    synthetic_event, box_event = captured
    assert synthetic_event.strategy_id == "synthetic-future-cash-carry"
    assert synthetic_event.metadata["gap"] == 5.0
    assert synthetic_event.metadata["gross_profit"] == 250.0
    assert box_event.strategy_id == "box-spread"
    assert box_event.metadata["gap"] == 7.0
    assert box_event.metadata["gross_profit"] == 350.0

def test_alert_status_counts_active_user_rules(db_session, monkeypatch):
    from app.alert_routes import get_alert_status
    from app.models import AlertRule, User
    monkeypatch.setattr("app.alert_routes.current_user_id", lambda _db: 1)
    db_session.add(User(id=1, email="alert-status@example.com", hashed_password="x", alerts_enabled=True))
    db_session.add(AlertRule(
        user_id=1, strategy_id="cash-future", name="Active", metric="gap", operator=">=", threshold=1,
        min_gross_profit=0, mobile_number="919999999999", enabled=True,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="box-spread", name="Disabled", metric="gap", operator=">=", threshold=1,
        min_gross_profit=0, mobile_number="919999999999", enabled=False,
    ))
    db_session.commit()
    result = get_alert_status(db_session)
    assert result["active_rules"] == 1
    assert result["triggered_30d"] == 0
    assert result["history_30d"] == 0
    assert result["history_scope"] == "scanner_global"
    assert result["window_days"] == 30

def test_strategy_alert_events_expose_calendar_gap_metadata():
    captured = []

    class CaptureAlerts:
        def dispatch(self, _db, event):
            captured.append(event)
            return 0

    service = CalendarSpreadAlertService(CaptureAlerts())
    signal = SimpleNamespace(
        qualifies=True, underlying="NIFTY", exchange="NFO",
        near_contract_month="2026-10-29", far_contract_month="2026-11-26",
        timestamp_ns=600, direction="LONG_NEAR_SHORT_FAR",
        edge_long=6.0, edge_short=-6.0, gap_points=6.0,
        gross_profit=300.0, lot_size=50, capacity_lots=1,
        near_bid=100.0, near_ask=101.0, far_bid=107.0, far_ask=108.0,
    )
    assert service.emit(object(), signal) == 0
    event = captured[0]
    assert event.strategy_id == "calendar-spread"
    assert event.metadata["gap"] == 6.0
    assert event.metadata["gap_points"] == 6.0
    assert event.metadata["gross_profit"] == 300.0

def test_strategy_metric_rules_match_synthetic_and_box_events(db_session, monkeypatch):
    from app.models import AlertRule, User
    from app.notifications.synthetic_alerts import SyntheticAlertService
    from app.notifications.box_spread_alerts import BoxSpreadAlertService

    user = User(id=1101, email="strategy-metrics@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add_all([
        AlertRule(user_id=1101, strategy_id="synthetic-future-cash-carry",
                  metric="gap", operator=">=", threshold=5.0,
                  min_gross_profit=0, mobile_number="919111111111",
                  whatsapp_enabled=True, enabled=True),
        AlertRule(user_id=1101, strategy_id="box-spread",
                  metric="gross_profit", operator=">=", threshold=300.0,
                  min_gross_profit=0, mobile_number="919222222222",
                  whatsapp_enabled=True, enabled=True),
    ])
    db_session.commit()

    service = AlertService(WhatsAppNotifier(WhatsAppConfig(
        enabled=True, access_token="token", phone_number_id="id"
    )))
    sent = []
    monkeypatch.setattr(service._notifier, "send_text",
                          lambda mobile, msg: sent.append(mobile) or True)

    synthetic_event = AlertEvent(
        "synthetic-future-cash-carry", "SYN-METRIC", "NIFTY", 700, "synthetic",
        metadata={"gap": 5.0, "gross_profit": 250.0},
    )
    box_event = AlertEvent(
        "box-spread", "BOX-METRIC", "NIFTY", 701, "box",
        metadata={"gap": 7.0, "gross_profit": 300.0},
    )
    assert service.dispatch(db_session, synthetic_event) == 1
    assert service.dispatch(db_session, box_event) == 1
    assert sent == ["919111111111", "919222222222"]

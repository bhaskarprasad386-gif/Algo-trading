from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import LiveCalendarSpreadAlertHistory
from app.notifications.common import AlertEvent, AlertService
from app.notifications.calendar_spread_alerts import CalendarSpreadAlertService

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


def test_calendar_paper_payload_preserves_exchange_for_expiry_boundary():
    captured = []

    class CaptureAlerts:
        def dispatch(self, _db, event):
            captured.append(event)
            return 0

    service = CalendarSpreadAlertService(CaptureAlerts())
    signal = SimpleNamespace(
        qualifies=True, underlying="CRUDEOIL", exchange="MCX",
        near_contract_month="2026-10-04", far_contract_month="2026-11-04",
        timestamp_ns=200, direction="LONG_NEAR_SHORT_FAR",
        edge_long=5.0, edge_short=-5.0, gap_points=5.0,
        gross_profit=250.0, lot_size=100,
        capacity_lots=1, near_bid=100.0, near_ask=101.0,
        far_bid=106.0, far_ask=107.0,
    )

    assert service.emit(object(), signal) == 0
    assert captured[0].metadata["paper_trade"]["exchange"] == "MCX"
    assert captured[0].metadata["paper_trade"]["strategy_direction"] == "LONG_NEAR_SHORT_FAR"
    assert captured[0].metadata["paper_trade"]["direction"] == "LONG"
    legs = captured[0].metadata["paper_trade"]["legs"]
    assert legs[0]["side"] == "BUY"
    assert legs[1]["side"] == "SELL"




def test_calendar_alert_dispatch_creates_normalized_paper_trade(db_session):
    from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade

    db_session.add(GlobalPaperSetting(
        user_id=1, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=1, strategy_id="calendar-spread", min_gross_profit=0,
        mobile_number="", whatsapp_enabled=False, enabled=True,
        max_daily_capital=100000, max_simultaneous_positions=2, max_loss=1000,
    ))
    db_session.commit()

    service = CalendarSpreadAlertService()
    signal = SimpleNamespace(
        qualifies=True, underlying="CRUDEOIL", exchange="MCX",
        near_contract_month="2026-10-04", far_contract_month="2026-11-04",
        timestamp_ns=300, direction="LONG_NEAR_SHORT_FAR",
        edge_long=5.0, edge_short=-5.0, gap_points=5.0,
        gross_profit=250.0, lot_size=100, capacity_lots=1,
        near_bid=100.0, near_ask=101.0, far_bid=106.0, far_ask=107.0,
    )

    assert service.emit(db_session, signal) == 0
    trade = db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 1,
        LivePaperTrade.event_id.like("CRUDEOIL:%"),
    ).one()
    assert trade.direction == "LONG"
    assert trade.capital_used > 0
    assert trade.lots == 1

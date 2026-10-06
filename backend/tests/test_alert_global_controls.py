from app.models import AlertRule, GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def _ensure_user(db_session, user_id, email, alerts_enabled=True):
    from app.models import User
    user = db_session.query(User).filter(User.id == user_id).first()
    if user is None:
        user = User(
            id=user_id,
            email=email,
            hashed_password="x",
            alerts_enabled=alerts_enabled,
        )
        db_session.add(user)
    else:
        user.alerts_enabled = alerts_enabled
    db_session.flush()
    return user


def test_global_paper_setting_is_not_strategy_specific(db_session):
    row = GlobalPaperSetting(user_id=1, enabled=True, paper_amount=500000.0, emergency_stop=False)
    db_session.add(row)
    db_session.commit()
    assert row.paper_amount == 500000.0
    assert row.enabled is True


def test_alert_rule_filters_by_strategy_and_gross_profit(db_session, monkeypatch):
    _ensure_user(db_session, 1001, "alert-filter@example.com", True)
    rule = AlertRule(
        user_id=1001,
        strategy_id="cash-future",
        min_gross_profit=1000.0,
        mobile_number="919999999999",
        whatsapp_enabled=True,
        enabled=True,
    )
    db_session.add(rule)
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(
        enabled=True, access_token="token", phone_number_id="id"
    )))
    sent = []
    monkeypatch.setattr(
        service._notifier,
        "send_text",
        lambda mobile, msg: sent.append((mobile, msg)) or True,
    )
    low = AlertEvent("cash-future", "e1", "ABC", 10, "low", metadata={"gross_profit": 999})
    high = AlertEvent("cash-future", "e2", "ABC", 20, "high", metadata={"gross_profit": 1000})
    assert service.dispatch(db_session, low) == 0
    assert service.dispatch(db_session, high) == 1
    assert sent == [("919999999999", "high")]


def test_alert_rule_does_not_cross_strategy(db_session, monkeypatch):
    _ensure_user(db_session, 1, "strategy-isolation@example.com", True)
    rule = AlertRule(
        user_id=1,
        strategy_id="box-spread",
        min_gross_profit=0,
        mobile_number="919999999999",
        whatsapp_enabled=True,
        enabled=True,
    )
    db_session.add(rule)
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(
        enabled=True, access_token="token", phone_number_id="id"
    )))
    monkeypatch.setattr(service._notifier, "send_text", lambda mobile, msg: True)
    event = AlertEvent("calendar-spread", "e3", "NIFTY", 30, "calendar", metadata={"gross_profit": 5000})
    assert service.dispatch(db_session, event) == 0


def test_alert_master_off_stops_notification_but_event_dispatch_remains_safe(db_session, monkeypatch):
    _ensure_user(db_session, 1001, "alert-master@example.com", False)
    rule = AlertRule(
        user_id=1001,
        strategy_id="cash-future",
        min_gross_profit=0,
        metric="gross_profit",
        operator=">=",
        threshold=100,
        mobile_number="919999999999",
        whatsapp_enabled=True,
        enabled=True,
    )
    db_session.add(rule)
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(
        enabled=True, access_token="token", phone_number_id="id"
    )))
    sent = []
    monkeypatch.setattr(
        service._notifier,
        "send_text",
        lambda mobile, msg: sent.append((mobile, msg)) or True,
    )
    event = AlertEvent("cash-future", "master-off", "ABC", 100, "blocked", metadata={"gross_profit": 500})
    assert service.dispatch(db_session, event) == 0
    assert sent == []


def test_alert_metric_operator_threshold_contract(db_session, monkeypatch):
    _ensure_user(db_session, 1002, "alert-metric@example.com", True)
    rule = AlertRule(
        user_id=1002,
        strategy_id="cash-future",
        min_gross_profit=0,
        metric="gap",
        operator=">=",
        threshold=1.5,
        mobile_number="919999999998",
        whatsapp_enabled=True,
        enabled=True,
    )
    db_session.add(rule)
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(
        enabled=True, access_token="token", phone_number_id="id"
    )))
    sent = []
    monkeypatch.setattr(
        service._notifier,
        "send_text",
        lambda mobile, msg: sent.append((mobile, msg)) or True,
    )
    assert service.dispatch(
        db_session,
        AlertEvent(
            "cash-future", "metric-low", "ABC", 200, "low",
            metadata={"gap": 1.49, "gross_profit": 5000},
        ),
    ) == 0
    assert service.dispatch(
        db_session,
        AlertEvent(
            "cash-future", "metric-high", "ABC", 300, "high",
            metadata={"gap": 1.5, "gross_profit": 5000},
        ),
    ) == 1
    assert sent == [("919999999998", "high")]

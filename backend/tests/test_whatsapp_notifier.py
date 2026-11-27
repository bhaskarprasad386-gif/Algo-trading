from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def test_whatsapp_notifier_is_safe_noop_when_disabled():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=False, access_token="x", phone_number_id="y"))
    assert not notifier.configured
    assert notifier.send_text("+919999999999", "test") is False


def test_whatsapp_notifier_requires_recipient():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="x", phone_number_id="y"))
    assert notifier.send_text("", "test") is False


def test_legacy_notify_user_is_notification_only(monkeypatch):
    from types import SimpleNamespace
    from app.notifications.common import AlertEvent, AlertService

    service = AlertService()
    sent = []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, message: sent.append((recipient, message)) or True)
    monkeypatch.setattr("app.notifications.common.settings.ALERT_NOTIFICATION_COOLDOWN_SECONDS", 0.0)

    user = SimpleNamespace(id=7, mobile_number="+919999999999", alerts_enabled=True, whatsapp_alerts_enabled=True, email_alerts_enabled=False)
    event = AlertEvent(
        strategy_id="iv-skew-flip",
        event_id="LEGACY-NOTIFY-NO-PAPER",
        symbol="ABC",
        timestamp_ns=3_000_000_000,
        message="legacy",
        metadata={"gross_profit": 1000.0, "paper_trade": {"direction": "LONG", "lots": 1}},
    )

    assert service.dispatch_user(user, event) is True
    assert sent == [(user.mobile_number, event.message)]

def test_alert_contact_model_supports_multiple_channel_preferences(db_session):
    from app.models import AlertContact, User
    user = User(id=9101, email="contact-test@example.com", hashed_password="x")
    db_session.add(user)
    db_session.add_all([
        AlertContact(user_id=9101, label="Primary", mobile_number="919999999999", whatsapp_enabled=True, app_enabled=True),
        AlertContact(user_id=9101, label="Backup", mobile_number="918888888888", sms_enabled=True, email_enabled=True, app_enabled=False),
    ])
    db_session.commit()
    rows = db_session.query(AlertContact).filter(AlertContact.user_id == 9101).order_by(AlertContact.id).all()
    assert len(rows) == 2
    assert rows[0].whatsapp_enabled is True
    assert rows[1].sms_enabled is True
    assert rows[1].email_enabled is True
    assert rows[1].app_enabled is False


def test_alert_dispatch_uses_enabled_whatsapp_contacts(db_session, monkeypatch):
    from app.models import AlertContact, AlertRule, User
    from app.notifications.common import AlertEvent, AlertService
    from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
    user = User(id=9102, email="dispatch-contact@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add(AlertRule(user_id=9102, strategy_id="cash-future", metric="gross_profit", operator=">=", threshold=100, min_gross_profit=0, mobile_number="919000000000", whatsapp_enabled=False, enabled=True))
    db_session.add(AlertContact(user_id=9102, label="WA", mobile_number="919111111111", whatsapp_enabled=True, enabled=True))
    db_session.add(AlertContact(user_id=9102, label="Off", mobile_number="922222222222", whatsapp_enabled=True, enabled=False))
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="token", phone_number_id="id")))
    sent = []
    monkeypatch.setattr(service._notifier, "send_text", lambda mobile, msg: sent.append(mobile) or True)
    count = service.dispatch(db_session, AlertEvent("cash-future", "contact-event", "ABC", 1000000000, "alert", metadata={"gross_profit": 500}))
    assert count == 1
    assert sent == ["919111111111"]

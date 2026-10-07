def test_alert_contact_model_supports_multiple_channel_preferences(db_session):
    from app.models import AlertContact, User
    user = User(id=9101, email="contact-test@example.com", hashed_password="x")
    db_session.add(user)
    db_session.add_all([
        AlertContact(user_id=9101, label="Primary", mobile_number="919999999999", whatsapp_enabled=True, app_enabled=True),
        AlertContact(user_id=9101, label="Backup", mobile_number="918888888888", email_address="backup@example.com", sms_enabled=True, email_enabled=True, app_enabled=False),
    ])
    db_session.commit()
    rows = db_session.query(AlertContact).filter(AlertContact.user_id == 9101).order_by(AlertContact.id).all()
    assert len(rows) == 2
    assert rows[0].whatsapp_enabled is True
    assert rows[1].sms_enabled is True
    assert rows[1].email_enabled is True
    assert rows[1].email_address == "backup@example.com"
    assert rows[1].app_enabled is False



def test_alert_master_off_blocks_enabled_whatsapp_and_telegram_contacts(db_session, monkeypatch):
    from app.models import AlertContact, AlertRule, User
    from app.notifications.common import AlertEvent, AlertService
    from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
    user = User(id=9103, email="master-contact@example.com", hashed_password="x", alerts_enabled=False)
    db_session.add(user)
    db_session.add(AlertRule(user_id=9103, strategy_id="cash-future", metric="gap", operator=">=", threshold=1, min_gross_profit=0, enabled=True))
    db_session.add(AlertContact(user_id=9103, label="WA", mobile_number="919333333333", whatsapp_enabled=True, telegram_enabled=True, telegram_chat_id="123456", enabled=True))
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="token", phone_number_id="id")))
    wa, tg = [], []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, msg: wa.append(recipient) or True)
    monkeypatch.setattr(service._telegram, "send_text", lambda recipient, msg: tg.append(recipient) or True)
    event = AlertEvent("cash-future", "master-contact-off", "ABC", 1000, "blocked", metadata={"gap": 2, "gross_profit": 500})
    assert service.dispatch(db_session, event) == 0
    assert wa == [] and tg == []


def test_disabled_contact_does_not_block_other_enabled_contact(db_session, monkeypatch):
    from app.models import AlertContact, AlertRule, User
    from app.notifications.common import AlertEvent, AlertService
    from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
    user = User(id=9104, email="contact-isolation@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add(AlertRule(user_id=9104, strategy_id="cash-future", metric="gap", operator=">=", threshold=1, min_gross_profit=0, enabled=True))
    db_session.add_all([
        AlertContact(user_id=9104, label="OFF", mobile_number="919444444444", whatsapp_enabled=True, enabled=False),
        AlertContact(user_id=9104, label="ON", mobile_number="919555555555", whatsapp_enabled=True, enabled=True),
    ])
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="token", phone_number_id="id")))
    sent = []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, msg: sent.append(recipient) or True)
    assert service.dispatch(db_session, AlertEvent("cash-future", "contact-isolation", "ABC", 2000, "alert", metadata={"gap": 2, "gross_profit": 500})) == 1
    assert sent == ["919555555555"]


def test_whatsapp_and_telegram_are_independent_per_contact(db_session, monkeypatch):
    from app.models import AlertContact, AlertRule, User
    from app.notifications.common import AlertEvent, AlertService
    from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
    user = User(id=9105, email="channel-isolation@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add(AlertRule(user_id=9105, strategy_id="cash-future", metric="gap", operator=">=", threshold=1, min_gross_profit=0, enabled=True))
    db_session.add(AlertContact(user_id=9105, label="TelegramOnly", mobile_number="", whatsapp_enabled=False, telegram_enabled=True, telegram_chat_id="987654", enabled=True))
    db_session.commit()
    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="token", phone_number_id="id")))
    wa, tg = [], []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, msg: wa.append(recipient) or True)
    monkeypatch.setattr(service._telegram, "send_text", lambda recipient, msg: tg.append(recipient) or True)
    assert service.dispatch(db_session, AlertEvent("cash-future", "telegram-only", "ABC", 3000, "telegram", metadata={"gap": 2, "gross_profit": 500})) == 1
    assert wa == [] and tg == ["987654"]

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


def test_alert_channel_preferences_are_user_scoped(db_session, monkeypatch):
    from app.models import User
    from app import alert_routes

    user = User(id=9110, email="channels@example.com", hashed_password="x", alert_email="channels@example.com")
    db_session.add(user)
    db_session.commit()
    monkeypatch.setattr(alert_routes, "current_user_id", lambda db: 9110)

    response = alert_routes.set_alert_channel(
        alert_routes.AlertChannelRequest(channel="whatsapp", enabled=False),
        db_session,
    )
    assert response["enabled"] is False
    assert user.whatsapp_alerts_enabled is False

def test_user_notification_channel_preferences_gate_dispatch(db_session, monkeypatch):
    from app.models import AlertContact, AlertRule, User
    from app.notifications.common import AlertEvent, AlertService
    from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier

    user = User(
        id=9111,
        email="channel-gate@example.com",
        hashed_password="x",
        alerts_enabled=True,
        whatsapp_alerts_enabled=False,
        telegram_alerts_enabled=False,
    )
    db_session.add(user)
    db_session.add(AlertRule(
        user_id=9111,
        strategy_id="cash-future",
        metric="gap",
        operator=">=",
        threshold=1,
        min_gross_profit=0,
        enabled=True,
    ))
    db_session.add(AlertContact(
        user_id=9111,
        label="Both",
        mobile_number="919666666666",
        whatsapp_enabled=True,
        telegram_enabled=True,
        telegram_chat_id="123456",
        enabled=True,
    ))
    db_session.commit()

    service = AlertService(
        WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="token", phone_number_id="id"))
    )
    wa, tg = [], []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, msg: wa.append(recipient) or True)
    monkeypatch.setattr(service._telegram, "send_text", lambda recipient, msg: tg.append(recipient) or True)

    count = service.dispatch(
        db_session,
        AlertEvent("cash-future", "user-channel-gate", "ABC", 4000, "blocked", metadata={"gap": 2, "gross_profit": 500}),
    )
    assert count == 0
    assert wa == [] and tg == []

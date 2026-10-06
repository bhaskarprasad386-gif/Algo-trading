from app.models import AlertContact, AlertRule, User
from app.notifications.common import AlertEvent, AlertService
from app.notifications.telegram import TelegramConfig, TelegramNotifier
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def test_telegram_notifier_is_safe_noop_when_disabled():
    notifier = TelegramNotifier(TelegramConfig(enabled=False, bot_token="token"))
    assert notifier.configured is False
    assert notifier.send_text("12345", "test") is False


def test_telegram_notifier_requires_chat_id():
    notifier = TelegramNotifier(TelegramConfig(enabled=True, bot_token="token"))
    assert notifier.configured is True
    assert notifier.send_text("", "test") is False


def test_alert_dispatch_routes_to_enabled_telegram_contact_and_respects_master(db_session, monkeypatch):
    user = User(id=9301, email="telegram-alert@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add(AlertRule(
        user_id=9301, strategy_id="cash-future", metric="gross_profit",
        operator=">=", threshold=100, min_gross_profit=0,
        mobile_number="919000000000", whatsapp_enabled=False, enabled=True,
    ))
    db_session.add(AlertContact(
        user_id=9301, label="Telegram", mobile_number="919111111111",
        telegram_enabled=True, telegram_chat_id="123456789", enabled=True,
    ))
    db_session.add(AlertContact(
        user_id=9301, label="Telegram Off", mobile_number="919222222222",
        telegram_enabled=True, telegram_chat_id="987654321", enabled=False,
    ))
    db_session.commit()

    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=False)))
    sent = []
    monkeypatch.setattr(service._telegram, "send_text", lambda chat_id, msg: sent.append((chat_id, msg)) or True)

    event = AlertEvent("cash-future", "telegram-event", "ABC", 1_000_000_000, "telegram alert", metadata={"gross_profit": 500})
    assert service.dispatch(db_session, event) == 1
    assert sent == [("123456789", "telegram alert")]

    user.alerts_enabled = False
    db_session.commit()
    sent.clear()
    assert service.dispatch(db_session, AlertEvent(
        "cash-future", "telegram-blocked", "ABC", 2_000_000_000, "blocked", metadata={"gross_profit": 500}
    )) == 0
    assert sent == []


def test_alert_dispatch_cooldown_applies_across_distinct_event_timestamps(db_session, monkeypatch):
    user = User(id=9302, email="telegram-cooldown@example.com", hashed_password="x", alerts_enabled=True)
    db_session.add(user)
    db_session.add(AlertRule(
        user_id=9302, strategy_id="cash-future", metric="gross_profit",
        operator=">=", threshold=100, min_gross_profit=0, cooldown_seconds=60,
        mobile_number="919333333333", whatsapp_enabled=False, enabled=True,
    ))
    db_session.add(AlertContact(
        user_id=9302, label="Telegram", mobile_number="919333333333",
        telegram_enabled=True, telegram_chat_id="111222333", enabled=True,
    ))
    db_session.commit()

    service = AlertService(WhatsAppNotifier(WhatsAppConfig(enabled=False)))
    sent = []
    monkeypatch.setattr(service._telegram, "send_text", lambda chat_id, msg: sent.append((chat_id, msg)) or True)

    assert service.dispatch(
        db_session,
        AlertEvent("cash-future", "same-logical-event", "ABC", 100_000_000_000, "first", metadata={"gross_profit": 500}),
    ) == 1
    assert service.dispatch(
        db_session,
        AlertEvent("cash-future", "same-logical-event", "ABC", 101_000_000_000, "within cooldown", metadata={"gross_profit": 500}),
    ) == 0
    assert sent == [("111222333", "first")]

from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def test_whatsapp_notifier_is_safe_noop_when_disabled():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=False, access_token="x", phone_number_id="y"))
    assert not notifier.configured
    assert notifier.send_text("+919999999999", "test") is False


def test_whatsapp_notifier_requires_recipient():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="x", phone_number_id="y"))
    assert notifier.send_text("", "test") is False

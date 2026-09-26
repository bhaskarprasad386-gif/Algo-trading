from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier


def test_whatsapp_notifier_is_safe_noop_when_disabled():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=False, access_token="x", phone_number_id="y"))
    assert not notifier.configured
    assert notifier.send_text("+919999999999", "test") is False


def test_whatsapp_notifier_requires_recipient():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=True, access_token="x", phone_number_id="y"))
    assert notifier.send_text("", "test") is False


def test_live_cash_future_alert_message_contains_custom_profit_fields():
    from app.notifications.service import LiveCashFutureAlert, NotificationService

    message = NotificationService._message(LiveCashFutureAlert(
        symbol="ABC", contract_month="CURRENT", cash_ask=100.0, future_bid=101.0,
        gap=1.0, gap_pct=1.0, timestamp_ns=1_000_000_000,
        lot_size=100, alert_lots=2, gross_profit=200.0, net_profit=180.0,
    ))
    assert "Lot Size: 100 | Lots: 2" in message
    assert "Gross Profit: ₹200.00" in message
    assert "Net Profit: ₹180.00" in message

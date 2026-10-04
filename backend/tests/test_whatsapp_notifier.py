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


def test_notification_service_sends_formatted_alert_to_user(monkeypatch):
    from types import SimpleNamespace
    from app.notifications.service import LiveCashFutureAlert, NotificationService

    service = NotificationService()
    sent = []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, message: sent.append((recipient, message)) or True)
    monkeypatch.setattr("app.notifications.service.settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS", 0.0)

    user = SimpleNamespace(id=7, mobile_number="+919999999999")
    alert = LiveCashFutureAlert(
        symbol="ABC", contract_month="CURRENT", cash_ask=100.0, future_bid=101.0,
        gap=1.0, gap_pct=1.0, timestamp_ns=2_000_000_000,
        lot_size=100, alert_lots=2, gross_profit=200.0, net_profit=180.0,
    )
    assert service.notify_user(user, alert) is True
    assert sent and sent[0][0] == user.mobile_number
    assert "Gross Profit: ₹200.00" in sent[0][1]
    assert "Net Profit: ₹180.00" in sent[0][1]


def test_legacy_notify_user_never_creates_paper_trade(monkeypatch, db_session):
    from types import SimpleNamespace
    from app.models import AlertRule, GlobalPaperSetting, LivePaperTrade
    from app.notifications.common import AlertEvent, AlertService

    db_session.add(GlobalPaperSetting(
        user_id=7, enabled=True, paper_amount=100000, emergency_stop=False,
    ))
    db_session.add(AlertRule(
        user_id=7, strategy_id="cash-future", min_gross_profit=0,
        mobile_number="+919999999999", whatsapp_enabled=True, enabled=True,
        max_daily_capital=0, max_simultaneous_positions=5, max_loss=0,
    ))
    db_session.commit()

    service = AlertService()
    sent = []
    monkeypatch.setattr(service._notifier, "send_text", lambda recipient, message: sent.append((recipient, message)) or True)
    monkeypatch.setattr("app.notifications.common.settings.LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS", 0.0)

    user = SimpleNamespace(id=7, mobile_number="+919999999999")
    event = AlertEvent(
        strategy_id="cash-future",
        event_id="LEGACY-NOTIFY-NO-PAPER",
        symbol="ABC",
        timestamp_ns=3_000_000_000,
        message="legacy",
        metadata={
            "gross_profit": 1000.0,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "earliest_expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 30000,
                "legs": [],
            },
        },
    )

    assert service.dispatch_user(user, event) is True
    assert sent == [(user.mobile_number, event.message)]
    assert db_session.query(LivePaperTrade).filter(
        LivePaperTrade.user_id == 7,
        LivePaperTrade.event_id == event.event_id,
    ).count() == 0

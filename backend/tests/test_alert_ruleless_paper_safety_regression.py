from app.models import GlobalPaperSetting, User
from app.notifications.common import AlertEvent, AlertService
from app.auto.live_paper import LivePaperTradeService


def test_ruleless_paper_alert_is_notification_only_and_creates_no_trade(db_session):
    db_session.add(
        GlobalPaperSetting(
            user_id=1,
            enabled=True,
            paper_amount=1_000_000,
            emergency_stop=False,
        )
    )
    db_session.add(
        User(
            id=1,
            mobile_number="9999999999",
            is_active=True,
        )
    )
    db_session.commit()

    class DummyNotifier:
        configured = True

        def send_text(self, mobile, message):
            return True

    event = AlertEvent(
        strategy_id="cash-future",
        event_id="RULELESS-PAPER-1",
        symbol="AAA",
        timestamp_ns=1,
        message="paper",
        metadata={
            "gross_profit": 1000,
            "paper_trade": {
                "direction": "LONG",
                "expiry": "2026-10-30",
                "lot_size": 10,
                "lots": 1,
                "edge": 5,
                "capital_used": 100_000,
            },
        },
    )

    assert AlertService(DummyNotifier()).dispatch(db_session, event) == 1
    assert LivePaperTradeService().ongoing(db_session, 1) == []

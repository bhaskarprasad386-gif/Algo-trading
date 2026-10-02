from app.models import AlertRule, GlobalPaperSetting
from app.notifications.common import AlertEvent, AlertService

def test_global_paper_setting_is_not_strategy_specific(db_session):
    row = GlobalPaperSetting(user_id=1, enabled=True, paper_amount=500000.0, emergency_stop=False)
    db_session.add(row); db_session.commit()
    assert row.paper_amount == 500000.0
    assert row.enabled is True

def test_alert_rule_filters_by_strategy_and_gross_profit(db_session, monkeypatch):
    rule = AlertRule(user_id=1, strategy_id="cash-future", min_gross_profit=1000.0,
                     mobile_number="919999999999", whatsapp_enabled=True, enabled=True)
    db_session.add(rule); db_session.commit()
    service = AlertService()
    sent = []
    monkeypatch.setattr(service._notifier, "configured", True, raising=False)
    monkeypatch.setattr(service._notifier, "send_text", lambda mobile, msg: sent.append((mobile, msg)) or True)
    low = AlertEvent("cash-future","e1","ABC",10,"low",metadata={"gross_profit":999})
    high = AlertEvent("cash-future","e2","ABC",20,"high",metadata={"gross_profit":1000})
    assert service.dispatch(db_session, low) == 0
    assert service.dispatch(db_session, high) == 1
    assert sent == [("919999999999","high")]

def test_alert_rule_does_not_cross_strategy(db_session, monkeypatch):
    rule = AlertRule(user_id=1, strategy_id="box-spread", min_gross_profit=0,
                     mobile_number="919999999999", whatsapp_enabled=True, enabled=True)
    db_session.add(rule); db_session.commit()
    service = AlertService()
    monkeypatch.setattr(service._notifier, "configured", True, raising=False)
    monkeypatch.setattr(service._notifier, "send_text", lambda mobile, msg: True)
    event = AlertEvent("calendar-spread","e3","NIFTY",30,"calendar",metadata={"gross_profit":5000})
    assert service.dispatch(db_session, event) == 0

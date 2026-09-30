from app.auto.paper_auto import AutoSignal, GlobalPaperAutoService
from app.models.strategy_auto_setting import StrategyAutoSetting
from app.models.strategy_auto_paper_position import StrategyAutoPaperPosition

def test_global_auto_gate_and_lifecycle(db_session):
    service = GlobalPaperAutoService()
    signal = AutoSignal("cash-future", "ABC", 100.0, 102.0, 10, "2026-10-29")
    assert service.qualify_and_enter(db_session, signal) is None
    service.set_enabled(db_session, "cash-future", True)
    position = service.qualify_and_enter(db_session, signal)
    assert position is not None
    assert position.status == "ACTIVE"
    assert position.pnl == 20.0
    assert service.qualify_and_enter(db_session, signal).id == position.id
    service.update_mark(db_session, position.id, 105.0)
    assert db_session.get(StrategyAutoPaperPosition, position.id).pnl == 50.0
    service.close(db_session, position.id)
    assert db_session.get(StrategyAutoPaperPosition, position.id).status == "CLOSED"

def test_auto_setting_is_strategy_scoped(db_session):
    service = GlobalPaperAutoService()
    service.set_enabled(db_session, "box-spread", True)
    assert service.is_enabled(db_session, "box-spread")
    assert not service.is_enabled(db_session, "synthetic-future-cash-carry")

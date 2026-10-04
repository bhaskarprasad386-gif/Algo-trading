from app.auto.live_paper import LivePaperTradeService
from app.models import GlobalPaperSetting, LivePaperTrade


def _setup(db):
    db.add(GlobalPaperSetting(user_id=1, enabled=True, paper_amount=100000, emergency_stop=False))
    db.commit()


def _enter(db, event_id, *, legs=None, metadata=None):
    return LivePaperTradeService().enter_or_mark(
        db, strategy_id="cash-future", symbol="AAA", event_id=event_id,
        direction="LONG", expiry="2026-10-30", lot_size=10, lots=1,
        edge=5, capital_used=30000, legs=legs, metadata=metadata, user_id=1,
    )


def test_malformed_legs_and_metadata_cannot_create_trade(db_session):
    _setup(db_session)
    cases = [
        ("LEGS-STRING", "bad", {}),
        ("LEGS-SCALAR", [1], {}),
        ("LEG-BAD-SIDE", [{"side": "HOLD", "price": 10}], {}),
        ("LEG-MISSING-PRICE", [{"side": "BUY"}], {}),
        ("LEG-NAN-PRICE", [{"side": "BUY", "price": float("nan")}], {}),
        ("LEG-INF-PRICE", [{"side": "SELL", "price": float("inf")}], {}),
        ("META-LIST", [], []),
    ]
    for event_id, legs, metadata in cases:
        trade, created = _enter(db_session, event_id, legs=legs, metadata=metadata)
        assert trade is None
        assert created is False
    assert db_session.query(LivePaperTrade).count() == 0


def test_valid_strategy_leg_shapes_are_preserved(db_session):
    _setup(db_session)
    legs = [
        {"instrument": "FUTURE", "side": "BUY", "price": 100.0},
        {"instrument": "CALL", "side": "SELL", "price": 20.0},
    ]
    metadata = {"exchange": "NSE", "source": "synthetic"}
    trade, created = _enter(db_session, "VALID-LEGS", legs=legs, metadata=metadata)
    assert created is True
    assert trade.legs_json == '[{"instrument": "FUTURE", "price": 100.0, "side": "BUY"}, {"instrument": "CALL", "price": 20.0, "side": "SELL"}]'
    assert '"exchange": "NSE"' in trade.metadata_json

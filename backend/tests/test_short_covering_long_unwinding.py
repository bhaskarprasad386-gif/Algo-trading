from app.scanner.short_covering_long_unwinding import evaluate_signal, robust_z

GROUPS=("liquidity","order_flow","futures","options","structure","participation")
def quality(): return {"fresh_feed":True,"synchronized":True,"execution_quality":True,"regime_known":True}

def test_dual_direction_scores():
    up=evaluate_signal({"groups":{k:.9 for k in GROUPS},"quality":quality()})
    dn=evaluate_signal({"groups":{k:-.9 for k in GROUPS},"quality":quality()})
    assert up["side"]=="BULLISH" and up["alert_eligible"]
    assert dn["side"]=="BEARISH" and dn["alert_eligible"]

def test_conflict_and_missing_evidence_fail_closed():
    mixed=evaluate_signal({"groups":{"liquidity":1,"order_flow":-1,"futures":1,"options":-1,"structure":.5,"participation":-.5},"quality":quality()})
    assert not mixed["alert_eligible"]
    missing=evaluate_signal({"groups":{"futures":1,"structure":1},"quality":quality()})
    assert missing["state"]=="DATA_INSUFFICIENT" and not missing["alert_eligible"]

def test_quality_gates_block_alert():
    result=evaluate_signal({"groups":{k:1 for k in GROUPS},"quality":{"fresh_feed":True}})
    assert result["state"]=="PRE_IMPULSE" and not result["alert_eligible"]

def test_robust_z_guards():
    assert robust_z(5,[1,2,3,4,5,6,7,8]) is not None
    assert robust_z(4,[4]*10) is None
    assert robust_z(4,[1,2,3]) is None

from app.scanner.early_impulse_engine import evaluate_early_impulse


def _valid_snapshot(**overrides):
    data = {
        "direction": "BUY",
        "engines": {
            "absorption": 1,
            "synthetic_spot": 1,
            "failed_auction": 1,
            "market_impact": 1,
            "cross_instrument": 1,
        },
        "data_valid": True,
        "spread_ok": True,
        "entry_not_extended": True,
        "invalidation_defined": True,
        "no_strong_opposite": True,
    }
    data.update(overrides)
    return data


def test_full_quality_snapshot_is_eligible_a_grade():
    result = evaluate_early_impulse(_valid_snapshot())
    assert result["score"] == 100
    assert result["grade"] == "A"
    assert result["eligible"] is True
    assert result["order_routing_enabled"] is False


def test_missing_mandatory_gate_rejects_even_high_score():
    result = evaluate_early_impulse(_valid_snapshot(spread_ok=False))
    assert result["score"] == 100
    assert result["eligible"] is False
    assert "SPREAD_OK" in result["rejection_reasons"]


def test_neutral_direction_cannot_be_trade_eligible():
    result = evaluate_early_impulse(_valid_snapshot(direction="NEUTRAL"))
    assert result["eligible"] is False
    assert "NO_DIRECTION" in result["rejection_reasons"]


def test_invalid_engine_value_scores_zero():
    result = evaluate_early_impulse(_valid_snapshot(engines={"absorption": 5}))
    assert result["components"]["absorption"] == 0
    assert result["eligible"] is False


def test_low_score_is_watch_and_not_eligible():
    result = evaluate_early_impulse(_valid_snapshot(engines={
        "absorption": 0.5, "synthetic_spot": 0.5, "failed_auction": 0.5,
        "market_impact": 0.5, "cross_instrument": 0.5,
    }))
    assert result["score"] == 50
    assert result["grade"] == "REJECT"
    assert result["eligible"] is False

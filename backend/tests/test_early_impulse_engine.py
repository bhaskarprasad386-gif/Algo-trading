from app.scanner.early_impulse_engine import evaluate_early_impulse, evaluate_market_features


def _valid_snapshot(**overrides):
    data = {
        "direction": "BUY",
        "engines": {"absorption": 1, "synthetic_spot": 1, "failed_auction": 1, "market_impact": 1, "cross_instrument": 1},
        "data_valid": True, "spread_ok": True, "entry_not_extended": True,
        "invalidation_defined": True, "no_strong_opposite": True,
    }
    data.update(overrides)
    return data


def test_full_quality_snapshot_is_eligible_a_grade():
    result = evaluate_early_impulse(_valid_snapshot())
    assert result["score"] == 100 and result["grade"] == "A"
    assert result["eligible"] is True and result["order_routing_enabled"] is False


def test_missing_mandatory_gate_rejects_even_high_score():
    result = evaluate_early_impulse(_valid_snapshot(spread_ok=False))
    assert result["score"] == 100 and not result["eligible"]
    assert "SPREAD_OK" in result["rejection_reasons"]


def test_neutral_direction_cannot_be_trade_eligible():
    result = evaluate_early_impulse(_valid_snapshot(direction="NEUTRAL"))
    assert not result["eligible"]
    assert "NO_DIRECTION" in result["rejection_reasons"]


def test_invalid_engine_value_scores_zero():
    result = evaluate_early_impulse(_valid_snapshot(engines={"absorption": 5}))
    assert result["components"]["absorption"] == 0 and not result["eligible"]


def test_low_score_is_rejected():
    result = evaluate_early_impulse(_valid_snapshot(engines={key: .5 for key in (
        "absorption", "synthetic_spot", "failed_auction", "market_impact", "cross_instrument")}))
    assert result["score"] == 50 and result["grade"] == "REJECT" and not result["eligible"


def test_market_features_calculate_directional_buy_candidate_from_quotes():
    result = evaluate_market_features({
        "prices": [100, 100.02, 100.04, 100.06, 100.08],
        "volumes": [10, 15, 20, 25, 30],
        "bid": 100.07, "ask": 100.08, "bid_qty": 1200, "ask_qty": 300,
        "current_price": 100.08, "quote_age_seconds": 0.1, "feed_authenticated": True,
        "future_prices": [100.1, 100.12, 100.14], "spot_prices": [100.0, 100.02, 100.04],
        "cross_prices": [200, 200.2, 200.4], "reference_high": 100.05, "reference_low": 99.9,
    })
    assert result["direction"] == "BUY"
    assert set(result["components"]) == {"absorption", "synthetic_spot", "failed_auction", "market_impact", "cross_instrument"}
    assert result["features"]["spread_pct"] is not None
    assert result["order_routing_enabled"] is False


def test_stale_or_unauthenticated_quote_cannot_qualify():
    result = evaluate_market_features({
        "prices": [100, 100.1, 100.2], "bid": 100.19, "ask": 100.2,
        "bid_qty": 100, "ask_qty": 50, "current_price": 100.2,
        "quote_age_seconds": 20, "feed_authenticated": False,
    })
    assert not result["eligible"]
    assert result["data_valid"] is False
    assert result["entry"] is None


def test_missing_quote_depth_and_cross_confirmation_fail_closed():
    result = evaluate_market_features({
        "prices": [100, 99.9, 99.8], "current_price": 99.8,
        "quote_age_seconds": 0.5, "feed_authenticated": True,
    })
    assert result["direction"] in {"BUY", "SELL", "NEUTRAL"}
    assert not result["eligible"]
    assert result["features"]["spread_pct"] is None


def test_non_finite_engine_input_never_scores():
    result = evaluate_early_impulse(_valid_snapshot(engines={"absorption": float("nan")}))
    assert result["components"]["absorption"] == 0
    assert not result["eligible"

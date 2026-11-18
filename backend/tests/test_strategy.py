from app.algo.strategy import Strategy, StrategyRule, threshold_rule


def test_strategy_composes_rules_with_and_semantics():
    strategy = Strategy(
        name="rsi_obv",
        rules=(
            StrategyRule("rsi_min", threshold_rule("rsi", minimum=50)),
            StrategyRule("obv_min", threshold_rule("obv", minimum=100)),
        ),
    )

    assert strategy.evaluate({"rsi": 55, "obv": 120}) is True
    assert strategy.evaluate({"rsi": 49, "obv": 120}) is False


def test_threshold_rule_supports_bounds_and_missing_fields():
    rule = threshold_rule("rsi", minimum=40, maximum=70)

    assert rule({"rsi": 40}) is True
    assert rule({"rsi": 70}) is True
    assert rule({"rsi": 71}) is False
    assert rule({}) is False

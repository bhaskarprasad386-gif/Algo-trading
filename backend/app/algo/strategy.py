"""Small, deterministic strategy-builder foundation.

This module only defines strategy/rule composition primitives. It does not
place orders or connect to a broker.
"""

from dataclasses import dataclass
from typing import Callable, Mapping


Rule = Callable[[Mapping[str, float]], bool]


@dataclass(frozen=True)
class StrategyRule:
    name: str
    rule: Rule

    def evaluate(self, context: Mapping[str, float]) -> bool:
        return bool(self.rule(context))


@dataclass(frozen=True)
class Strategy:
    name: str
    rules: tuple[StrategyRule, ...]

    def evaluate(self, context: Mapping[str, float]) -> bool:
        return all(rule.evaluate(context) for rule in self.rules)


def threshold_rule(field: str, minimum: float | None = None, maximum: float | None = None) -> Rule:
    if minimum is None and maximum is None:
        raise ValueError("minimum or maximum is required")

    def evaluate(context: Mapping[str, float]) -> bool:
        if field not in context:
            return False
        value = float(context[field])
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
        return True

    return evaluate

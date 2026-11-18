"""Small, deterministic strategy-builder foundation.

This module only defines strategy/rule composition primitives. It does not
place orders or connect to a broker.
"""

from dataclasses import dataclass
from typing import Callable, Mapping
import math


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
    if minimum is not None and not math.isfinite(float(minimum)):
        raise ValueError("minimum must be finite")
    if maximum is not None and not math.isfinite(float(maximum)):
        raise ValueError("maximum must be finite")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("minimum cannot exceed maximum")

    def evaluate(context: Mapping[str, float]) -> bool:
        if field not in context:
            return False
        value = float(context[field])
        if not math.isfinite(value):
            return False
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
        return True

    return evaluate

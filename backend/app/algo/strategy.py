"""Small, deterministic strategy-builder foundation.

This module only defines strategy/rule composition primitives. It does not
place orders or connect to a broker.
"""

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


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


def _rsi(closes: Sequence[float], period: int = 14) -> float:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if len(closes) < period + 1:
        raise ValueError("not enough closes for RSI")

    gains = []
    losses = []
    for previous, current in zip(closes[-period - 1:-1], closes[-period:]):
        change = float(current) - float(previous)
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


@dataclass(frozen=True)
class RsiSidewaysScanner:
    """First scanner: sideways price + rising RSI + rising OBV + delivery > 50%."""

    rsi_period: int = 14
    sideways_window: int = 10
    max_range_percent: float = 3.0
    min_delivery_percent: float = 50.0

    def scan(
        self,
        closes: Sequence[float],
        volumes: Sequence[float],
        delivery_percent: float,
    ) -> dict[str, object]:
        if len(closes) != len(volumes):
            raise ValueError("closes and volumes must have the same length")
        if len(closes) < self.rsi_period + 2:
            raise ValueError("not enough candles for scanner")
        if self.sideways_window < 2:
            raise ValueError("sideways_window must be at least 2")

        window = [float(value) for value in closes[-self.sideways_window:]]
        midpoint = (max(window) + min(window)) / 2
        range_percent = 0.0 if midpoint == 0 else ((max(window) - min(window)) / midpoint) * 100

        current_rsi = _rsi(closes, self.rsi_period)
        previous_rsi = _rsi(closes[:-1], self.rsi_period)

        obv = 0.0
        previous_obv = 0.0
        for previous, current, volume in zip(closes[:-1], closes[1:], volumes[1:]):
            if current > previous:
                obv += float(volume)
            elif current < previous:
                obv -= float(volume)
        for previous, current, volume in zip(closes[:-2], closes[1:-1], volumes[1:-1]):
            if current > previous:
                previous_obv += float(volume)
            elif current < previous:
                previous_obv -= float(volume)

        checks = {
            "sideways": range_percent <= self.max_range_percent,
            "rsi_rising": current_rsi >= previous_rsi,
            "obv_rising": obv > previous_obv,
            "delivery_above_50": float(delivery_percent) > self.min_delivery_percent,
        }
        return {
            "match": all(checks.values()),
            "checks": checks,
            "rsi": round(current_rsi, 2),
            "previous_rsi": round(previous_rsi, 2),
            "range_percent": round(range_percent, 2),
            "delivery_percent": float(delivery_percent),
        }

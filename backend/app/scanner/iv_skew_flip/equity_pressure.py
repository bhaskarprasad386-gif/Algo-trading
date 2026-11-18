"""Rolling, weight-aware constituent pressure for IV early-move research.

This module is deliberately pure and has no broker, order, or runtime side
effects. Callers must supply verified index weights and source-timestamped
cash-equity prices. Incomplete coverage never produces a directional state.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from threading import RLock
from typing import Iterable


@dataclass(frozen=True)
class ComponentWeight:
    symbol: str
    weight: float


@dataclass(frozen=True)
class ComponentSample:
    symbol: str
    timestamp_ns: int
    ltp: float


class EquityComponentPressure:
    """Compute weighted 5s/10s/30s constituent returns with strict coverage."""

    WINDOWS_SECONDS = (5, 10, 30)

    def __init__(
        self,
        basket: Iterable[ComponentWeight],
        *,
        max_tick_age_seconds: float = 3.0,
        min_weight_coverage: float = 0.90,
        min_abs_return_pct: float = 0.03,
        min_breadth_pct: float = 60.0,
        history_seconds: int = 45,
    ) -> None:
        items = tuple(basket)
        if not items:
            raise ValueError("component basket cannot be empty")
        numeric_config = (max_tick_age_seconds, min_weight_coverage, min_abs_return_pct, min_breadth_pct)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
               for value in numeric_config):
            raise ValueError("numeric configuration values must be finite numbers")
        if max_tick_age_seconds <= 0 or isinstance(history_seconds, bool) or not isinstance(history_seconds, int) or history_seconds < max(self.WINDOWS_SECONDS):
            raise ValueError("invalid freshness or history configuration")
        if not 0 < min_weight_coverage <= 1:
            raise ValueError("min_weight_coverage must be in (0, 1]")
        if min_abs_return_pct < 0 or not 50 <= min_breadth_pct <= 100:
            raise ValueError("invalid directional thresholds")
        normalized: dict[str, float] = {}
        for item in items:
            symbol = str(item.symbol).strip().upper()
            weight = self._finite(item.weight)
            if not symbol or weight is None or weight <= 0 or symbol in normalized:
                raise ValueError("basket needs unique symbols and positive finite weights")
            normalized[symbol] = weight
        weight_total = sum(normalized.values())
        if not isfinite(weight_total) or weight_total <= 0:
            raise ValueError("total component weight must be finite and positive")
        self._weights = normalized
        self._weight_total = weight_total
        self._max_age_ns = int(max_tick_age_seconds * 1_000_000_000)
        self._min_coverage = min_weight_coverage
        self._min_return = min_abs_return_pct
        self._min_breadth = min_breadth_pct
        self._history_ns = history_seconds * 1_000_000_000
        self._samples: dict[str, deque[ComponentSample]] = {s: deque() for s in normalized}
        self._lock = RLock()

    @staticmethod
    def _finite(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if isfinite(number) else None

    def update(self, sample: ComponentSample) -> bool:
        symbol = str(sample.symbol).strip().upper()
        price = self._finite(sample.ltp)
        if (symbol not in self._weights or price is None or price <= 0
                or isinstance(sample.timestamp_ns, bool)
                or not isinstance(sample.timestamp_ns, int) or sample.timestamp_ns <= 0):
            return False
        with self._lock:
            history = self._samples[symbol]
            if history and sample.timestamp_ns <= history[-1].timestamp_ns:
                return False
            history.append(ComponentSample(symbol, sample.timestamp_ns, price))
            cutoff = sample.timestamp_ns - self._history_ns
            while len(history) > 2 and history[1].timestamp_ns < cutoff:
                history.popleft()
        return True

    def _return_at(self, history: deque[ComponentSample], target_ns: int, now_ns: int) -> float | None:
        baseline = None
        latest = None
        for sample in history:
            if sample.timestamp_ns <= target_ns:
                baseline = sample
            if sample.timestamp_ns <= now_ns:
                latest = sample
        if baseline is None or latest is None or baseline.ltp <= 0:
            return None
        # Avoid silently turning a 5-second return into a longer-window return.
        if target_ns - baseline.timestamp_ns > 2_000_000_000:
            return None
        if now_ns - latest.timestamp_ns > self._max_age_ns:
            return None
        return (latest.ltp / baseline.ltp - 1.0) * 100.0

    def snapshot(self, *, now_ns: int) -> dict[str, object]:
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0:
            raise ValueError("now_ns must be a positive integer")
        with self._lock:
            histories = {symbol: tuple(values) for symbol, values in self._samples.items()}
        fresh: dict[str, ComponentSample] = {}
        for symbol, history in histories.items():
            if history and 0 <= now_ns - history[-1].timestamp_ns <= self._max_age_ns:
                fresh[symbol] = history[-1]
        fresh_weight = sum(self._weights[s] for s in fresh)
        coverage = fresh_weight / self._weight_total if self._weight_total else 0.0
        windows: dict[str, dict[str, object]] = {}
        for seconds in self.WINDOWS_SECONDS:
            returns: dict[str, float] = {}
            target = now_ns - seconds * 1_000_000_000
            for symbol in fresh:
                value = self._return_at(deque(histories[symbol]), target, now_ns)
                if value is not None:
                    returns[symbol] = value
            covered_weight = sum(self._weights[s] for s in returns)
            window_coverage = covered_weight / self._weight_total
            if returns and covered_weight > 0:
                weighted_return = sum(self._weights[s] * returns[s] for s in returns) / covered_weight
                positive_weight = sum(self._weights[s] for s, v in returns.items() if v > 0)
                negative_weight = sum(self._weights[s] for s, v in returns.items() if v < 0)
                breadth_up = positive_weight / covered_weight * 100
                breadth_down = negative_weight / covered_weight * 100
                contributors = sorted(
                    ({"symbol": s, "weight": self._weights[s], "return_pct": returns[s],
                      "weighted_contribution_pct": self._weights[s] / covered_weight * returns[s]}
                     for s in returns),
                    key=lambda row: abs(float(row["weighted_contribution_pct"])), reverse=True,
                )
            else:
                weighted_return = breadth_up = breadth_down = None
                contributors = []
            ready = window_coverage >= self._min_coverage and weighted_return is not None
            if not ready:
                direction = "WAITING"
            elif weighted_return >= self._min_return and breadth_up >= self._min_breadth:
                direction = "BULLISH"
            elif weighted_return <= -self._min_return and breadth_down >= self._min_breadth:
                direction = "BEARISH"
            else:
                direction = "MIXED"
            windows[f"{seconds}s"] = {
                "status": "READY" if ready else "INSUFFICIENT_COVERAGE",
                "coverage_pct": round(window_coverage * 100, 2),
                "weighted_return_pct": round(weighted_return, 5) if weighted_return is not None else None,
                "advancing_weight_pct": round(breadth_up, 2) if breadth_up is not None else None,
                "declining_weight_pct": round(breadth_down, 2) if breadth_down is not None else None,
                "direction": direction,
                "top_contributors": contributors[:5],
            }
        overall_ready = coverage >= self._min_coverage and all(
            item["status"] == "READY" for item in windows.values()
        )
        directions = {str(item["direction"]) for item in windows.values() if item["direction"] != "WAITING"}
        agreement = (
            overall_ready
            and len(directions) == 1
            and next(iter(directions), None) in {"BULLISH", "BEARISH"}
        )
        overall_direction = next(iter(directions)) if agreement else ("MIXED" if overall_ready else "WAITING")
        any_samples = any(histories.values())
        return {
            "status": "LIVE" if overall_ready else ("PARTIAL" if fresh else ("STALE" if any_samples else "WAITING_FOR_TICKS")) ,
            "signals_allowed": overall_ready and agreement and overall_direction in {"BULLISH", "BEARISH"},
            "expected_components": len(self._weights),
            "fresh_components": len(fresh),
            "weight_coverage_pct": round(coverage * 100, 2),
            "direction": overall_direction,
            "timeframe_agreement": agreement,
            "windows": windows,
        }

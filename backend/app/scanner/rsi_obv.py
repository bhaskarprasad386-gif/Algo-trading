"""Deterministic first scanner: price weakness/sideways + rising RSI + rising OBV."""

from typing import Sequence


def _closes(candles: Sequence[Sequence[float]]) -> list[float]:
    return [float(c[4]) for c in candles if len(c) >= 5]


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for a, b in zip(closes[-period - 1:-1], closes[-period:]):
        delta = b - a
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def obv(closes: Sequence[float], volumes: Sequence[float]) -> float:
    value = 0.0
    for i in range(1, min(len(closes), len(volumes))):
        if closes[i] > closes[i - 1]:
            value += float(volumes[i])
        elif closes[i] < closes[i - 1]:
            value -= float(volumes[i])
    return value


def scan(candles: Sequence[Sequence[float]], lookback: int = 5) -> dict:
    if len(candles) < 20:
        return {"qualified": False, "reason": "insufficient_candles"}
    closes = _closes(candles)
    volumes = [float(c[5]) for c in candles if len(c) >= 6]
    if len(closes) != len(volumes):
        return {"qualified": False, "reason": "invalid_candle_shape"}

    current_rsi = rsi(closes)
    previous_rsi = rsi(closes[:-lookback]) if len(closes) > lookback + 14 else None
    current_obv = obv(closes, volumes)
    previous_obv = obv(closes[:-lookback], volumes[:-lookback])
    recent = closes[-lookback:]
    start = recent[0]
    end = recent[-1]
    price_change_pct = ((end - start) / start * 100.0) if start else 0.0

    price_weak_or_sideways = price_change_pct <= 0.5 and price_change_pct >= -3.0
    rsi_rising = previous_rsi is not None and current_rsi >= previous_rsi
    obv_rising = current_obv >= previous_obv
    qualified = price_weak_or_sideways and rsi_rising and obv_rising

    return {
        "qualified": qualified,
        "price_change_pct": round(price_change_pct, 4),
        "rsi": round(current_rsi, 4) if current_rsi is not None else None,
        "rsi_rising": rsi_rising,
        "obv": round(current_obv, 4),
        "obv_rising": obv_rising,
    }

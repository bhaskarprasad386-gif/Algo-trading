"""Pure, serializable IV analytics handlers for the isolated worker process."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .engine import (
    OptionQuote, ScannerConfig, evaluate_skew, evaluate_flip,
    evaluate_gamma_blast, confluence, Signal,
)


def evaluate_iv_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one immutable snapshot; performs no I/O and owns no runtime state.

    Payload contract is intentionally primitive-only so it can cross a spawn
    process boundary without sharing broker adapters, ORM sessions or locks.
    """
    if not isinstance(payload, dict):
        raise TypeError("IV worker payload must be a mapping")
    quote_rows = payload.get("quotes")
    if not isinstance(quote_rows, (tuple, list)):
        raise ValueError("quotes must be a sequence")
    config_values = payload.get("config")
    if not isinstance(config_values, dict):
        raise ValueError("config must be a mapping")
    config = ScannerConfig(**config_values)
    quotes = tuple(OptionQuote(**row) for row in quote_rows)
    now = datetime.fromisoformat(str(payload["now"]))
    now_ns = payload["now_ns"]
    spot = payload["spot"]
    if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0:
        raise ValueError("now_ns must be a positive integer")
    previous_diff = payload.get("previous_atm_iv_diff")
    skew = evaluate_skew(quotes, float(spot), now, now_ns, config)
    flip = evaluate_flip(quotes, float(spot), now, now_ns, config, previous_diff)
    gamma_events = []
    prior_rows = payload.get("oi_references") or {}
    for quote in quotes:
        prior = prior_rows.get(quote.token)
        if prior is None:
            continue
        gamma = evaluate_gamma_blast(
            quote, OptionQuote(**prior),
            min_oi_drop_pct=config.gamma_min_oi_drop_pct,
            min_premium_rise_pct=config.gamma_min_premium_rise_pct,
        )
        if gamma.status == "SIGNAL":
            gamma_events.append(gamma)
    directions = {item.direction for item in gamma_events}
    if len(directions) == 1:
        direction = next(iter(directions))
        gamma_signal = {
            "strategy": "GAMMA_BLAST", "direction": direction,
            "value": float(len(gamma_events)),
            "threshold": float(config.gamma_min_oi_drop_pct),
            "status": "SIGNAL",
            "reason": f"{len(gamma_events)} contracts confirm {direction} OI unwinding with premium expansion",
        }
    elif len(directions) > 1:
        gamma_signal = {
            "strategy": "GAMMA_BLAST", "direction": None,
            "value": float(len(gamma_events)), "threshold": float(config.gamma_min_oi_drop_pct),
            "status": "NEUTRAL", "reason": "conflicting CE and PE unwinding signals; direction suppressed",
        }
    else:
        gamma_signal = {
            "strategy": "GAMMA_BLAST", "direction": None, "value": 0.0,
            "threshold": float(config.gamma_min_oi_drop_pct), "status": "WAITING",
            "reason": "no confirmed five-minute OI reference with premium expansion",
        }
    combined = confluence((skew, flip, Signal(**gamma_signal)), required=2)
    def serialize(signal):
        return {
            "strategy": signal.strategy, "direction": signal.direction,
            "value": signal.value, "threshold": signal.threshold,
            "status": signal.status, "reason": signal.reason,
        }
    return {
        "evaluated_at_ns": now_ns,
        "strategies": [serialize(skew), serialize(flip), gamma_signal],
        "confluence": serialize(combined),
        "data_ready": skew.status != "WAITING" and flip.status != "WAITING",
        "signals_allowed": combined.status == "SIGNAL",
    }


__all__ = ["evaluate_iv_snapshot"]

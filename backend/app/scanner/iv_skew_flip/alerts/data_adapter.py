"""Adapter from the existing IV one-second sampler to alert-domain inputs."""
from __future__ import annotations

from typing import Any

from .models import OptionObservation


def option_observation(sample: Any) -> OptionObservation:
    """Convert an OptionMetricSample without promoting display/LAST values to live."""
    return OptionObservation(
        token=str(sample.token), underlying=str(sample.underlying).upper(),
        expiry=str(sample.expiry), strike=float(sample.strike),
        option_type=str(sample.option_type).upper(),
        source_timestamp_ns=int(sample.source_timestamp_ns),
        sampled_at_ns=int(sample.sampled_at_ns),
        spot_timestamp_ns=int(sample.spot_timestamp_ns),
        spot_price=float(sample.spot_price or 0.0),
        iv=None if sample.iv is None else float(sample.iv),
        iv_input=sample.iv_input,
        bid=None if sample.bid is None else float(sample.bid),
        ask=None if sample.ask is None else float(sample.ask),
        ltp=None if sample.ltp is None else float(sample.ltp),
        oi=None if sample.oi is None else int(sample.oi),
        oi_timestamp_ns=getattr(sample, "oi_timestamp_ns", None),
        volume=None if sample.volume is None else int(sample.volume),
    )


def sampler_history(sampler: Any, token: str) -> tuple[OptionObservation, ...]:
    """Return bounded sampler history in source-time order, never display rows."""
    history = sampler.history_snapshot(token)
    return tuple(option_observation(item) for item in history)


def live_sampler_snapshot(sampler: Any) -> dict[str, Any]:
    """Expose only the sampler's current live metrics and status for gating."""
    snapshot = sampler.snapshot()
    return {
        "status": snapshot.get("status", "WAITING"),
        "sampled_at_ns": snapshot.get("sampled_at_ns"),
        "live_metrics": tuple(snapshot.get("live_metrics", ())),
        "reason": snapshot.get("reason"),
    }

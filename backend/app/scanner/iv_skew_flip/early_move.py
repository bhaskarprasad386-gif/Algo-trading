"""Short-horizon, alert-only early-move detector for the isolated IV scanner.

Uses source-timestamped cumulative option volume deltas, synchronized IV/mark
changes and underlying spot movement. It does not infer institutional intent
or place orders. Baselines warm up from live observations and fail closed when
quotes are stale or the sample is insufficient.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable


@dataclass(frozen=True)
class _Observation:
    timestamp_ns: int
    volume: int
    mark: float | None
    iv: float | None


class IVEarlyMoveDetector:
    """Stateful per-expiry detector; call once per runtime refresh (about 1s)."""

    BUCKET_NS = 10_000_000_000
    HISTORY_BUCKETS = 20
    MAX_AGE_NS = 5_000_000_000

    def __init__(self) -> None:
        self._last: dict[str, _Observation] = {}
        self._volume_buckets: dict[str, dict[int, int]] = defaultdict(dict)
        self._first_volume_bucket: dict[str, int] = {}
        self._spot: deque[tuple[int, float]] = deque(maxlen=180)
        self._state: dict[str, Any] = {
            "state": "WATCH",
            "direction": None,
            "score": 0,
            "volume_ratio": None,
            "premium_change_pct": None,
            "iv_change_points": None,
            "spot_change_pct": None,
            "contracts_confirming": 0,
            "updated_at_ns": None,
            "reason": "warming up on fresh market ticks",
            "signals_allowed": False,
        }

    @staticmethod
    def _valid_number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if isfinite(result) else None

    def update(
        self,
        *,
        quotes: Iterable[Any],
        metrics: Iterable[dict[str, Any]],
        spot_price: float,
        now_ns: int,
        spot_timestamp_ns: int | None = None,
    ) -> dict[str, Any]:
        previous_state = dict(self._state)
        spot = self._valid_number(spot_price)
        if spot is None or spot <= 0 or isinstance(now_ns, bool) or now_ns <= 0:
            return self._waiting(now_ns, "valid spot and timestamp required")
        spot_stamp = now_ns if spot_timestamp_ns is None else spot_timestamp_ns
        if isinstance(spot_stamp, bool) or not isinstance(spot_stamp, int) or spot_stamp <= 0 or spot_stamp > now_ns or now_ns - spot_stamp > self.MAX_AGE_NS:
            return self._waiting(now_ns, "fresh spot tick required")
        if not self._spot or spot_stamp > self._spot[-1][0]:
            self._spot.append((spot_stamp, spot))
        cutoff = now_ns - self.HISTORY_BUCKETS * self.BUCKET_NS
        while self._spot and self._spot[0][0] < now_ns - 120_000_000_000:
            self._spot.popleft()

        metric_by_token = {
            str(row.get("token")): row for row in metrics
            if isinstance(row, dict) and row.get("token") is not None
        }
        candidates: dict[str, list[tuple[float, float, float]]] = {
            "BULLISH": [], "BEARISH": [],
        }
        ratios: list[float] = []
        for quote in quotes:
            token = str(getattr(quote, "token", "") or "")
            ts = getattr(quote, "timestamp_ns", None)
            volume = getattr(quote, "volume", None)
            side = str(getattr(quote, "option_type", "")).upper()
            bid = self._valid_number(getattr(quote, "bid", None))
            ask = self._valid_number(getattr(quote, "ask", None))
            metric = metric_by_token.get(token, {})
            if (not token or side not in {"CE", "PE"} or isinstance(ts, bool)
                    or not isinstance(ts, int) or ts <= 0 or ts > now_ns
                    or now_ns - ts > self.MAX_AGE_NS
                    or isinstance(volume, bool) or not isinstance(volume, int)
                    or volume < 0):
                continue
            # IV must be calculated from this exact option tick and a fresh
            # spot snapshot. Mixing an older IV sample with a newer quote can
            # manufacture apparent IV/premium acceleration.
            metric_source_ts = metric.get("source_timestamp_ns")
            metric_spot_ts = metric.get("spot_timestamp_ns")
            if (metric.get("iv_input") != "BID_ASK_MIDPOINT"
                    or metric_source_ts != ts
                    or isinstance(metric_spot_ts, bool)
                    or not isinstance(metric_spot_ts, int)
                    or metric_spot_ts <= 0 or metric_spot_ts > now_ns
                    or now_ns - metric_spot_ts > self.MAX_AGE_NS):
                continue
            if bid is None or ask is None or bid <= 0 or ask < bid:
                continue
            mark = (bid + ask) / 2.0
            if mark <= 0 or (ask - bid) * 100.0 / mark > 8.0:
                continue
            iv = self._valid_number(metric.get("iv"))
            previous = self._last.get(token)
            self._last[token] = _Observation(ts, volume, mark, iv)
            if previous is None or ts <= previous.timestamp_ns or volume < previous.volume:
                continue
            increment = volume - previous.volume
            previous_bucket = previous.timestamp_ns // self.BUCKET_NS
            bucket = ts // self.BUCKET_NS
            buckets = self._volume_buckets[token]
            self._first_volume_bucket.setdefault(token, previous_bucket)
            # A cumulative-volume delta spanning two 10-second buckets cannot
            # be attributed reliably to either interval. Drop only this
            # interval's volume contribution; retain the quote observation so
            # premium/IV processing and later same-bucket deltas still work.
            crossed_bucket = (
                bucket != previous_bucket
                and ts - previous.timestamp_ns < self.MAX_AGE_NS
            )
            # Suppress only short, fresh intervals that straddle a boundary:
            # their full cumulative delta could be a boundary artefact. For
            # wider sampling gaps (e.g. 10s/20s baseline observations), retain
            # the established endpoint-bucket attribution so time baselines
            # continue to accumulate normally.
            if not crossed_bucket:
                buckets[bucket] = buckets.get(bucket, 0) + increment
            for old_bucket in tuple(buckets):
                if old_bucket * self.BUCKET_NS < cutoff:
                    del buckets[old_bucket]
            # Empty 10-second buckets are real zero-volume observations.
            # Excluding them would inflate the baseline for illiquid contracts.
            elapsed_buckets = max(0, bucket - self._first_volume_bucket[token])
            available_prior = min(self.HISTORY_BUCKETS, elapsed_buckets)
            prior_buckets = [bucket - offset for offset in range(1, available_prior + 1)]
            prior_volumes = [buckets.get(key, 0) for key in prior_buckets]
            baseline = sum(prior_volumes) / available_prior if available_prior >= 12 else 0.0
            current_volume = buckets.get(bucket, 0)
            ratio = (
                current_volume / baseline
                if baseline > 0 and not crossed_bucket
                else None
            )
            if ratio is not None:
                ratios.append(ratio)
            premium_change = (
                (mark - previous.mark) * 100.0 / previous.mark
                if mark is not None and previous.mark is not None and previous.mark > 0
                else None
            )
            iv_change = iv - previous.iv if iv is not None and previous.iv is not None else None
            if ratio is not None and premium_change is not None and iv_change is not None:
                direction = "BULLISH" if side == "CE" else "BEARISH"
                candidates[direction].append((ratio, premium_change, iv_change))

        if len(self._spot) < 2:
            return self._waiting(now_ns, "collecting spot history")
        spot_10s = next((price for stamp, price in reversed(self._spot)
                         if stamp <= now_ns - 10_000_000_000), None)
        spot_60s = next((price for stamp, price in reversed(self._spot)
                         if stamp <= now_ns - 60_000_000_000), None)
        spot_change_10s = None if spot_10s is None else (spot - spot_10s) * 100.0 / spot_10s
        spot_change_60s = None if spot_60s is None else (spot - spot_60s) * 100.0 / spot_60s

        summaries = {}
        for direction, rows in candidates.items():
            if not rows:
                continue
            best = max(rows, key=lambda row: row[0] + max(0.0, row[1]) + max(0.0, row[2]))
            qualifying = sum(1 for row in rows if row[0] >= 4.0 and row[1] >= 0.75 and row[2] >= 0.15)
            summaries[direction] = {
                "contracts": qualifying,
                "observed_contracts": len(rows),
                "volume_ratio": best[0],
                "premium_change_pct": best[1],
                "iv_change_points": best[2],
            }
        prior_direction = previous_state.get("direction")
        adverse_spot = (
            spot_change_10s is not None
            and ((prior_direction == "BULLISH" and spot_change_10s <= -0.05)
                 or (prior_direction == "BEARISH" and spot_change_10s >= 0.05))
        )
        if previous_state.get("state") == "PRE_MOVE" and adverse_spot:
            self._state = {
                "state": "INVALIDATED", "direction": prior_direction, "score": 0,
                "volume_ratio": None, "premium_change_pct": None,
                "iv_change_points": None, "spot_change_pct": round(spot_change_10s, 4),
                "contracts_confirming": 0, "updated_at_ns": now_ns,
                "reason": "spot moved against the pre-move direction; setup invalidated",
                "signals_allowed": False, "baseline_status": "READY",
            }
            return dict(self._state)
        if not summaries:
            # Keep a just-triggered alert visible briefly while its underlying
            # source quotes remain fresh. Do not extend the evidence clock on
            # repeated UI/runtime refreshes: evidence_at_ns is set only when a
            # qualifying state is first produced.
            evidence_at_ns = previous_state.get("evidence_at_ns")
            fresh_option_count = sum(
                1 for quote in quotes
                if isinstance(getattr(quote, "timestamp_ns", None), int)
                and not isinstance(getattr(quote, "timestamp_ns", None), bool)
                and 0 <= now_ns - quote.timestamp_ns <= self.MAX_AGE_NS
                and self._valid_number(getattr(quote, "bid", None)) is not None
                and self._valid_number(getattr(quote, "ask", None)) is not None
            )
            if (
                previous_state.get("state") in {"PRE_MOVE", "CONFIRMED"}
                and isinstance(evidence_at_ns, int)
                and 0 <= now_ns - evidence_at_ns <= self.MAX_AGE_NS
                and fresh_option_count >= 2
            ):
                held = dict(previous_state)
                held["updated_at_ns"] = now_ns
                held["reason"] = "holding recent alert while source quotes remain fresh; waiting for next market tick"
                self._state = held
                return dict(self._state)
            return self._waiting(now_ns, "waiting for new volume, premium and IV ticks")

        ranked = sorted(summaries.items(), key=lambda item: (
            item[1]["contracts"], item[1]["volume_ratio"],
            item[1]["premium_change_pct"], item[1]["iv_change_points"]
        ), reverse=True)
        direction, values = ranked[0]
        if previous_state.get("state") == "PRE_MOVE" and prior_direction and direction != prior_direction:
            self._state = {
                "state": "INVALIDATED", "direction": prior_direction, "score": 0,
                "volume_ratio": None, "premium_change_pct": None,
                "iv_change_points": None, "spot_change_pct": None,
                "contracts_confirming": 0, "updated_at_ns": now_ns,
                "reason": "fresh option pressure reversed against the prior pre-move direction",
                "signals_allowed": False, "baseline_status": "READY",
            }
            return dict(self._state)
        conflicting = len(ranked) > 1 and ranked[1][1]["contracts"] == values["contracts"]
        if conflicting:
            return self._waiting(now_ns, "conflicting CE and PE pressure; direction suppressed")

        volume_ratio = values["volume_ratio"]
        premium_rise = values["premium_change_pct"] >= 0.75
        iv_rise = values["iv_change_points"] >= 0.15
        volume_watch = volume_ratio >= 2.0
        volume_confirm = volume_ratio >= 4.0
        spot_aligned = (
            spot_change_10s is not None and
            ((direction == "BULLISH" and spot_change_10s > 0)
             or (direction == "BEARISH" and spot_change_10s < 0))
        )
        score = sum((volume_watch, premium_rise, iv_rise, spot_aligned))
        spot_confirmed = spot_change_60s is not None and (
            (direction == "BULLISH" and spot_change_60s >= 0.15)
            or (direction == "BEARISH" and spot_change_60s <= -0.15)
        )
        option_pressure_confirmed = (
            volume_confirm and premium_rise and iv_rise and values["contracts"] >= 2
        )
        # Early-entry setup: option pressure must lead, while spot has not
        # materially moved yet. A small opposing wiggle is tolerated, but a
        # meaningful adverse move suppresses the setup. This deliberately
        # avoids waiting for spot alignment, which would make the alert late.
        spot_not_adverse = spot_change_10s is not None and spot_change_60s is not None and (
            (direction == "BULLISH" and spot_change_10s >= -0.03 and spot_change_60s >= -0.05)
            or (direction == "BEARISH" and spot_change_10s <= 0.03 and spot_change_60s <= 0.05)
        )
        if spot_confirmed and option_pressure_confirmed:
            state = "CONFIRMED"
            reason = "multi-contract option pressure is confirmed by a 60-second underlying move"
        elif option_pressure_confirmed and spot_not_adverse:
            state = "PRE_MOVE"
            reason = "early-entry candidate: multi-contract volume, premium and IV pressure before a material spot move"
        elif volume_watch or premium_rise or iv_rise:
            state = "WATCH"
            reason = "early pressure detected; waiting for independent confirmation"
        else:
            state = "WATCH"
            reason = "no qualifying early-move evidence yet"

        self._state = {
            "state": state,
            "direction": direction,
            "score": score,
            "volume_ratio": round(volume_ratio, 2),
            "premium_change_pct": round(values["premium_change_pct"], 3),
            "iv_change_points": round(values["iv_change_points"], 4),
            "spot_change_pct": None if spot_change_10s is None else round(spot_change_10s, 4),
            "spot_change_60s_pct": None if spot_change_60s is None else round(spot_change_60s, 4),
            "contracts_confirming": values["contracts"],
            "updated_at_ns": now_ns,
            "evidence_at_ns": (
                previous_state.get("evidence_at_ns", now_ns)
                if state in {"PRE_MOVE", "CONFIRMED"}
                and previous_state.get("state") == state
                and previous_state.get("direction") == direction
                else now_ns if state in {"PRE_MOVE", "CONFIRMED"} else None
            ),
            "reason": reason,
            "signals_allowed": state in {"PRE_MOVE", "CONFIRMED"},
            "baseline_status": "READY" if any(
                (max(self._volume_buckets[token], default=first_bucket) - first_bucket) >= 12
                for token, first_bucket in self._first_volume_bucket.items()
            ) else "WARMING",
        }
        return dict(self._state)

    def _waiting(self, now_ns: int, reason: str) -> dict[str, Any]:
        self._state = {
            "state": "WATCH", "direction": None, "score": 0,
            "volume_ratio": None, "premium_change_pct": None,
            "iv_change_points": None, "spot_change_pct": None,
            "contracts_confirming": 0, "updated_at_ns": now_ns,
            "reason": reason, "signals_allowed": False,
            "baseline_status": "WARMING",
        }
        return dict(self._state)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._state)


__all__ = ["IVEarlyMoveDetector"]

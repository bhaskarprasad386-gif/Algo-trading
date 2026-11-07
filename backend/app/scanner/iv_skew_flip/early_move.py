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
            mark = self._valid_number(getattr(quote, "mark", None))
            metric = metric_by_token.get(token, {})
            iv = self._valid_number(metric.get("iv"))
            if (not token or side not in {"CE", "PE"} or isinstance(ts, bool)
                    or not isinstance(ts, int) or ts <= 0 or ts > now_ns
                    or now_ns - ts > self.MAX_AGE_NS
                    or isinstance(volume, bool) or not isinstance(volume, int)
                    or volume < 0):
                continue
            previous = self._last.get(token)
            self._last[token] = _Observation(ts, volume, mark, iv)
            if previous is None or ts <= previous.timestamp_ns or volume < previous.volume:
                continue
            increment = volume - previous.volume
            bucket = ts // self.BUCKET_NS
            buckets = self._volume_buckets[token]
            buckets[bucket] = buckets.get(bucket, 0) + increment
            for old_bucket in tuple(buckets):
                if old_bucket * self.BUCKET_NS < cutoff:
                    del buckets[old_bucket]
            prior_buckets = [bucket - offset for offset in range(1, self.HISTORY_BUCKETS + 1)]
            prior_volumes = [buckets[key] for key in prior_buckets if key in buckets]
            baseline = sum(prior_volumes) / len(prior_volumes) if len(prior_volumes) >= 12 else 0.0
            current_volume = buckets.get(bucket, 0)
            ratio = current_volume / baseline if baseline > 0 else None
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
        if not summaries:
            return self._waiting(now_ns, "waiting for new volume, premium and IV ticks")

        ranked = sorted(summaries.items(), key=lambda item: (
            item[1]["contracts"], item[1]["volume_ratio"],
            item[1]["premium_change_pct"], item[1]["iv_change_points"]
        ), reverse=True)
        direction, values = ranked[0]
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
        if spot_confirmed:
            state = "CONFIRMED"
            reason = "underlying has moved at least 0.15% in the same direction"
        elif volume_confirm and premium_rise and iv_rise and spot_aligned and values["contracts"] >= 2:
            state = "PRE_MOVE"
            reason = "multi-contract volume acceleration with rising premium and IV, aligned with spot"
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
            "reason": reason,
            "signals_allowed": state in {"PRE_MOVE", "CONFIRMED"},
            "baseline_status": "READY" if any(
                len(buckets) >= 12 for buckets in self._volume_buckets.values()
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

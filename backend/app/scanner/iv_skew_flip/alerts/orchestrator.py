"""Alert-only orchestration for the active NIFTY Weekly IV sampler."""
from __future__ import annotations

from datetime import datetime, time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

from .aggregation import SourceMinuteAggregator
from .data_adapter import option_observation
from .conditions import evaluate_strategy_a, evaluate_strategy_b, even_strikes
from .models import AlertState, StrategyEvaluation

IST = ZoneInfo("Asia/Kolkata")


class IVEarlyEntryAlertEngine:
    """Consumes existing sampler data; never creates feeds or sends orders."""

    def __init__(self, store: Any, *, retention_minutes: int = 30) -> None:
        self.store = store
        self.aggregator = SourceMinuteAggregator(retention_minutes=retention_minutes)
        self._last_results: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _in_window(now: datetime, strategy: str) -> bool:
        local = now.astimezone(IST)
        if local.weekday() >= 5 or local.time() < clock_time(9, 30) or local.time() > clock_time(15, 5):
            return False
        if strategy == "IV_VOLUME_MOMENTUM":
            return (clock_time(9, 45) <= local.time() <= clock_time(11, 30)
                    or clock_time(13, 30) <= local.time() <= clock_time(15, 0))
        return clock_time(9, 30) <= local.time() <= clock_time(11, 30)

    def evaluate(self, *, sampler: Any, spot: Any, expiry: str, now: datetime, now_ns: int) -> dict[str, Any]:
        if now.tzinfo is None:
            raise ValueError("alert engine requires timezone-aware datetime")
        self.store.expire_session_alerts(now=now)
        live_spot_stamp = int(getattr(spot, "timestamp_ns", 0) or 0)
        live_spot_price = float(getattr(spot, "price", 0) or 0)
        if (0 < live_spot_stamp <= now_ns and now_ns-live_spot_stamp <= 5_000_000_000
                and live_spot_price > 0):
            self.store.invalidate_breached_oi_walls(
                spot=live_spot_price, observed_at_ns=now_ns,
            )
        if not expiry:
            return {"state": "WATCH", "reason": "WAITING_FOR_WEEKLY_EXPIRY", "signals_allowed": False}
        snapshot = sampler.snapshot()
        if snapshot.get("status") != "LIVE":
            return {"state": "WATCH", "reason": "SAMPLER_NOT_LIVE", "signals_allowed": False}
        samples = tuple(snapshot.get("samples", ()))
        if not samples:
            return {"state": "WATCH", "reason": "NO_LIVE_OPTION_SAMPLES", "signals_allowed": False}
        self.aggregator.ingest(samples, spot, now_ns=now_ns)
        bars = self.aggregator.spot_bars(now_ns=now_ns)
        token_rows = {
            token: self.aggregator.option_history(token, now_ns=now_ns)
            for token in self.aggregator.option_tokens()
        }
        current = {str(row.token): row for row in samples}
        by_strike: dict[float, dict[str, str]] = {}
        for token, row in current.items():
            if row.underlying.upper() != "NIFTY" or row.expiry != expiry:
                continue
            if row.option_type in {"CE", "PE"} and row.strike in even_strikes([row.strike]):
                by_strike.setdefault(float(row.strike), {})[row.option_type] = token

        candidates: list[Any] = []
        reasons: list[str] = []
        for strike, pair in by_strike.items():
            if "CE" not in pair or "PE" not in pair:
                continue
            ce = token_rows.get(pair["CE"], ())
            pe = token_rows.get(pair["PE"], ())
            for direction, target_type in (("BULLISH", "CE"), ("BEARISH", "PE")):
                strategy = "IV_VOLUME_MOMENTUM"
                if not self._in_window(now, strategy):
                    reasons.append("OUTSIDE_IV_VOLUME_WINDOW")
                    continue
                live_ce = option_observation(current[pair["CE"]])
                live_pe = option_observation(current[pair["PE"]])
                live_target = option_observation(current[pair[target_type]])
                result = evaluate_strategy_a(
                    now_ns=now_ns, expiry=expiry, bars=bars, ce=ce, pe=pe,
                    target=token_rows.get(pair[target_type], ()), direction=direction,
                    live_ce=live_ce, live_pe=live_pe, live_target=live_target,
                )
                if result.candidate is not None:
                    candidates.append(result.candidate)
                elif result.reasons:
                    reasons.extend(result.reasons)

        # Select the highest-OI persistent wall from completed minute observations.
        def wall_history(option_type: str):
            # A wall must be the highest-OI eligible contract on each of the
            # same three completed minute buckets, not merely the current
            # highest-OI contract with an old history.
            winners: dict[int, tuple[int, str]] = {}
            histories: dict[str, tuple[Any, ...]] = {}
            for token, history in token_rows.items():
                rows = tuple(row for row in history if row.expiry == expiry
                             and row.option_type == option_type
                             and row.strike in even_strikes([row.strike])
                             and row.oi is not None)
                histories[token] = rows
                for row in rows:
                    minute = row.source_timestamp_ns // 60_000_000_000
                    current_winner = winners.get(minute)
                    if current_winner is None or row.oi > current_winner[0]:
                        winners[minute] = (row.oi, token)
            minutes = sorted(winners)[-3:]
            if len(minutes) != 3 or any(b-a != 1 for a, b in zip(minutes, minutes[1:])):
                return ()
            tokens = {winners[minute][1] for minute in minutes}
            if len(tokens) != 1:
                return ()
            token = next(iter(tokens))
            by_minute = {row.source_timestamp_ns // 60_000_000_000: row for row in histories[token]}
            return tuple(by_minute[minute] for minute in minutes if minute in by_minute)
        ce_wall, pe_wall = wall_history("CE"), wall_history("PE")
        if self._in_window(now, "OI_WALL_REJECTION"):
            for direction in ("BULLISH", "BEARISH"):
                result = evaluate_strategy_b(
                    now_ns=now_ns, expiry=expiry, spot_bars=bars,
                    ce_wall_history=ce_wall, pe_wall_history=pe_wall,
                    direction=direction,
                )
                if result.candidate is not None:
                    candidates.append(result.candidate)
                elif result.reasons:
                    reasons.extend(result.reasons)
        else:
            reasons.append("OUTSIDE_OI_WALL_WINDOW")

        # Persist only valid EARLY_ENTRY transitions; store enforces durable cooldown.
        persisted = []
        conflicts = []
        conflict_records = []
        directions = {candidate.direction for candidate in candidates}
        if len(directions) > 1:
            conflicts = candidates
            for candidate in conflicts:
                conflict_records.append(self.store.record_conflict(candidate))
        else:
            for candidate in candidates:
                persisted.append(self.store.record(candidate))
        state = "CONFLICT" if conflicts else ("EARLY_ENTRY" if any(item.get("accepted") for item in persisted) else "WATCH")
        result = {
            "state": state, "underlying": "NIFTY", "expiry": expiry,
            "signals_allowed": state == "EARLY_ENTRY",
            "accepted_alerts": persisted,
            "conflict_records": conflict_records,
            "conflicts": [
                {"strategy": item.strategy, "strike": item.strike, "direction": item.direction,
                 "option_type": item.option_type, "reason": item.reason}
                for item in conflicts
            ],
            "reasons": sorted(set(reasons))[-20:],
            "completed_spot_bars": len(bars),
            "completed_option_contracts": len(token_rows),
            "evaluated_at_ns": now_ns,
        }
        self._last_results["NIFTY_WEEKLY"] = result
        return result

    def snapshot(self) -> dict[str, Any]:
        return dict(self._last_results.get("NIFTY_WEEKLY", {
            "state": "WATCH", "signals_allowed": False, "reason": "NOT_EVALUATED",
        }))


__all__ = ["IVEarlyEntryAlertEngine"]

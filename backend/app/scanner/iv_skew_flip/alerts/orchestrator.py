"""Alert-only orchestration for the active NIFTY Weekly IV sampler."""
from __future__ import annotations

from datetime import datetime
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

    def evaluate(self, *, sampler: Any, spot: Any, expiry: str, now: datetime, now_ns: int) -> dict[str, Any]:
        if now.tzinfo is None:
            raise ValueError("alert engine requires timezone-aware datetime")
        self.store.expire_session_alerts(now=now)
        live_spot_stamp = int(getattr(spot, "timestamp_ns", 0) or 0)
        live_spot_price = float(getattr(spot, "price", 0) or 0)
        if (0 < live_spot_stamp <= now_ns and live_spot_price > 0):
            self.store.invalidate_breached_oi_walls(
                spot=live_spot_price, observed_at_ns=now_ns,
            )
        if not expiry:
            return {"state": "WATCH", "reason": "WAITING_FOR_WEEKLY_EXPIRY", "signals_allowed": False}
        snapshot = sampler.snapshot()
        if snapshot.get("status") != "LIVE":
            return {"state": "WATCH", "reason": "SAMPLER_NOT_LIVE", "signals_allowed": False}
        # Never use display samples here: snapshot()["samples"] deliberately
        # carries forward last-good IV for UI continuity when current IV is None.
        # Only raw current-cycle samples may authorize an alert.
        samples = tuple(snapshot.get("live_samples", ()))
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

        # Select a persistent OI wall from three distinct source observations.
        # Observations may arrive at irregular intervals; timestamps are kept
        # for ordering/audit and are not forced into consecutive minute buckets.
        def wall_history(option_type: str):
            eligible: list[tuple[int, tuple[Any, ...]]] = []
            for token, history in token_rows.items():
                rows = tuple(row for row in history if row.expiry == expiry
                             and row.option_type == option_type
                             and row.strike in even_strikes([row.strike])
                             and row.oi is not None)
                if len(rows) >= 3:
                    recent = rows[-3:]
                    if len({row.strike for row in recent}) == 1:
                        eligible.append((int(recent[-1].oi or 0), recent))
            if not eligible:
                return ()
            return max(eligible, key=lambda item: item[0])[1]
        ce_wall, pe_wall = wall_history("CE"), wall_history("PE")
        for direction in ("BULLISH", "BEARISH"):
            wall_rows = pe_wall if direction == "BULLISH" else ce_wall
            wall_strike = wall_rows[-1].strike if wall_rows else None
            entry_type = "CE" if direction == "BULLISH" else "PE"
            entry_token = by_strike.get(float(wall_strike), {}).get(entry_type) if wall_strike is not None else None
            entry_quote = option_observation(current[entry_token]) if entry_token else None
            if entry_quote is None:
                reasons.append("WAITING_FOR_FRESH_ENTRY_LEG_QUOTE")
                continue
            result = evaluate_strategy_b(
                now_ns=now_ns, expiry=expiry, spot_bars=bars,
                ce_wall_history=ce_wall, pe_wall_history=pe_wall,
                entry_quote=entry_quote, direction=direction,
            )
            if result.candidate is not None:
                candidates.append(result.candidate)
            elif result.reasons:
                reasons.extend(result.reasons)
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

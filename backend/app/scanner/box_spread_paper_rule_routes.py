"""Authenticated custom-rule API and paper-only periodic position monitor."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.scanner.box_spread_paper_routes import require_paper_access
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry, BoxSpreadPaperEntryClaim,
)
from app.scanner.box_spread_paper_rules import (
    BoxSpreadPaperRule, BoxSpreadPaperRuleEvaluation, BoxSpreadPaperEntryQuotaReservation,
    decode_rule_config, validate_rule_config, utc_now, entry_matches, exit_matches,
    metric_value, select_entry_candidates,
)

router = APIRouter(
    prefix="/api/v1/scanner/box-spread-paper",
    tags=["box-spread-paper-rules"],
    dependencies=[Depends(require_paper_access)],
)


class PaperRulePayload(BaseModel):
    account_key: str = Field(default="default", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    config: dict[str, Any]


def _rule_or_404(session, rule_id: int, account_key: str):
    rule = session.get(BoxSpreadPaperRule, rule_id)
    if rule is None or rule.account_key != account_key:
        raise HTTPException(status_code=404, detail="paper rule not found")
    return rule


@router.get("/rules")
def get_rules(account_key: str = "default"):
    with SessionLocal() as session:
        rows = session.scalars(
            select(BoxSpreadPaperRule)
            .where(BoxSpreadPaperRule.account_key == account_key)
            .order_by(BoxSpreadPaperRule.id.desc())
        ).all()
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False, "data": [
            {"rule_id": row.id, "name": row.name, "enabled": row.enabled,
             "config": decode_rule_config(row), "created_at": row.created_at.isoformat(),
             "updated_at": row.updated_at.isoformat()} for row in rows
        ]}


@router.post("/rules")
def create_rule(payload: PaperRulePayload):
    try:
        config = validate_rule_config(payload.config)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = utc_now()
    with SessionLocal() as session:
        row = BoxSpreadPaperRule(
            account_key=payload.account_key, name=payload.name.strip(),
            enabled=payload.enabled, config_json=json.dumps(config, separators=(",", ":"), sort_keys=True),
            created_at=now, updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "rule_id": row.id, "name": row.name, "enabled": row.enabled, "config": config}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: PaperRulePayload):
    try:
        config = validate_rule_config(payload.config)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with SessionLocal() as session:
        row = _rule_or_404(session, rule_id, payload.account_key)
        row.name = payload.name.strip()
        row.enabled = payload.enabled
        row.config_json = json.dumps(config, separators=(",", ":"), sort_keys=True)
        row.updated_at = utc_now()
        session.commit()
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "rule_id": row.id, "name": row.name, "enabled": row.enabled, "config": config}


@router.get("/rules/{rule_id}/evaluations")
def rule_evaluations(rule_id: int, account_key: str = "default", limit: int = 100):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    with SessionLocal() as session:
        _rule_or_404(session, rule_id, account_key)
        rows = session.scalars(
            select(BoxSpreadPaperRuleEvaluation)
            .where(BoxSpreadPaperRuleEvaluation.rule_id == rule_id,
                   BoxSpreadPaperRuleEvaluation.account_key == account_key)
            .order_by(BoxSpreadPaperRuleEvaluation.evaluated_at.desc(),
                      BoxSpreadPaperRuleEvaluation.id.desc())
            .limit(limit)
        ).all()
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False, "data": [
            {"evaluation_id": row.id, "trade_id": row.trade_id, "event": row.event,
             "outcome": row.outcome, "metric": row.metric,
             "observed_value": row.observed_value, "threshold": row.threshold,
             "reason": row.reason, "evaluated_at": row.evaluated_at.isoformat()}
            for row in rows
        ]}


def _account_rules() -> list[tuple[int, str, bool, dict[str, Any]]]:
    with SessionLocal() as session:
        rows = session.scalars(select(BoxSpreadPaperRule)).all()
        return [(row.id, row.account_key, row.enabled, decode_rule_config(row)) for row in rows]


def _open_rule_trades(rule_id: int, account_key: str):
    prefix = f"rule-{rule_id}-%"
    with SessionLocal() as session:
        return session.scalars(
            select(BoxSpreadPaperTradeRecord)
            .where(BoxSpreadPaperTradeRecord.account_key == account_key,
                   BoxSpreadPaperTradeRecord.idempotency_key.like(prefix),
                   BoxSpreadPaperTradeRecord.status.notin_(("CREATED", "CLOSED", "CANCELLED")))
            .order_by(BoxSpreadPaperTradeRecord.created_at, BoxSpreadPaperTradeRecord.id)
        ).all()


def _entry_window_bounds_utc(window_minutes: int) -> tuple[datetime, datetime]:
    """Return the current fixed IST wall-clock entry window as UTC bounds."""
    local_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = local_now.hour * 60 + local_now.minute
    bucket_start = (elapsed_minutes // window_minutes) * window_minutes
    start_local = midnight + timedelta(minutes=bucket_start)
    end_local = start_local + timedelta(minutes=window_minutes)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _claimed_rule_entry_window_count(
    rule_id: int, account_key: str, start_utc: datetime, end_utc: datetime,
) -> int:
    """Count durable entry attempts claimed inside this rule's current IST window."""
    with SessionLocal() as session:
        claim_ids = session.scalars(
            select(BoxSpreadPaperEntryClaim.id)
            .join(BoxSpreadPaperTradeRecord, BoxSpreadPaperEntryClaim.trade_id == BoxSpreadPaperTradeRecord.id)
            .where(BoxSpreadPaperTradeRecord.account_key == account_key,
                   BoxSpreadPaperTradeRecord.idempotency_key.like(f"rule-{rule_id}-%"),
                   BoxSpreadPaperEntryClaim.account_key == account_key,
                   BoxSpreadPaperEntryClaim.claimed_at >= start_utc,
                   BoxSpreadPaperEntryClaim.claimed_at < end_utc)
        ).all()
    return len(claim_ids)


def _reserve_rule_entry_quota_slot(
    rule_id: int, account_key: str, window_start: datetime, window_end: datetime,
    max_per_window: int, candidate_key: str,
) -> int | None:
    """Atomically reserve a unique durable slot before a paper entry attempt.

    Unique database constraints serialize workers; reservations are never released,
    because a crash or ambiguous attempt must continue consuming the interval quota.
    """
    for slot in range(1, max_per_window + 1):
        try:
            with SessionLocal() as session, session.begin():
                reservation = BoxSpreadPaperEntryQuotaReservation(
                    account_key=account_key, rule_id=rule_id,
                    window_start_utc=window_start, window_end_utc=window_end,
                    slot=slot, candidate_key=candidate_key, reserved_at=utc_now(),
                )
                session.add(reservation)
                session.flush()
                return slot
        except IntegrityError:
            # A competing worker may have taken this slot, or this exact candidate
            # may already be reserved. The latter is a lifetime one-shot block.
            with SessionLocal() as session:
                existing_candidate = session.scalar(select(BoxSpreadPaperEntryQuotaReservation.id).where(
                    BoxSpreadPaperEntryQuotaReservation.account_key == account_key,
                    BoxSpreadPaperEntryQuotaReservation.rule_id == rule_id,
                    BoxSpreadPaperEntryQuotaReservation.candidate_key == candidate_key,
                ))
            if existing_candidate is not None:
                return None
    return None


def _claimed_rule_entry_keys(rule_id: int, account_key: str) -> set[str]:
    """Return durable one-shot entry attempts already consumed by this rule."""
    prefix = f"rule-{rule_id}-%"
    with SessionLocal() as session:
        keys = session.scalars(
            select(BoxSpreadPaperTradeRecord.idempotency_key)
            .join(BoxSpreadPaperEntryClaim, BoxSpreadPaperEntryClaim.trade_id == BoxSpreadPaperTradeRecord.id)
            .where(BoxSpreadPaperTradeRecord.account_key == account_key,
                   BoxSpreadPaperTradeRecord.idempotency_key.like(prefix),
                   BoxSpreadPaperEntryClaim.account_key == account_key)
        ).all()
    return set(keys)


def _signed_open_quantities(trade_id: int, account_key: str) -> dict[str, int]:
    with SessionLocal() as session:
        entries = session.scalars(
            select(BoxSpreadPaperLedgerEntry)
            .where(BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                   BoxSpreadPaperLedgerEntry.account_key == account_key)
            .order_by(BoxSpreadPaperLedgerEntry.created_at, BoxSpreadPaperLedgerEntry.id)
        ).all()
    signed: dict[str, int] = {}
    for entry in entries:
        signed[entry.role] = signed.get(entry.role, 0) + (
            entry.quantity if entry.action == "BUY" else -entry.quantity
        )
    return signed


def _record(rule_id: int, account_key: str, event: str, outcome: str, reason: str,
            trade_id: int | None = None, metric: str | None = None,
            observed: float | None = None, threshold: float | None = None):
    with SessionLocal() as session:
        session.add(BoxSpreadPaperRuleEvaluation(
            rule_id=rule_id, account_key=account_key, trade_id=trade_id,
            event=event, outcome=outcome, metric=metric,
            observed_value=None if observed is None else str(observed),
            threshold=None if threshold is None else str(threshold),
            reason=reason[:512], evaluated_at=utc_now(),
        ))
        session.flush()
        # Keep evaluation history bounded: retain the newest 1,000 rows per rule.
        stale_ids = session.scalars(
            select(BoxSpreadPaperRuleEvaluation.id)
            .where(BoxSpreadPaperRuleEvaluation.rule_id == rule_id)
            .order_by(BoxSpreadPaperRuleEvaluation.evaluated_at.desc(),
                      BoxSpreadPaperRuleEvaluation.id.desc())
            .offset(1000)
        ).all()
        if stale_ids:
            session.execute(delete(BoxSpreadPaperRuleEvaluation).where(
                BoxSpreadPaperRuleEvaluation.id.in_(stale_ids)
            ))
        session.commit()


async def _run_rule_cycle(rule_id: int, account_key: str, config: dict[str, Any], rule_enabled: bool):
    # Imports are delayed to keep the router and the paper route module acyclic.
    from app.scanner import box_spread_paper_routes as paper
    from app.scanner.live_box_spread_routes import monitor_snapshot as monitor_opportunities

    snapshot = await asyncio.to_thread(monitor_opportunities, max_age_seconds=5.0)
    if not isinstance(snapshot, dict):
        snapshot = {"fresh": False, "data": [], "entry_data": []}
    rows = snapshot.get("data", [])
    entry_rows = snapshot.get("entry_data", []) if isinstance(snapshot, dict) else []
    if not isinstance(rows, list):
        rows = []
    if not isinstance(entry_rows, list):
        entry_rows = []

    # Emit one durable monitor diagnostic for every cycle in which entry
    # scanning cannot proceed. This makes OFF/stale/no-candidate states visible
    # instead of silently returning from the background monitor.
    if not snapshot.get("fresh", False):
        _record(rule_id, account_key, "ENTRY_SCAN", "SKIPPED",
                "scanner_snapshot_stale_or_unavailable;fresh_four_leg_data_required")
    elif not rule_enabled:
        _record(rule_id, account_key, "ENTRY_SCAN", "DISABLED",
                f"algo_rule_disabled;scanner_candidates={len(entry_rows)}")
    else:
        with paper._paper_execution_state_lock:
            entry_switch_enabled = paper._paper_execution_enabled
        if not entry_switch_enabled:
            _record(rule_id, account_key, "ENTRY_SCAN", "BLOCKED",
                    f"paper_execution_switch_OFF;scanner_candidates={len(entry_rows)}")
        elif not entry_rows:
            _record(rule_id, account_key, "ENTRY_SCAN", "NO_CANDIDATES",
                    "fresh_snapshot_has_no_entry_candidates;check_scanner_eligibility_and_feed")

    # Exit monitoring is independent of the new-entry kill switch. Turning OFF
    # blocks new entries but must not strand already-open paper positions.
    for trade in await asyncio.to_thread(_open_rule_trades, rule_id, account_key):
        request = await asyncio.to_thread(paper._load_trade_request, trade.id, account_key)
        contracts = await asyncio.to_thread(paper._resolve, request)
        exit_config = config["exit"]
        if exit_config["hold_to_expiry"]:
            try:
                expiry_date = date.fromisoformat(paper._canonical_expiry(trade.expiry))
            except ValueError:
                expiry_date = None
            # Hold through the complete expiry date in IST. Settlement becomes
            # a manual reconciliation item only after that date has passed.
            if expiry_date is None or datetime.now(ZoneInfo("Asia/Kolkata")).date() <= expiry_date:
                _record(rule_id, account_key, "EXIT_CHECK", "HOLD_TO_EXPIRY",
                        "hold_to_expiry_rule_active", trade.id)
                continue
            # Do not invent settlement prices or settle from client data. Keep the
            # expired position visible for trusted settlement/manual reconciliation.
            _record(rule_id, account_key, "EXIT_CHECK", "MANUAL_SETTLEMENT_REQUIRED",
                    "expiry_reached_but_verified_server_settlement_source_is_not_configured",
                    trade.id)
            continue

        live_row = paper._matching_live_row(contracts, snapshot=snapshot)
        if live_row is None:
            _record(rule_id, account_key, "EXIT_CHECK", "SKIPPED",
                    "matching_fresh_four_leg_snapshot_unavailable", trade.id)
            continue

        roles = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
        signed = None
        observed_override = None
        if exit_config["metric"] == "position_net_pnl":
            signed = await asyncio.to_thread(_signed_open_quantities, trade.id, account_key)
            quantities_for_pnl = {role: abs(int(signed.get(role, 0))) for role in roles}
            if any(quantity <= 0 for quantity in quantities_for_pnl.values()) or len(set(quantities_for_pnl.values())) != 1:
                _record(rule_id, account_key, "EXIT_CHECK", "SKIPPED",
                        "position_net_pnl_unavailable_for_incomplete_or_unequal_four_leg_exposure",
                        trade.id, exit_config["metric"], None, exit_config["threshold"])
                continue
            close_marks = {
                role: paper._exit_reference_price(live_row, contracts.direction, role)
                for role in roles
            }
            pnl_summary = await asyncio.to_thread(
                paper._ledger.get_trade_summary,
                trade.id, account_key=account_key, mark_prices=close_marks,
            )
            observed_override = float(
                Decimal(pnl_summary["realized_pnl"]) +
                Decimal(pnl_summary["unrealized_pnl"] or 0)
            )

        matched, reason, observed = exit_matches(
            live_row, config, metric_override=observed_override,
        )
        if not matched:
            _record(rule_id, account_key, "EXIT_CHECK", "NO_ACTION", reason,
                    trade.id, exit_config["metric"], observed, exit_config["threshold"])
            continue

        if signed is None:
            signed = await asyncio.to_thread(_signed_open_quantities, trade.id, account_key)
        open_quantities = {role: abs(int(signed.get(role, 0))) for role in roles}
        if any(quantity <= 0 for quantity in open_quantities.values()) or len(set(open_quantities.values())) != 1:
            _record(rule_id, account_key, "EXIT_CHECK", "SKIPPED",
                    "four_leg_open_quantities_not_equal_or_missing; position left unchanged",
                    trade.id, exit_config["metric"], observed, exit_config["threshold"])
            continue

        basket_legs = {
            role: {
                "quantity": open_quantities[role],
                "fill_price": paper._exit_reference_price(live_row, contracts.direction, role),
            }
            for role in roles
        }
        await asyncio.to_thread(
            paper._ledger.record_exit_basket,
            trade_id=trade.id,
            fill_id=f"rule-exit-{rule_id}-{trade.id}-{uuid.uuid4().hex}",
            legs=basket_legs,
            account_key=account_key,
        )
        _record(rule_id, account_key, "EXIT_CHECK", "EXIT_RECORDED",
                f"{reason}; {exit_config['order_type']} four-leg paper basket",
                trade.id, exit_config["metric"], observed, exit_config["threshold"])

    # Entry remains subject to the global paper execution switch and all existing
    # five-second four-leg quote, idempotency, claim and ledger protections.
    with paper._paper_execution_state_lock:
        execution_enabled = paper._paper_execution_enabled
    if not rule_enabled or not execution_enabled or not snapshot.get("fresh", False):
        return

    open_count = len(await asyncio.to_thread(_open_rule_trades, rule_id, account_key))
    max_open_positions = config["entry"]["max_open_positions"]
    if open_count >= max_open_positions:
        _record(rule_id, account_key, "ENTRY_SCAN", "BLOCKED",
                f"max_open_positions_reached;open={open_count};maximum={max_open_positions}")
        return

    if config["entry"].get("range_enabled", False):
        # The quota resets at each fixed IST entry window. Durable claims count
        # failed/ambiguous attempts too, while lifetime idempotency still blocks
        # replaying an identical candidate in a later window.
        claimed_keys = await asyncio.to_thread(_claimed_rule_entry_keys, rule_id, account_key)
        window_minutes = config["monitor"]["entry_window_minutes"]
        window_start, window_end = _entry_window_bounds_utc(window_minutes)
        max_per_window = config["entry"]["max_trades_per_interval"]
        unclaimed_rows = []
        previously_claimed_rows = []
        for candidate in entry_rows:
            expiry_value = candidate.get("expiry_date") or candidate.get("expiry")
            try:
                candidate_key = "rule-{}-{}-{}-{}-{}-{}".format(
                    rule_id, str(candidate.get("symbol", "")).upper(),
                    paper._canonical_expiry(expiry_value), candidate.get("low_strike"),
                    candidate.get("high_strike"), str(candidate.get("direction", "")).upper(),
                )
            except (TypeError, ValueError):
                unclaimed_rows.append(candidate)
                continue
            (previously_claimed_rows if candidate_key in claimed_keys else unclaimed_rows).append(candidate)
        # Rank all eligible candidates; the DB reservation below decides which
        # workers actually own the remaining slots, avoiding stale count snapshots.
        selected_entry_rows, skipped_rows = select_entry_candidates(
            unclaimed_rows, config, range_limit=len(unclaimed_rows),
        )
        skipped_rows.extend((candidate, "candidate_already_attempted") for candidate in previously_claimed_rows)
    else:
        selected_entry_rows, skipped_rows = select_entry_candidates(entry_rows, config)
    skipped_count = len(skipped_rows)
    skipped_reasons: dict[str, int] = {}
    evaluated_count = len(entry_rows)
    for skipped_row, reason in skipped_rows:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        if sum(skipped_reasons.values()) <= 20:
            observed = metric_value(skipped_row, config["entry"]["metric"])
            _record(rule_id, account_key, "ENTRY_SCAN", "SKIPPED",
                    f"candidate={str(skipped_row.get('symbol','')).upper()} "
                    f"expiry={skipped_row.get('expiry_date') or skipped_row.get('expiry')} "
                    f"direction={skipped_row.get('direction')};reason={reason}",
                    metric=config["entry"]["metric"], observed=observed,
                    threshold=config["entry"]["range_min"] if config["entry"].get("range_enabled") else config["entry"]["threshold"])

    for row in selected_entry_rows:
        if open_count >= max_open_positions:
            break
        expiry = row.get("expiry_date") or row.get("expiry")
        candidate_audit = json.dumps({
            "symbol": str(row.get("symbol", "")).upper(),
            "expiry": str(expiry),
            "low_strike": row.get("low_strike"),
            "high_strike": row.get("high_strike"),
            "direction": str(row.get("direction", "")).upper(),
            "lots": config["entry"]["lots"],
            "metric": config["entry"]["metric"],
            "observed": metric_value(row, config["entry"]["metric"]),
            "range_min": config["entry"].get("range_min"),
            "range_max": config["entry"].get("range_max"),
        }, separators=(",", ":"), default=str)
        trade = None
        entry_started = time.perf_counter()
        try:
            idempotency_key = "rule-{}-{}-{}-{}-{}-{}".format(
                rule_id, str(row.get("symbol", "")).upper(), paper._canonical_expiry(expiry),
                row.get("low_strike"), row.get("high_strike"), str(row.get("direction", "")).upper(),
            )
            quota_slot = await asyncio.to_thread(
                _reserve_rule_entry_quota_slot, rule_id, account_key, window_start, window_end,
                max_per_window, idempotency_key,
            )
            if quota_slot is None:
                _record(rule_id, account_key, "ENTRY_CHECK", "SKIPPED",
                        f"{candidate_audit};interval_quota_full_or_candidate_already_reserved")
                continue
            request = paper.CreatePaperTrade(
                underlying=str(row["symbol"]), instrument_class=str(row["instrument_class"]),
                expiry=str(expiry), low_strike=float(row["low_strike"]),
                high_strike=float(row["high_strike"]), direction=str(row["direction"]).upper(),
                lots=config["entry"]["lots"], account_key=account_key,
                idempotency_key=idempotency_key,
                entry_spot_price=row.get("spot_price"),
            )
            contracts = await asyncio.to_thread(paper._resolve, request)
            # Validate all four executable prices before creating a durable trade.
            paper._reference_prices_for_direction(row, contracts.direction)
            trade = await asyncio.to_thread(
                paper._ledger.create_trade,
                idempotency_key=idempotency_key, underlying=contracts.underlying,
                instrument_class=contracts.instrument_class, expiry=contracts.expiry,
                low_strike=contracts.low_strike, high_strike=contracts.high_strike,
                direction=contracts.direction, lots=request.lots, lot_size=contracts.lot_size,
                account_key=account_key, entry_spot_price=request.entry_spot_price,
            )
            if trade.status != "CREATED":
                continue
            open_count += 1
            existing_claim = await asyncio.to_thread(
                paper._ledger.get_entry_claim, trade_id=trade.id, account_key=account_key
            )
            existing_fills = await asyncio.to_thread(
                paper._ledger.list_fills, trade.id, account_key=account_key
            )
            if existing_claim is not None or existing_fills:
                continue
            result = await paper._simulate_entry_locked(
                trade.id, paper.SimulateEntry(account_key=account_key),
            )
            leg_audit = [
                {
                    "role": fill.get("role"), "action": fill.get("action"),
                    "quote_side": "ASK" if fill.get("action") == "BUY" else "BID",
                    "qty": fill.get("quantity"), "reference": fill.get("reference_price"),
                    "limit": fill.get("reference_price"), "fill": fill.get("fill_price"),
                }
                for fill in result.get("fills", [])
            ]
            execution_audit = json.dumps({
                "candidate": json.loads(candidate_audit),
                "order_type": "LIMIT",
                "elapsed_s": round(time.perf_counter() - entry_started, 4),
                "engine_latency_s": result.get("latency_seconds"),
                "legs": leg_audit,
            }, separators=(",", ":"), default=str)
            _record(rule_id, account_key, "ENTRY_CHECK", "ENTRY_RECORDED",
                    execution_audit, trade.id,
                    config["entry"]["metric"], metric_value(row, config["entry"]["metric"]),
                    config["entry"]["range_min"] if config["entry"].get("range_enabled") else config["entry"]["threshold"])
        except HTTPException as exc:
            _record(rule_id, account_key, "ENTRY_CHECK", "BLOCKED",
                    f"{candidate_audit};paper_entry_blocked:{exc.status_code}:{exc.detail}",
                    trade.id if trade is not None else None,
                    config["entry"]["metric"], metric_value(row, config["entry"]["metric"]),
                    config["entry"]["range_min"] if config["entry"].get("range_enabled") else config["entry"]["threshold"])
        except Exception as exc:
            _record(rule_id, account_key, "ENTRY_CHECK", "ERROR",
                    f"{candidate_audit};paper_entry_error:{type(exc).__name__}:{exc}",
                    trade.id if trade is not None else None,
                    config["entry"]["metric"], metric_value(row, config["entry"]["metric"]),
                    config["entry"]["range_min"] if config["entry"].get("range_enabled") else config["entry"]["threshold"])

    if skipped_count > 20:
        _record(rule_id, account_key, "ENTRY_SCAN", "SUMMARY",
                f"evaluated={evaluated_count};skipped={skipped_count};"
                f"remaining_skips_aggregated={max(0, skipped_count - 20)};reasons={json.dumps(skipped_reasons, sort_keys=True)}")


async def paper_rule_monitor_loop():
    """Poll each enabled rule at its configured fixed interval; safe across restarts."""
    last_run: dict[int, float] = {}
    while True:
        try:
            rules = await asyncio.to_thread(_account_rules)
            now = time.monotonic()
            for rule_id, account_key, enabled, config in rules:
                if not enabled and not await asyncio.to_thread(_open_rule_trades, rule_id, account_key):
                    continue
                interval = config["monitor"]["interval_seconds"]
                if now - last_run.get(rule_id, 0.0) < interval:
                    continue
                last_run[rule_id] = now
                try:
                    await _run_rule_cycle(rule_id, account_key, config, enabled)
                except Exception as exc:
                    _record(rule_id, account_key, "MONITOR", "ERROR",
                            f"monitor_cycle_error:{type(exc).__name__}:{exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient DB/feed issue must not kill the long-running monitor.
            pass
        await asyncio.sleep(1.0)


__all__ = ["router", "paper_rule_monitor_loop"]

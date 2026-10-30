"""Durable custom rule definitions and deterministic Box Spread rule evaluation.

The rule engine is paper-only. It evaluates backend scanner snapshots and never
calls a broker adapter. Entry/exit execution is delegated to the paper ledger.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, SessionLocal


ENTRY_METRICS = frozenset({"executable_edge", "edge_per_lot", "gross_pnl"})
EXIT_METRICS = frozenset({"executable_edge", "edge_per_lot", "gross_pnl"})
OPERATORS = frozenset({"<", "<=", "==", ">=", ">"})
DIRECTIONS = frozenset({"ALL", "LONG", "SHORT"})
INSTRUMENT_CLASSES = frozenset({"ALL", "INDEX", "STOCK"})
EXPIRY_TYPES = frozenset({"ALL", "WEEKLY", "MONTHLY", "UNKNOWN"})


class BoxSpreadPaperRule(Base):
    __tablename__ = "box_spread_paper_rules"
    __table_args__ = (Index("ix_box_paper_rules_account_enabled", "account_key", "enabled"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BoxSpreadPaperRuleEvaluation(Base):
    __tablename__ = "box_spread_paper_rule_evaluations"
    __table_args__ = (
        Index("ix_box_paper_rule_eval_rule_time", "rule_id", "evaluated_at"),
        Index("ix_box_paper_rule_eval_account_time", "account_key", "evaluated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=False)
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threshold: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_rule_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize and strictly validate the supported custom rule DSL."""
    if not isinstance(config, dict):
        raise ValueError("rule config must be an object")
    entry = config.get("entry", {})
    exit_rule = config.get("exit", {})
    monitor = config.get("monitor", {})
    if not all(isinstance(item, dict) for item in (entry, exit_rule, monitor)):
        raise ValueError("entry, exit and monitor must be objects")

    direction = str(entry.get("direction", "ALL")).upper()
    instrument_class = str(entry.get("instrument_class", "ALL")).upper()
    expiry_type = str(entry.get("expiry_type", "ALL")).upper()
    if direction not in DIRECTIONS:
        raise ValueError("entry.direction must be ALL, LONG or SHORT")
    if instrument_class not in INSTRUMENT_CLASSES:
        raise ValueError("entry.instrument_class must be ALL, INDEX or STOCK")
    if expiry_type not in EXPIRY_TYPES:
        raise ValueError("entry.expiry_type must be ALL, WEEKLY, MONTHLY or UNKNOWN")

    symbols = entry.get("symbols", [])
    if not isinstance(symbols, list) or len(symbols) > 100:
        raise ValueError("entry.symbols must be a list of at most 100 symbols")
    symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if any(len(symbol) > 64 for symbol in symbols):
        raise ValueError("entry symbols must be at most 64 characters")

    entry_metric = str(entry.get("metric", "executable_edge"))
    entry_operator = str(entry.get("operator", ">="))
    if entry_metric not in ENTRY_METRICS or entry_operator not in OPERATORS:
        raise ValueError("unsupported entry metric or operator")
    entry_threshold = float(entry.get("threshold", 0))
    if not math.isfinite(entry_threshold):
        raise ValueError("entry threshold must be finite")

    exit_metric = str(exit_rule.get("metric", "executable_edge"))
    exit_operator = str(exit_rule.get("operator", "<="))
    exit_threshold = float(exit_rule.get("threshold", 1))
    if exit_metric not in EXIT_METRICS or exit_operator not in OPERATORS:
        raise ValueError("unsupported exit metric or operator")
    if not math.isfinite(exit_threshold):
        raise ValueError("exit threshold must be finite")
    hold_raw = exit_rule.get("hold_to_expiry", False)
    if not isinstance(hold_raw, bool):
        raise ValueError("exit.hold_to_expiry must be a boolean")
    hold_to_expiry = hold_raw
    if not hold_to_expiry and "threshold" not in exit_rule:
        raise ValueError("exit.threshold is required unless hold_to_expiry is enabled")

    interval_raw = monitor.get("interval_seconds", 20)
    if isinstance(interval_raw, bool) or not isinstance(interval_raw, (int, float)) or not float(interval_raw).is_integer():
        raise ValueError("monitor.interval_seconds must be an integer")
    interval = int(interval_raw)
    if interval < 5 or interval > 3600:
        raise ValueError("monitor.interval_seconds must be between 5 and 3600 seconds")

    lots_raw = entry.get("lots", 1)
    if isinstance(lots_raw, bool) or not isinstance(lots_raw, (int, float)) or not float(lots_raw).is_integer():
        raise ValueError("entry.lots must be an integer")
    lots = int(lots_raw)
    if lots < 1 or lots > 100:
        raise ValueError("entry.lots must be between 1 and 100")
    max_open_raw = entry.get("max_open_positions", 1)
    if isinstance(max_open_raw, bool) or not isinstance(max_open_raw, (int, float)) or not float(max_open_raw).is_integer():
        raise ValueError("entry.max_open_positions must be an integer")
    max_open_positions = int(max_open_raw)
    if max_open_positions < 1 or max_open_positions > 20:
        raise ValueError("entry.max_open_positions must be between 1 and 20")

    return {
        "entry": {
            "symbols": symbols,
            "direction": direction,
            "instrument_class": instrument_class,
            "expiry_type": expiry_type,
            "metric": entry_metric,
            "operator": entry_operator,
            "threshold": entry_threshold,
            "lots": lots,
            "max_open_positions": max_open_positions,
        },
        "exit": {
            "metric": exit_metric,
            "operator": exit_operator,
            "threshold": exit_threshold,
            "hold_to_expiry": hold_to_expiry,
        },
        "monitor": {"interval_seconds": interval},
    }


def decode_rule_config(rule: BoxSpreadPaperRule) -> dict[str, Any]:
    return validate_rule_config(json.loads(rule.config_json))


def metric_value(row: dict[str, Any], metric: str) -> float | None:
    try:
        value = float(row.get(metric))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def compare(value: float, operator: str, threshold: float) -> bool:
    return {
        "<": value < threshold,
        "<=": value <= threshold,
        "==": math.isclose(value, threshold, rel_tol=0.0, abs_tol=1e-9),
        ">=": value >= threshold,
        ">": value > threshold,
    }[operator]


def entry_matches(row: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    entry = config["entry"]
    symbol = str(row.get("symbol", "")).strip().upper()
    if entry["symbols"] and symbol not in entry["symbols"]:
        return False, "symbol_not_selected"
    if entry["direction"] != "ALL" and str(row.get("direction", "")).upper() != entry["direction"]:
        return False, "direction_mismatch"
    if entry["instrument_class"] != "ALL" and str(row.get("instrument_class", "")).upper() != entry["instrument_class"]:
        return False, "instrument_class_mismatch"
    if entry["expiry_type"] != "ALL" and str(row.get("expiry_type", "UNKNOWN")).upper() != entry["expiry_type"]:
        return False, "expiry_type_mismatch"
    value = metric_value(row, entry["metric"])
    if value is None:
        return False, "entry_metric_unavailable"
    return compare(value, entry["operator"], entry["threshold"]), (
        "entry_conditions_met" if compare(value, entry["operator"], entry["threshold"])
        else "entry_threshold_not_met"
    )


def exit_matches(row: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str, float | None]:
    exit_rule = config["exit"]
    value = metric_value(row, exit_rule["metric"])
    if value is None:
        return False, "exit_metric_unavailable", None
    matched = compare(value, exit_rule["operator"], exit_rule["threshold"])
    return matched, ("exit_threshold_met" if matched else "exit_threshold_not_met"), value


def list_rules(account_key: str) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(BoxSpreadPaperRule)
            .where(BoxSpreadPaperRule.account_key == account_key)
            .order_by(BoxSpreadPaperRule.id.desc())
        ).all()
        return [{
            "rule_id": row.id, "account_key": row.account_key, "name": row.name,
            "enabled": row.enabled, "config": decode_rule_config(row),
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        } for row in rows]


def record_evaluation(
    *, rule_id: int, account_key: str, event: str, outcome: str, reason: str,
    trade_id: int | None = None, metric: str | None = None,
    observed_value: float | None = None, threshold: float | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(BoxSpreadPaperRuleEvaluation(
            rule_id=rule_id, account_key=account_key, trade_id=trade_id,
            event=event, outcome=outcome, metric=metric,
            observed_value=None if observed_value is None else str(observed_value),
            threshold=None if threshold is None else str(threshold),
            reason=reason[:512], evaluated_at=utc_now(),
        ))
        session.commit()

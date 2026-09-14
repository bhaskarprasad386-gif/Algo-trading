"""Controlled continuation of durable Cash-Future strategy runs."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable, Mapping

from app.backtesting.cash_future_strategy_resume import load_validated_cash_future_checkpoint
from app.backtesting.cash_future_strategy_runner import (
    CashFutureCapitalLedger,
    CashFutureStrategy,
    CashFutureStrategyConfig,
    CashFutureStrategyRun,
    _LedgerPayloadSequence,
    _point_date,
    _trade_gross_profit,
    _write_checkpoint,
)
from app.scanner.cash_future_history import CashFutureHistoryPoint


def resume_cash_future_strategy(
    points: Iterable[CashFutureHistoryPoint],
    strategy: CashFutureStrategy,
    *,
    ledger,
    run_id: str,
    strategy_id: str,
    strategy_version: str = "1",
    config: CashFutureStrategyConfig | None = None,
    strategy_hash: str | None = None,
    data_source_fingerprint: str | None = None,
) -> CashFutureStrategyRun:
    """Continue a durable Cash-Future strategy run strictly after its checkpoint."""
    config = config or CashFutureStrategyConfig()
    checkpoint_row = ledger.load_checkpoint(run_id)
    checkpoint = load_validated_cash_future_checkpoint(
        ledger,
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        data_source_fingerprint=data_source_fingerprint,
    )
    event_index = checkpoint_row.event_index if checkpoint_row is not None else 0

    restore = getattr(strategy, "restore_checkpoint_state", None)
    has_strategy_state = checkpoint.strategy_state is not None
    if has_strategy_state and restore is None:
        raise ValueError(
            "unsafe Cash-Future resume: checkpoint contains strategy state but strategy cannot restore it"
        )
    if restore is not None:
        if checkpoint.strategy_state is None:
            raise ValueError("unsafe Cash-Future resume: strategy state is unavailable")
        try:
            restore(dict(checkpoint.strategy_state))
        except Exception as exc:
            raise ValueError("unsafe Cash-Future resume: strategy state restoration failed") from exc

    metadata = ledger.run_metadata(run_id) or {}
    initial_capital = float(metadata.get("initial_capital", config.initial_capital))
    if not isfinite(initial_capital) or initial_capital < 0:
        raise ValueError("unsafe Cash-Future resume: stored initial_capital is invalid")
    if config.initial_capital != initial_capital:
        raise ValueError(
            "unsafe Cash-Future resume: initial_capital mismatch "
            f"(stored={initial_capital!r}, requested={config.initial_capital!r})"
        )

    selected_contract = checkpoint.selected_contract or config.contract_month
    if selected_contract is None:
        raise ValueError("unsafe Cash-Future resume: checkpoint has no selected contract")

    capital_ledger = CashFutureCapitalLedger(
        initial_capital,
        realized_capital=checkpoint.realized_capital,
        reserved_margin=checkpoint.reserved_margin,
        blocked_entries=checkpoint.blocked_entries,
    )
    entry = _deserialize_entry(checkpoint.open_entry, selected_contract=selected_contract)
    history: list[CashFutureHistoryPoint] | deque[CashFutureHistoryPoint]
    history = [] if config.history_window is None else deque(maxlen=config.history_window)

    previous_timestamp: datetime | date | None = None
    seen_events = 0
    resumed = False
    last_processed_point: CashFutureHistoryPoint | None = None
    last_processed_event_index = event_index

    for point in points:
        point_date = _point_date(point)
        if previous_timestamp is not None and point.timestamp < previous_timestamp:
            raise ValueError("Cash-Future strategy input must be ordered by timestamp")
        previous_timestamp = point.timestamp
        if config.end_date is not None and point_date > config.end_date:
            break
        if point.contract_month != selected_contract:
            continue

        seen_events += 1
        history.append(point)
        if seen_events <= event_index:
            continue
        resumed = True
        last_processed_point = point
        last_processed_event_index = seen_events

        visible_history = tuple(history)
        raw_signal = strategy(point, visible_history)
        if config.start_date is not None and point_date < config.start_date:
            _maybe_checkpoint(ledger, config, run_id, seen_events, point, selected_contract,
                              capital_ledger, entry, strategy, strategy_id, strategy_version,
                              strategy_hash, data_source_fingerprint)
            continue

        action = "NONE" if raw_signal is None else str(raw_signal).upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("Cash-Future strategy must return BUY, SELL, HOLD, or NONE")

        signal_record: dict[str, Any] = {
            "timestamp": point.timestamp.isoformat(), "symbol": point.symbol,
            "contract_month": point.contract_month, "action": action,
            "cash_price": point.cash_price, "future_price": point.future_price,
            "gap": point.gap, "lot_size": point.lot_size,
        }
        expiry_day = point.expiry_date is not None and point_date >= point.expiry_date
        if action == "BUY" and entry is None:
            if expiry_day:
                signal_record.update({"execution_status": "blocked", "blocked_reason": "expiry_day_new_entry"})
            elif capital_ledger.reserve(point.margin_required):
                entry = point
            else:
                signal_record.update({
                    "execution_status": "blocked",
                    "blocked_reason": "insufficient_available_capital",
                    "required_margin": max(float(point.margin_required), 0.0),
                    "available_capital": capital_ledger.available_capital,
                })
        elif action == "BUY" and entry is not None:
            capital_ledger.blocked_entries += 1
            signal_record.update({"execution_status": "blocked", "blocked_reason": "position_already_open"})

        exit_reason: str | None = None
        if action == "SELL" and entry is not None:
            exit_reason = "strategy"
        elif entry is not None and expiry_day:
            exit_reason = "expiry"

        from app.backtesting.ledger import LedgerRecord
        ledger.append(LedgerRecord(run_id, "signal", int(point.timestamp.timestamp() * 1_000_000_000), signal_record))

        if exit_reason is not None and entry is not None:
            gross = _trade_gross_profit(entry, point, config)
            net = gross - config.charges_per_trade - config.funding_cost_per_trade
            capital_ledger.apply_realized_pnl(net)
            capital_ledger.release(entry.margin_required)
            ledger.append(LedgerRecord(run_id, "trade", int(point.timestamp.timestamp() * 1_000_000_000), {
                "entry_time": entry.timestamp.isoformat(),
                "exit_time": point.timestamp.isoformat(),
                "symbol": entry.symbol,
                "contract_month": entry.contract_month,
                "lot_size": entry.lot_size,
                "quantity": entry.lot_size,
                "entry_cash_price": entry.cash_price,
                "entry_future_price": entry.future_price,
                "entry_gap": entry.gap,
                "exit_cash_price": point.cash_price,
                "exit_future_price": point.future_price,
                "exit_gap": point.gap,
                "gross_profit": gross,
                "charges": config.charges_per_trade,
                "funding_cost": config.funding_cost_per_trade,
                "net_profit": net,
                "execution_model": config.execution_model,
                "cash_side": config.cash_side,
                "future_side": config.future_side,
                "slippage_per_share": config.slippage_per_share,
                "exit_reason": exit_reason,
                "reserved_margin": entry.margin_required,
            }))
            entry = None

        unrealized = 0.0
        if entry is not None:
            try:
                unrealized = _trade_gross_profit(entry, point, config)
            except ValueError:
                unrealized = 0.0
        ledger.append(LedgerRecord(run_id, "equity", int(point.timestamp.timestamp() * 1_000_000_000), {
            "timestamp": point.timestamp.isoformat(),
            "equity": float(capital_ledger.realized_capital) + unrealized,
            "realized_capital": float(capital_ledger.realized_capital),
            "unrealized_pnl": unrealized,
            "available_capital": capital_ledger.available_capital,
            "reserved_margin": capital_ledger.reserved_margin,
        }))
        _maybe_checkpoint(ledger, config, run_id, seen_events, point, selected_contract,
                          capital_ledger, entry, strategy, strategy_id, strategy_version,
                          strategy_hash, data_source_fingerprint)

    if not resumed:
        raise ValueError(
            "Cash-Future resume source does not contain observations after checkpoint "
            f"event_index={event_index}"
        )

    if config.checkpoint_interval is not None and last_processed_point is not None:
        _write_checkpoint(
            ledger, run_id, last_processed_event_index, last_processed_point,
            selected_contract, capital_ledger, entry, strategy, strategy_id,
            strategy_version, strategy_hash, data_source_fingerprint,
        )

    return CashFutureStrategyRun(
        strategy_id, strategy_version, initial_capital, float(capital_ledger.realized_capital),
        float(capital_ledger.realized_capital) - initial_capital,
        _LedgerPayloadSequence(ledger, run_id, "signal"),
        _LedgerPayloadSequence(ledger, run_id, "trade"),
        _LedgerPayloadSequence(ledger, run_id, "equity"),
        capital_ledger.available_capital, capital_ledger.reserved_margin, capital_ledger.blocked_entries,
    )


def _maybe_checkpoint(ledger, config, run_id, event_index, point, selected_contract,
                      capital_ledger, entry, strategy, strategy_id, strategy_version,
                      strategy_hash, data_source_fingerprint):
    if config.checkpoint_interval is None or event_index % config.checkpoint_interval != 0:
        return
    _write_checkpoint(ledger, run_id, event_index, point, selected_contract, capital_ledger, entry,
                      strategy, strategy_id, strategy_version, strategy_hash, data_source_fingerprint)


def _deserialize_entry(payload: Mapping[str, Any] | None, *, selected_contract: str) -> CashFutureHistoryPoint | None:
    if payload is None:
        return None
    required = ("timestamp", "symbol", "contract_month", "cash_price", "future_price",
                "gap", "gap_pct", "lot_size", "margin_required", "expiry_date")
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"unsafe Cash-Future resume: open_entry missing fields: {missing}")
    try:
        timestamp = datetime.fromisoformat(str(payload["timestamp"]))
        expiry_raw = payload.get("expiry_date")
        expiry = date.fromisoformat(str(expiry_raw)) if expiry_raw else None
        values = {
            "cash_price": float(payload["cash_price"]), "future_price": float(payload["future_price"]),
            "gap": float(payload["gap"]), "gap_pct": float(payload["gap_pct"]),
            "margin_required": float(payload["margin_required"]),
            "volume": float(payload["volume"]) if payload.get("volume") is not None else None,
            "oi": float(payload["oi"]) if payload.get("oi") is not None else None,
            "cash_bid": float(payload["cash_bid"]) if payload.get("cash_bid") is not None else None,
            "cash_ask": float(payload["cash_ask"]) if payload.get("cash_ask") is not None else None,
            "future_bid": float(payload["future_bid"]) if payload.get("future_bid") is not None else None,
            "future_ask": float(payload["future_ask"]) if payload.get("future_ask") is not None else None,
            "cash_bid_qty": float(payload["cash_bid_qty"]) if payload.get("cash_bid_qty") is not None else None,
            "cash_ask_qty": float(payload["cash_ask_qty"]) if payload.get("cash_ask_qty") is not None else None,
            "future_bid_qty": float(payload["future_bid_qty"]) if payload.get("future_bid_qty") is not None else None,
            "future_ask_qty": float(payload["future_ask_qty"]) if payload.get("future_ask_qty") is not None else None,
            "charges": float(payload.get("charges", 0.0)),
            "funding_cost": float(payload.get("funding_cost", 0.0)),
            "net_profit": float(payload.get("net_profit", 0.0)),
            "roi_pct": float(payload.get("roi_pct", 0.0)),
        }
        lot_size = int(payload["lot_size"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("unsafe Cash-Future resume: open_entry contains invalid values") from exc
    if str(payload["contract_month"]) != selected_contract:
        raise ValueError("unsafe Cash-Future resume: open_entry contract does not match selected contract")
    numeric_values = [value for value in values.values() if value is not None]
    if not all(isfinite(value) for value in numeric_values) or values["margin_required"] < 0 or lot_size <= 0:
        raise ValueError("unsafe Cash-Future resume: open_entry contains non-finite or invalid values")
    return CashFutureHistoryPoint(timestamp=timestamp, symbol=str(payload["symbol"]),
        contract_month=str(payload["contract_month"]), lot_size=lot_size, expiry_date=expiry,
        **values)


__all__ = ["resume_cash_future_strategy"]

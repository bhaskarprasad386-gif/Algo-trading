"""Build isolated runtime baskets only from current, fully verified registry records."""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from app.scanner.iv_skew_flip.basket_registry import summarize_basket_registry
from app.scanner.iv_skew_flip.equity_feed import WeightedEquity


def load_verified_runtime_baskets(records, *, as_of: date, instrument_master: Any) -> dict[str, tuple[WeightedEquity, ...]]:
    """Return NIFTY and BANKNIFTY baskets or fail closed for either invalid index.

    Registry freshness/provenance/hash/weight/token checks are reused. The
    Runtime token mapping is compared with the current NSE instrument master;
    a changed, missing, or stale token mapping blocks the entire dual-index load.
    """
    record_rows = list(records)
    registry = summarize_basket_registry(record_rows, as_of=as_of)
    baskets = registry.get("baskets", {})
    required = ("NIFTY", "BANKNIFTY")
    invalid = {
        symbol: baskets.get(symbol, {}).get("status", "MISSING")
        for symbol in required
        if baskets.get(symbol, {}).get("status") != "VERIFIED_FRESH"
    }
    if invalid:
        raise RuntimeError(f"verified NIFTY and BANKNIFTY baskets are required: {invalid}")

    latest: dict[str, tuple[tuple[str, str, int], Any]] = {}
    for record in record_rows:
        symbol = str(getattr(record, "underlying", "") or "").strip().upper()
        if symbol not in required:
            continue
        effective = getattr(record, "effective_date", None)
        created = getattr(record, "created_at", None)
        key = (
            effective.isoformat() if hasattr(effective, "isoformat") else "",
            created.isoformat() if hasattr(created, "isoformat") else "",
            int(getattr(record, "id", 0) or 0),
        )
        if symbol not in latest or key > latest[symbol][0]:
            latest[symbol] = (key, record)

    result: dict[str, tuple[WeightedEquity, ...]] = {}
    for symbol in required:
        record = latest[symbol][1]
        try:
            components = json.loads(record.components_json)
            token_mapping = json.loads(record.token_mapping_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{symbol} basket stored payload is unreadable") from exc
        if not isinstance(components, list) or not isinstance(token_mapping, dict):
            raise RuntimeError(f"{symbol} basket stored payload has an invalid shape")
        rows: list[WeightedEquity] = []
        seen: set[str] = set()
        for item in components:
            if not isinstance(item, dict):
                raise RuntimeError(f"{symbol} basket contains an invalid component")
            name = item.get("symbol")
            weight = item.get("weight")
            if not isinstance(name, str) or not name or name != name.strip().upper() or name in seen:
                raise RuntimeError(f"{symbol} basket contains a duplicate or invalid symbol")
            if isinstance(weight, bool):
                raise RuntimeError(f"{symbol} basket has an invalid weight for {name}")
            try:
                numeric_weight = float(weight)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(f"{symbol} basket has an invalid weight for {name}") from exc
            if not math.isfinite(numeric_weight) or numeric_weight <= 0:
                raise RuntimeError(f"{symbol} basket has a non-positive weight for {name}")
            token = token_mapping.get(name)
            if not isinstance(token, str) or not token.isdigit() or int(token) <= 0:
                raise RuntimeError(f"{symbol} basket has an invalid token mapping for {name}")
            try:
                current_row = instrument_master.resolve_cash_instrument(name, "NSE")
            except Exception as exc:
                raise RuntimeError(f"{symbol} current NSE instrument mapping is unavailable for {name}") from exc
            current_token = str(current_row.get("token", "")).strip() if isinstance(current_row, dict) else ""
            if not current_token or current_token != token:
                raise RuntimeError(f"{symbol} stored NSE token mapping is stale for {name}")
            seen.add(name)
            rows.append(WeightedEquity(name, numeric_weight))
        if not rows or set(token_mapping) != seen:
            raise RuntimeError(f"{symbol} basket token mapping does not match its components")
        result[symbol] = tuple(rows)
    return result

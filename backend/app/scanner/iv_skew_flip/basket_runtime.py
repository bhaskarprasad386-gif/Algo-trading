"""Build isolated runtime baskets only from current, fully verified registry records."""
from __future__ import annotations

import json
from datetime import date, datetime

from app.scanner.iv_skew_flip.basket_registry import summarize_basket_registry
from app.scanner.iv_skew_flip.equity_feed import WeightedEquity


def load_verified_runtime_baskets(records, *, as_of: date) -> dict[str, tuple[WeightedEquity, ...]]:
    """Return NIFTY and BANKNIFTY baskets or fail closed for either invalid index.

    Registry freshness/provenance/hash/weight/token checks are reused. The
    runtime receives weights only; the feed adapter independently resolves
    current NSE cash instruments before subscribing.
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

    latest: dict[str, object] = {}
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
            if numeric_weight <= 0:
                raise RuntimeError(f"{symbol} basket has a non-positive weight for {name}")
            token = token_mapping.get(name)
            if not isinstance(token, str) or not token.isdigit() or int(token) <= 0:
                raise RuntimeError(f"{symbol} basket has an invalid token mapping for {name}")
            seen.add(name)
            rows.append(WeightedEquity(name, numeric_weight))
        if not rows or set(token_mapping) != seen:
            raise RuntimeError(f"{symbol} basket token mapping does not match its components")
        result[symbol] = tuple(rows)
    return result

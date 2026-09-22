"""Strict canonical serialization for immutable backtest provenance."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def _canonical(value: Any) -> Any:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("provenance floats must be finite")
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, Mapping):
        items: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("provenance mapping keys must be strings")
            items.append((key, _canonical(item)))
        items.sort(key=lambda pair: pair[0])
        return {"type": "mapping", "value": items}
    if isinstance(value, list):
        return {"type": "list", "value": [_canonical(item) for item in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "value": [_canonical(item) for item in value]}
    raise TypeError(f"unsupported provenance value type: {type(value).__name__}")


def canonical_provenance_json(value: Any) -> str:
    """Return deterministic JSON without silently stringifying unsupported values."""
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def provenance_hash(value: Any) -> str:
    """Return the SHA-256 hash of canonical provenance JSON."""
    return hashlib.sha256(canonical_provenance_json(value).encode("utf-8")).hexdigest()

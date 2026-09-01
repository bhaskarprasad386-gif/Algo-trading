from typing import Any


def normalize_candles(rows: list[Any]) -> list[dict[str, Any]]:
    """Normalize provider candle arrays into stable records for storage/backtesting."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(dict(row))
            continue
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        normalized.append(
            {
                "timestamp": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
        )
    return normalized

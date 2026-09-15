from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from sqlalchemy.orm import Session

from app.models import Tick


class TickStorage:
    """Persist normalized ticks without coupling the websocket layer to SQLAlchemy."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _float(value: Any, *, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"tick {field} must be numeric")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"tick {field} must be numeric") from None
        if not math.isfinite(parsed):
            raise ValueError(f"tick {field} must be finite")
        return parsed

    def save(self, tick: dict[str, Any]) -> Tick:
        token = str(tick.get("token") or "").strip()
        symbol = str(tick.get("symbol") or tick.get("tradingSymbol") or token).strip()
        if not token or not symbol:
            raise ValueError("tick requires token and symbol")

        ltp = self._float(tick.get("ltp"), field="ltp")
        volume = self._float(tick.get("volume"), field="volume")
        if ltp is not None and ltp <= 0:
            raise ValueError("tick ltp must be positive")
        if volume is not None and volume < 0:
            raise ValueError("tick volume must be non-negative")

        row = Tick(
            token=token,
            symbol=symbol,
            ltp=ltp,
            volume=volume,
            received_at=datetime.now(timezone.utc),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def commit(self) -> None:
        self.db.commit()

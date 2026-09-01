from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Tick


class TickStorage:
    """Persist normalized ticks without coupling the websocket layer to SQLAlchemy."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def save(self, tick: dict[str, Any]) -> Tick:
        token = str(tick.get("token") or "").strip()
        symbol = str(tick.get("symbol") or tick.get("tradingSymbol") or token).strip()
        if not token or not symbol:
            raise ValueError("tick requires token and symbol")

        row = Tick(
            token=token,
            symbol=symbol,
            ltp=self._float(tick.get("ltp")),
            volume=self._float(tick.get("volume")),
            received_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def commit(self) -> None:
        self.db.commit()

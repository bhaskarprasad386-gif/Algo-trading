from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Candle


class CandleStorage:
    """Persist completed candles produced by CandleBuilder."""

    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        *,
        token: str,
        symbol: str,
        timeframe: str,
        candle: Any,
    ) -> Candle:
        token = str(token).strip()
        symbol = str(symbol).strip()
        timeframe = str(timeframe).strip()
        if not token or not symbol or not timeframe:
            raise ValueError("token, symbol and timeframe are required")

        row = Candle(
            token=token,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=candle.start.replace(tzinfo=None),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def commit(self) -> None:
        self.db.commit()

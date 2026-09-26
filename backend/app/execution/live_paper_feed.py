"""Bridge live market-data ticks into persistent live-paper execution.

The bridge is intentionally broker-neutral: a real market-data feed may call
handle_tick later, but this class never creates broker orders.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from app.execution.live_paper import LivePaperExecution


class LivePaperFeedBridge:
    def __init__(self, session_factory: Callable[[], Session], user_id: int,
                 engine: LivePaperExecution | None = None) -> None:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        self.session_factory = session_factory
        self.user_id = user_id
        self.engine = engine or LivePaperExecution()

    def handle_tick(self, tick: dict) -> list[dict]:
        db = self.session_factory()
        try:
            return self.engine.on_tick(db, user_id=self.user_id, tick=tick)
        finally:
            db.close()

from __future__ import annotations
import json
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.live_paper_trade import LivePaperTrade
from app.models.global_paper_setting import GlobalPaperSetting

IST_OFFSET = timezone.utc
MARKET_CLOSE = time(15, 30)
IST = ZoneInfo("Asia/Kolkata")

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_fresh_market_timestamp(timestamp_ns, now_ns, max_age_ns=5_000_000_000):
    """Accept timestamps no older than max_age and never future-dated."""
    try:
        timestamp_ns = int(timestamp_ns)
        now_ns = int(now_ns)
        max_age_ns = int(max_age_ns)
    except (TypeError, ValueError):
        return False
    if timestamp_ns <= 0 or now_ns <= 0 or max_age_ns < 0:
        return False
    return now_ns - max_age_ns <= timestamp_ns <= now_ns

def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            pass
    return None

class LivePaperTradeService:
    def enter_or_mark(self, db: Session, *, strategy_id, symbol, event_id, direction,
                      expiry=None, earliest_expiry=None, lot_size=1, lots=1,
                      edge=0.0, capital_used=0.0, legs=None, metadata=None,
                      user_id=1):
        event_id = str(event_id)
        existing = db.query(LivePaperTrade).filter(
            LivePaperTrade.event_id == event_id,
            LivePaperTrade.user_id == int(user_id),
            LivePaperTrade.status == "ONGOING",
        ).first()
        if existing:
            # A duplicate signal updates the mark only. Entry capital is fixed
            # for the lifetime of the paper position so its reservation cannot
            # drift when a later alert reports a different capital estimate.
            self.mark(db, existing, edge=edge)
            return existing, False
        if lot_size <= 0 or lots <= 0 or edge < 0:
            return None, False
        # Serialize capital allocation on the per-user global paper setting
        # row before reading reservations. SQLite otherwise allows two
        # deferred read transactions to observe the same free capital and
        # both insert trades. The no-op UPDATE acquires the database write
        # lock (and a row lock on databases that support it) without changing
        # the configured amount.
        lock_count = db.query(GlobalPaperSetting).filter(
            GlobalPaperSetting.user_id == int(user_id),
        ).update(
            {GlobalPaperSetting.paper_amount: GlobalPaperSetting.paper_amount},
            synchronize_session=False,
        )
        if lock_count == 0:
            return None, False
        setting = db.query(GlobalPaperSetting).filter(
            GlobalPaperSetting.user_id == int(user_id),
        ).first()
        if setting is None or not setting.enabled or setting.emergency_stop or float(setting.paper_amount) <= 0:
            return None, False
        capital_per_lot = max(0.0, float(capital_used)) / max(1, int(lots))
        if capital_per_lot > 0:
            paper_amount = max(0.0, float(setting.paper_amount))
            reserved_rows = db.query(LivePaperTrade.capital_used).filter(
                LivePaperTrade.user_id == int(user_id),
                LivePaperTrade.status == "ONGOING",
            ).all()
            reserved = sum(float(row[0] or 0.0) for row in reserved_rows)
            available = max(0.0, paper_amount - reserved)
            lots = min(int(lots), int(available // capital_per_lot))
            if lots <= 0:
                return None, False
            capital_used = capital_per_lot * lots
        trade = LivePaperTrade(
            user_id=int(user_id),
            strategy_id=str(strategy_id).strip().lower(),
            symbol=str(symbol).strip().upper(),
            event_id=event_id,
            direction=str(direction),
            expiry=str(expiry) if expiry else None,
            earliest_expiry=str(earliest_expiry or expiry) if (earliest_expiry or expiry) else None,
            lot_size=int(lot_size),
            lots=int(lots),
            entry_edge=float(edge),
            current_edge=float(edge),
            capital_used=max(0.0, float(capital_used)),
            unrealized_pnl=0.0,
            legs_json=json.dumps(legs or [], sort_keys=True, default=str),
            metadata_json=json.dumps(metadata or {}, sort_keys=True, default=str),
            status="ONGOING",
            opened_at=_now(),
            last_mark_at=_now(),
        )
        db.add(trade)
        try:
            db.commit()
        except IntegrityError:
            # Another concurrent request won the user/event insert race.
            # Roll back the failed INSERT, then treat the winner as the
            # duplicate mark-only position.
            db.rollback()
            existing = db.query(LivePaperTrade).filter(
                LivePaperTrade.event_id == event_id,
                LivePaperTrade.user_id == int(user_id),
                LivePaperTrade.status == "ONGOING",
            ).first()
            if existing is not None:
                self.mark(db, existing, edge=edge)
                return existing, False
            # A completed trade with the same event_id is intentionally not
            # reopened; the event has already been consumed.
            return None, False
        db.refresh(trade)
        return trade, True

    def mark(self, db: Session, trade: LivePaperTrade, *, edge: float, capital_used=None, pnl_override=None):
        if trade.status != "ONGOING":
            return trade
        current_edge = max(0.0, float(edge))
        unrealized_pnl = round(
            float(pnl_override) if pnl_override is not None else (current_edge - trade.entry_edge) * trade.lot_size * trade.lots,
            8,
        )
        effective_capital = (
            float(capital_used)
            if capital_used is not None and float(capital_used) > 0
            else float(trade.capital_used)
        )
        pnl_pct = round(unrealized_pnl / effective_capital * 100.0, 8) if effective_capital > 0 else 0.0
        # Conditional UPDATE prevents a stale monitor/API object from marking a
        # trade after another transaction has already completed it.
        values = {
            LivePaperTrade.current_edge: current_edge,
            LivePaperTrade.unrealized_pnl: unrealized_pnl,
            LivePaperTrade.pnl_pct: pnl_pct,
            LivePaperTrade.last_mark_at: _now(),
        }
        if capital_used is not None and float(capital_used) > 0:
            values[LivePaperTrade.capital_used] = effective_capital
        updated = db.query(LivePaperTrade).filter(
            LivePaperTrade.id == trade.id,
            LivePaperTrade.status == "ONGOING",
        ).update(values, synchronize_session=False)
        if updated:
            db.refresh(trade)
        else:
            db.expire(trade)
            db.refresh(trade)
        return trade

    def close(self, db: Session, trade: LivePaperTrade, reason="MANUAL"):
        # Close atomically against the current DB row. This prevents a stale
        # in-memory P&L from overwriting a newer mark during a close race.
        now = _now()
        updated = db.query(LivePaperTrade).filter(
            LivePaperTrade.id == trade.id,
            LivePaperTrade.status == "ONGOING",
        ).update(
            {
                LivePaperTrade.status: "COMPLETED",
                LivePaperTrade.exit_reason: reason,
                LivePaperTrade.realized_pnl: LivePaperTrade.unrealized_pnl,
                LivePaperTrade.closed_at: now,
                LivePaperTrade.last_mark_at: now,
            },
            synchronize_session=False,
        )
        if updated:
            db.commit()
            db.refresh(trade)
        else:
            db.expire(trade)
            db.refresh(trade)
        return trade

    def close_expired(self, db: Session, *, now=None):
        if now is None:
            local_now = datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None)
        elif now.tzinfo is None:
            local_now = now
        else:
            local_now = now.astimezone(IST).replace(tzinfo=None)
        closed = []
        for trade in db.query(LivePaperTrade).filter(LivePaperTrade.status == "ONGOING").all():
            expiry = _parse_date(trade.earliest_expiry)
            if expiry is None:
                continue
            try:
                metadata = json.loads(trade.metadata_json or "{}")
            except Exception:
                metadata = {}
            exchange = str(metadata.get("exchange") or "").strip().upper()
            close_time = time(23, 30) if exchange == "MCX" else MARKET_CLOSE
            if local_now.date() > expiry or (local_now.date() == expiry and local_now.time() >= close_time):
                closed_trade = self.close(db, trade, "EXPIRY_CLOSE")
                # A concurrent manual close may win the atomic status update.
                # Only report this trade as an expiry closure when EXPIRY_CLOSE
                # is actually the persisted terminal reason.
                if closed_trade.status == "COMPLETED" and closed_trade.exit_reason == "EXPIRY_CLOSE":
                    closed.append(closed_trade)
        return closed

    def ongoing(self, db: Session, user_id=1):
        return db.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == int(user_id),
            LivePaperTrade.status == "ONGOING",
        ).order_by(LivePaperTrade.unrealized_pnl.desc(), LivePaperTrade.opened_at.asc()).all()

    def completed(self, db: Session, user_id=1):
        return db.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == int(user_id),
            LivePaperTrade.status == "COMPLETED",
        ).order_by(LivePaperTrade.realized_pnl.desc(), LivePaperTrade.closed_at.desc()).all()

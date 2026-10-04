from __future__ import annotations
import json
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from app.models.live_paper_trade import LivePaperTrade
from app.models.global_paper_setting import GlobalPaperSetting

IST_OFFSET = timezone.utc
MARKET_CLOSE = time(15, 30)
IST = ZoneInfo("Asia/Kolkata")

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

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
            self.mark(db, existing, edge=edge, capital_used=capital_used)
            return existing, False
        if lot_size <= 0 or lots <= 0 or edge < 0:
            return None, False
        setting = db.query(GlobalPaperSetting).filter(GlobalPaperSetting.user_id == int(user_id)).first()
        if setting is None or not setting.enabled or setting.emergency_stop or float(setting.paper_amount) <= 0:
            return None, False
        capital_per_lot = max(0.0, float(capital_used)) / max(1, int(lots))
        if capital_per_lot > 0:
            lots = min(int(lots), int(float(setting.paper_amount) // capital_per_lot))
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
        db.commit()
        db.refresh(trade)
        return trade, True

    def mark(self, db: Session, trade: LivePaperTrade, *, edge: float, capital_used=None, pnl_override=None):
        trade.current_edge = max(0.0, float(edge))
        trade.unrealized_pnl = round(
            float(pnl_override) if pnl_override is not None
            else (trade.current_edge - trade.entry_edge) * trade.lot_size * trade.lots,
            8,
        )
        if capital_used is not None and float(capital_used) > 0:
            trade.capital_used = float(capital_used)
        trade.pnl_pct = round(
            trade.unrealized_pnl / trade.capital_used * 100.0, 8
        ) if trade.capital_used > 0 else 0.0
        trade.last_mark_at = _now()
        return trade

    def close(self, db: Session, trade: LivePaperTrade, reason="MANUAL"):
        if trade.status != "ONGOING":
            return trade
        trade.status = "COMPLETED"
        trade.exit_reason = reason
        trade.realized_pnl = round(trade.unrealized_pnl, 8)
        trade.closed_at = _now()
        trade.last_mark_at = trade.closed_at
        db.commit()
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
                closed.append(self.close(db, trade, "EXPIRY_CLOSE"))
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

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

def _valid_paper_setting(setting) -> bool:
    """Persisted paper settings must be finite and non-negative before use."""
    try:
        import math
        amount = float(setting.paper_amount)
        return math.isfinite(amount) and amount >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_persisted_trade_json(trade) -> bool:
    """Validate persisted JSON fields before they can affect P&L or expiry."""
    try:
        import math
        from collections.abc import Mapping
        legs = json.loads(getattr(trade, "legs_json", None) or "[]")
        metadata = json.loads(getattr(trade, "metadata_json", None) or "{}")
        if not isinstance(legs, list) or not isinstance(metadata, Mapping):
            return False
        for leg in legs:
            if not isinstance(leg, Mapping):
                return False
            side = str(leg.get("side", "")).strip().upper()
            if side not in {"BUY", "SELL"}:
                return False
            price = float(leg.get("price"))
            if not math.isfinite(price) or price <= 0:
                return False
        exchange = metadata.get("exchange")
        if exchange is not None and str(exchange).strip().upper() not in {"NSE", "NFO", "BSE", "BFO", "MCX"}:
            return False
        return True
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return False


def _valid_persisted_trade(trade) -> bool:
    """Return True only for persisted trade numerics safe for accounting."""
    try:
        import math
        lot_size = float(trade.lot_size)
        lots = float(trade.lots)
        entry_edge = float(trade.entry_edge)
        current_edge = float(trade.current_edge)
        capital_used = float(trade.capital_used)
        unrealized_pnl = float(trade.unrealized_pnl)
        realized_pnl = float(trade.realized_pnl)
        pnl_pct = float(trade.pnl_pct)
        if not (
            math.isfinite(lot_size) and lot_size.is_integer() and lot_size > 0
            and math.isfinite(lots) and lots.is_integer() and lots > 0
            and math.isfinite(entry_edge) and entry_edge >= 0
            and math.isfinite(current_edge) and current_edge >= 0
            and math.isfinite(capital_used) and capital_used > 0
            and math.isfinite(unrealized_pnl)
            and math.isfinite(realized_pnl)
            and math.isfinite(pnl_pct)
        ):
            return False
        strategy_id = str(getattr(trade, "strategy_id", "") or "").strip()
        symbol = str(getattr(trade, "symbol", "") or "").strip()
        event_id = str(getattr(trade, "event_id", "") or "").strip()
        direction = str(getattr(trade, "direction", "") or "").strip().upper()
        if not strategy_id or not symbol or not event_id or direction not in {"LONG", "SHORT"}:
            return False
        if len(strategy_id) > 128 or len(symbol) > 128 or len(event_id) > 512:
            return False
        if str(getattr(trade, "status", "") or "").upper() not in {"ONGOING", "COMPLETED"}:
            return False
        expiry = _parse_date(getattr(trade, "expiry", None))
        earliest_expiry = _parse_date(getattr(trade, "earliest_expiry", None))
        if expiry is None or earliest_expiry is None or earliest_expiry > expiry:
            return False
        opened_at = getattr(trade, "opened_at", None)
        last_mark_at = getattr(trade, "last_mark_at", None)
        closed_at = getattr(trade, "closed_at", None)
        if not isinstance(opened_at, datetime) or not isinstance(last_mark_at, datetime):
            return False
        if opened_at.tzinfo is not None or last_mark_at.tzinfo is not None:
            return False
        opened_ist_date = opened_at.replace(tzinfo=timezone.utc).astimezone(IST).date()
        if earliest_expiry < opened_ist_date:
            return False
        if last_mark_at < opened_at:
            return False
        status = str(trade.status).upper()
        exit_reason = getattr(trade, "exit_reason", None)
        expected_pnl_pct = round(unrealized_pnl / capital_used * 100.0, 8)
        if pnl_pct != expected_pnl_pct:
            return False
        if status == "ONGOING":
            if (
                closed_at is not None
                or exit_reason is not None
                or realized_pnl != 0.0
            ):
                return False
        elif (
            not isinstance(closed_at, datetime)
            or closed_at.tzinfo is not None
            or closed_at < opened_at
            or last_mark_at > closed_at
            or str(exit_reason or "").strip().upper() not in {"MANUAL", "EXPIRY_CLOSE"}
            or realized_pnl != unrealized_pnl
        ):
            return False
        return True
    except (TypeError, ValueError, OverflowError):
        return False


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
        event_id = str(event_id).strip()
        strategy_text = str(strategy_id).strip().lower()
        symbol_text = str(symbol).strip().upper()
        direction_text = str(direction).strip().upper()
        expiry_text = str(expiry).strip() if expiry is not None else ""
        earliest_expiry_text = str(earliest_expiry).strip() if earliest_expiry is not None else expiry_text
        expiry_date = _parse_date(expiry_text)
        earliest_expiry_date = _parse_date(earliest_expiry_text)
        opening_ist_date = datetime.now(timezone.utc).astimezone(IST).date()
        if (
            not event_id
            or not strategy_text
            or not symbol_text
            or direction_text not in {"LONG", "SHORT"}
            or not expiry_text
            or expiry_date is None
            or not earliest_expiry_text
            or earliest_expiry_date is None
            or earliest_expiry_date > expiry_date
            or earliest_expiry_date < opening_ist_date
        ):
            return None, False
        from collections.abc import Mapping
        import math
        if legs is not None:
            if not isinstance(legs, list):
                return None, False
            for leg in legs:
                if not isinstance(leg, Mapping):
                    return None, False
                side = str(leg.get("side", "")).strip().upper()
                if side not in {"BUY", "SELL"}:
                    return None, False
                if leg.get("price") is None:
                    return None, False
                try:
                    price = float(leg.get("price"))
                except (TypeError, ValueError):
                    return None, False
                if not math.isfinite(price) or price <= 0:
                    return None, False
        if metadata is not None and not isinstance(metadata, Mapping):
            return None, False
        if metadata is not None:
            exchange = metadata.get("exchange")
            if exchange is not None and str(exchange).strip().upper() not in {"NSE", "NFO", "BSE", "BFO", "MCX"}:
                return None, False
        try:
            lot_size_value = float(lot_size)
            lots_value = float(lots)
            edge_value = float(edge)
            capital_value = float(capital_used)
        except (TypeError, ValueError):
            return None, False
        if (
            not math.isfinite(lot_size_value)
            or not math.isfinite(lots_value)
            or not math.isfinite(edge_value)
            or not math.isfinite(capital_value)
            or not lot_size_value.is_integer()
            or not lots_value.is_integer()
        ):
            return None, False
        lot_size = int(lot_size_value)
        lots = int(lots_value)
        edge = edge_value
        capital_used = capital_value
        existing = db.query(LivePaperTrade).filter(
            LivePaperTrade.event_id == event_id,
            LivePaperTrade.user_id == int(user_id),
            LivePaperTrade.status == "ONGOING",
        ).first()
        if existing and not _valid_persisted_trade(existing):
            return None, False
        if existing:
            # event_id is user-scoped identity, but strategy and symbol are
            # part of the persisted trade identity too. Never let a malformed
            # cross-strategy/cross-symbol duplicate mark the wrong position.
            if (
                str(existing.strategy_id).strip().lower() != strategy_text
                or str(existing.symbol).strip().upper() != symbol_text
            ):
                return None, False
            # A duplicate signal updates the mark only. Entry capital is fixed
            # for the lifetime of the paper position so its reservation cannot
            # drift when a later alert reports a different capital estimate.
            self.mark(db, existing, edge=edge)
            return existing, False
        if lot_size <= 0 or lots <= 0 or edge < 0 or capital_used <= 0:
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
        user_trades = db.query(LivePaperTrade).filter(
            LivePaperTrade.user_id == int(user_id),
        ).all()
        if any(not _valid_persisted_trade(trade) for trade in user_trades):
            return None, False
        if (
            setting is None
            or not _valid_paper_setting(setting)
            or not setting.enabled
            or setting.emergency_stop
            or float(setting.paper_amount) <= 0
        ):
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
            # Avoid binary-float floor under-allocation at exact capital
            # boundaries; the service is also called directly outside alert dispatch.
            from decimal import Decimal, ROUND_FLOOR
            available_decimal = Decimal(str(available))
            per_lot_decimal = Decimal(str(capital_per_lot))
            allocatable_lots = int(
                (available_decimal / per_lot_decimal).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            lots = min(int(lots), max(0, allocatable_lots))
            if lots <= 0:
                return None, False
            capital_used = capital_per_lot * lots
        trade = LivePaperTrade(
            user_id=int(user_id),
            strategy_id=strategy_text,
            symbol=symbol_text,
            event_id=event_id,
            direction=direction_text,
            expiry=expiry_text,
            earliest_expiry=earliest_expiry_text,
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
                if not _valid_persisted_trade(existing):
                    return None, False
                if (
                    str(existing.strategy_id).strip().lower() != strategy_text
                    or str(existing.symbol).strip().upper() != symbol_text
                ):
                    return None, False
                self.mark(db, existing, edge=edge)
                return existing, False
            # A completed trade with the same event_id is intentionally not
            # reopened; the event has already been consumed.
            return None, False
        db.refresh(trade)
        return trade, True

    def mark(self, db: Session, trade: LivePaperTrade, *, edge: float, pnl_override=None):
        if trade.status != "ONGOING" or not _valid_persisted_trade(trade):
            return trade
        try:
            import math
            edge_value = float(edge)
            if not math.isfinite(edge_value) or edge_value < 0:
                return trade
            if pnl_override is not None:
                pnl_value = float(pnl_override)
                if not math.isfinite(pnl_value):
                    return trade
        except (TypeError, ValueError, OverflowError):
            return trade
        # Serialize P&L marks with alert risk gates on the same per-user
        # paper-setting row. Without this, a risk check could read an old
        # unrealized loss, then a concurrent mark could worsen the loss before
        # the new entry commits.
        lock_count = db.query(GlobalPaperSetting).filter(
            GlobalPaperSetting.user_id == int(trade.user_id),
        ).update(
            {GlobalPaperSetting.paper_amount: GlobalPaperSetting.paper_amount},
            synchronize_session=False,
        )
        if lock_count == 0:
            return trade
        current_edge = edge_value
        unrealized_pnl = round(
            pnl_value if pnl_override is not None else (current_edge - trade.entry_edge) * trade.lot_size * trade.lots,
            8,
        )
        effective_capital = float(trade.capital_used)
        pnl_pct = round(unrealized_pnl / effective_capital * 100.0, 8) if effective_capital > 0 else 0.0
        # Conditional UPDATE prevents a stale monitor/API object from marking a
        # trade after another transaction has already completed it.
        values = {
            LivePaperTrade.current_edge: current_edge,
            LivePaperTrade.unrealized_pnl: unrealized_pnl,
            LivePaperTrade.pnl_pct: pnl_pct,
            LivePaperTrade.last_mark_at: _now(),
        }
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

    def close(self, db: Session, trade: LivePaperTrade, reason="MANUAL", *, commit=True):
        reason_text = str(reason or "").strip().upper()
        if reason_text not in {"MANUAL", "EXPIRY_CLOSE"}:
            return trade
        if trade.status != "ONGOING" or not _valid_persisted_trade(trade):
            return trade
        # Serialize terminal close with P&L marks and risk gates on the
        # same per-user paper-setting row. Without this lock, a close could
        # commit between a mark's risk-lock/read and its conditional UPDATE,
        # allowing a stale mark transaction to race the terminal transition.
        lock_count = db.query(GlobalPaperSetting).filter(
            GlobalPaperSetting.user_id == int(trade.user_id),
        ).update(
            {GlobalPaperSetting.paper_amount: GlobalPaperSetting.paper_amount},
            synchronize_session=False,
        )
        # Legacy/externally-created trades may exist without a global
        # paper setting. Manual/expiry close must still be able to terminate
        # those trades; they cannot participate in the risk-gated entry path,
        # and mark() likewise requires this serialization row.
        # Close atomically against the current DB row. This prevents a stale
        # in-memory P&L from overwriting a newer mark during a close race.
        now = _now()
        updated = db.query(LivePaperTrade).filter(
            LivePaperTrade.id == trade.id,
            LivePaperTrade.status == "ONGOING",
        ).update(
            {
                LivePaperTrade.status: "COMPLETED",
                LivePaperTrade.exit_reason: reason_text,
                LivePaperTrade.realized_pnl: LivePaperTrade.unrealized_pnl,
                LivePaperTrade.closed_at: now,
                LivePaperTrade.last_mark_at: now,
            },
            synchronize_session=False,
        )
        if updated:
            if commit:
                db.commit()
            else:
                db.flush()
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
            if not _valid_persisted_trade(trade):
                continue
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
                # Expiry release and new-entry risk gates must serialize on the
                # same per-user setting row. Otherwise an entry can calculate
                # available capital while this expiry close is still pending,
                # or the expiry close can race a max-loss/daily-cap read.
                # Users without a paper setting cannot enter through the risk
                # gated dispatcher, so there is no competing allocation to
                # serialize in that case.
                db.query(GlobalPaperSetting).filter(
                    GlobalPaperSetting.user_id == int(trade.user_id),
                ).update(
                    {GlobalPaperSetting.paper_amount: GlobalPaperSetting.paper_amount},
                    synchronize_session=False,
                )
                closed_trade = self.close(db, trade, "EXPIRY_CLOSE", commit=False)
                # A concurrent manual close may win the atomic status update.
                # Only report this trade as an expiry closure when EXPIRY_CLOSE
                # is actually the persisted terminal reason.
                if closed_trade.status == "COMPLETED" and closed_trade.exit_reason == "EXPIRY_CLOSE":
                    closed.append(closed_trade)
        if closed:
            # Expiry processing is one explicit transaction boundary. The
            # caller can opt into commit=False only when it owns the session
            # transaction; all normal expiry callers get a durable commit.
            db.commit()
        return closed

    def ongoing(self, db: Session, user_id=1):
        return [
            trade for trade in db.query(LivePaperTrade).filter(
                LivePaperTrade.user_id == int(user_id),
                LivePaperTrade.status == "ONGOING",
            ).order_by(
                LivePaperTrade.unrealized_pnl.desc(),
                LivePaperTrade.opened_at.asc(),
                LivePaperTrade.id.asc(),
            ).all()
            if _valid_persisted_trade(trade)
        ]

    def completed(self, db: Session, user_id=1):
        return [
            trade for trade in db.query(LivePaperTrade).filter(
                LivePaperTrade.user_id == int(user_id),
                LivePaperTrade.status == "COMPLETED",
            ).order_by(
                LivePaperTrade.realized_pnl.desc(),
                LivePaperTrade.closed_at.desc(),
                LivePaperTrade.id.desc(),
            ).all()
            if _valid_persisted_trade(trade)
        ]

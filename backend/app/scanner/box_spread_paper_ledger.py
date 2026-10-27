"""Persistent, isolated Box Spread paper ledger.

This module records paper-only trades and fills. It has no broker dependency
and does not write to generic trading accounts or live scanner history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.core.database import Base


CENT = Decimal("0.01")
DEFAULT_PAPER_BALANCE = Decimal("10000000.00")


def _money(value: object, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a finite non-negative amount") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be a finite non-negative amount")
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def _positive_quantity(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("fill quantity must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("fill quantity must be a positive integer") from None
    if result <= 0 or str(value).strip() not in {str(result), f"{result}.0"}:
        raise ValueError("fill quantity must be a positive integer")
    return result


@dataclass(frozen=True)
class BoxSpreadPaperFeeSchedule:
    """Explicit, versioned estimates; rates must be reviewed before deployment."""

    version: str = "india-options-estimate-v1"
    brokerage_per_fill: Decimal = Decimal("20.00")
    exchange_transaction_rate: Decimal = Decimal("0.0003503")
    sebi_rate: Decimal = Decimal("0.000001")
    gst_rate: Decimal = Decimal("0.18")
    sell_stt_rate: Decimal = Decimal("0.001")
    buy_stamp_rate: Decimal = Decimal("0.00003")

    def calculate(self, *, action: str, notional: Decimal) -> Decimal:
        if action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        brokerage = self.brokerage_per_fill
        exchange = notional * self.exchange_transaction_rate
        sebi = notional * self.sebi_rate
        gst = (brokerage + exchange) * self.gst_rate
        stt = notional * self.sell_stt_rate if action == "SELL" else Decimal("0")
        stamp = notional * self.buy_stamp_rate if action == "BUY" else Decimal("0")
        return (brokerage + exchange + sebi + gst + stt + stamp).quantize(CENT, rounding=ROUND_HALF_UP)


class BoxSpreadPaperAccount(Base):
    __tablename__ = "box_spread_paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    virtual_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BoxSpreadPaperTradeRecord(Base):
    __tablename__ = "box_spread_paper_trades"
    __table_args__ = (
        UniqueConstraint("account_key", "idempotency_key", name="uq_box_paper_trade_idempotency"),
        Index("ix_box_paper_trades_account_created", "account_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    underlying: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_class: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    low_strike: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high_strike: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    lots: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    fee_schedule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BoxSpreadPaperLedgerEntry(Base):
    __tablename__ = "box_spread_paper_ledger_entries"
    __table_args__ = (
        UniqueConstraint("account_key", "entry_key", name="uq_box_paper_ledger_entry"),
        Index("ix_box_paper_ledger_account_created", "account_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_id: Mapped[int] = mapped_column(ForeignKey("box_spread_paper_trades.id"), nullable=False)
    entry_key: Mapped[str] = mapped_column(String(256), nullable=False)
    fill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash_delta: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    fee_schedule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class BoxSpreadPaperFillReceipt:
    trade_id: int
    fill_id: str
    role: str
    action: str
    quantity: int
    fill_price: Decimal
    notional: Decimal
    charges: Decimal
    cash_delta: Decimal
    virtual_balance: Decimal
    fee_schedule_version: str


class BoxSpreadPaperLedger:
    """Synchronous DB unit-of-work; call from a worker, never a market-data callback."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        fee_schedule: BoxSpreadPaperFeeSchedule | None = None,
        starting_balance: object = DEFAULT_PAPER_BALANCE,
    ) -> None:
        self._session_factory = session_factory
        self.fee_schedule = fee_schedule or BoxSpreadPaperFeeSchedule()
        self.starting_balance = _money(starting_balance, field="starting balance")
        if not self.fee_schedule.version.strip():
            raise ValueError("fee schedule version is required")

    def ensure_account(self, account_key: str = "default") -> Decimal:
        key = self._account_key(account_key)
        with self._session_factory() as session, session.begin():
            account = session.scalar(select(BoxSpreadPaperAccount).where(BoxSpreadPaperAccount.account_key == key))
            if account is None:
                account = BoxSpreadPaperAccount(
                    account_key=key, starting_balance=self.starting_balance,
                    virtual_balance=self.starting_balance, created_at=datetime.now(timezone.utc),
                )
                session.add(account)
                session.flush()
            return Decimal(account.virtual_balance)

    def create_trade(
        self,
        *,
        idempotency_key: str,
        underlying: str,
        instrument_class: str,
        expiry: str,
        low_strike: object,
        high_strike: object,
        direction: str,
        lots: int,
        lot_size: int,
        account_key: str = "default",
    ) -> BoxSpreadPaperTradeRecord:
        account = self._account_key(account_key)
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("idempotency_key is required and must be at most 200 characters")
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        lots_value, lot_value = _positive_quantity(lots), _positive_quantity(lot_size)
        low, high = _money(low_strike, field="low strike"), _money(high_strike, field="high strike")
        if low >= high:
            raise ValueError("low strike must be below high strike")
        if not str(underlying).strip() or not str(expiry).strip() or not str(instrument_class).strip():
            raise ValueError("underlying, instrument_class and expiry are required")
        with self._session_factory() as session, session.begin():
            self._ensure_account_in_session(session, account)
            existing = session.scalar(select(BoxSpreadPaperTradeRecord).where(
                BoxSpreadPaperTradeRecord.account_key == account,
                BoxSpreadPaperTradeRecord.idempotency_key == key,
            ))
            if existing is not None:
                expected = (str(underlying).upper(), str(instrument_class).upper(), str(expiry),
                            low, high, direction, lots_value, lot_value)
                actual = (existing.underlying, existing.instrument_class, existing.expiry,
                          Decimal(existing.low_strike), Decimal(existing.high_strike),
                          existing.direction, existing.lots, existing.lot_size)
                if expected != actual:
                    raise ValueError("idempotency_key reused for a different trade")
                session.expunge(existing)
                return existing
            trade = BoxSpreadPaperTradeRecord(
                account_key=account, idempotency_key=key, underlying=str(underlying).upper(),
                instrument_class=str(instrument_class).upper(), expiry=str(expiry),
                low_strike=low, high_strike=high, direction=direction,
                quantity=lots_value * lot_value, lots=lots_value, lot_size=lot_value,
                status="CREATED", fee_schedule_version=self.fee_schedule.version,
                created_at=datetime.now(timezone.utc),
            )
            session.add(trade)
            session.flush()
            session.expunge(trade)
            return trade

    def record_fill(
        self,
        *,
        trade_id: int,
        fill_id: str,
        role: str,
        action: str,
        quantity: int,
        fill_price: object,
        account_key: str = "default",
    ) -> BoxSpreadPaperFillReceipt:
        account_key = self._account_key(account_key)
        fill_id, role, action = str(fill_id or "").strip(), str(role or "").upper(), str(action or "").upper()
        if not fill_id or len(fill_id) > 128:
            raise ValueError("fill_id is required and must be at most 128 characters")
        if role not in {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}:
            raise ValueError("unknown box spread leg role")
        if action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        qty = _positive_quantity(quantity)
        price = _money(fill_price, field="fill price")
        if price <= 0:
            raise ValueError("fill price must be positive")
        notional = (price * qty).quantize(CENT, rounding=ROUND_HALF_UP)
        charges = self.fee_schedule.calculate(action=action, notional=notional)
        cash_delta = (-notional if action == "BUY" else notional) - charges
        entry_key = f"{trade_id}:{fill_id}"

        with self._session_factory() as session, session.begin():
            account = self._ensure_account_in_session(session, account_key)
            trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
            if trade is None or trade.account_key != account_key:
                raise ValueError("paper trade not found for account")
            existing = session.scalar(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.account_key == account_key,
                BoxSpreadPaperLedgerEntry.entry_key == entry_key,
            ))
            if existing is not None:
                expected = (role, action, qty, price)
                actual = (existing.role, existing.action, existing.quantity, Decimal(existing.fill_price))
                if expected != actual:
                    raise ValueError("fill_id reused with different fill data")
                receipt = self._receipt(existing, Decimal(account.virtual_balance))
                return receipt
            if trade.status in {"CLOSED", "CANCELLED"}:
                raise ValueError("cannot add a fill to a closed or cancelled trade")
            if trade.status == "EXITING":
                raise ValueError("cannot add entry fills while exits are in progress")
            expected_actions = (
                {"LOW_CE": "BUY", "HIGH_CE": "SELL", "HIGH_PE": "BUY", "LOW_PE": "SELL"}
                if trade.direction == "LONG"
                else {"LOW_CE": "SELL", "HIGH_CE": "BUY", "HIGH_PE": "SELL", "LOW_PE": "BUY"}
            )
            if action != expected_actions[role]:
                raise ValueError("fill action does not match the paper trade direction")
            entry_rows = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade.id,
                BoxSpreadPaperLedgerEntry.role == role,
                BoxSpreadPaperLedgerEntry.account_key == account_key,
            )).all()
            already_filled = sum(row.quantity for row in entry_rows if not row.fill_id.startswith(("EXIT:", "SETTLE:")))
            if already_filled + qty > trade.quantity:
                raise ValueError("fill quantity exceeds remaining leg quantity")
            if cash_delta < 0 and Decimal(account.virtual_balance) + cash_delta < 0:
                raise ValueError("insufficient virtual balance")
            entry = BoxSpreadPaperLedgerEntry(
                account_key=account_key, trade_id=trade.id, entry_key=entry_key, fill_id=fill_id,
                role=role, action=action, quantity=qty, fill_price=price, notional=notional,
                charges=charges, cash_delta=cash_delta, fee_schedule_version=self.fee_schedule.version,
                created_at=datetime.now(timezone.utc),
            )
            session.add(entry)
            account.virtual_balance = Decimal(account.virtual_balance) + cash_delta
            session.flush()
            role_totals = {
                role_name: sum(
                    row.quantity for row in session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                        BoxSpreadPaperLedgerEntry.trade_id == trade.id,
                        BoxSpreadPaperLedgerEntry.role == role_name,
                        BoxSpreadPaperLedgerEntry.account_key == account_key,
                    )).all()
                    if not row.fill_id.startswith(("EXIT:", "SETTLE:"))
                )
                for role_name in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
            }
            trade.status = "FILLED" if all(total == trade.quantity for total in role_totals.values()) else "PARTIALLY_FILLED"
            receipt = self._receipt(entry, Decimal(account.virtual_balance))
            return receipt


    def record_exit_fill(
        self,
        *,
        trade_id: int,
        fill_id: str,
        role: str,
        quantity: int,
        fill_price: object,
        account_key: str = "default",
        settlement: bool = False,
    ) -> BoxSpreadPaperFillReceipt:
        """Close an existing paper position using a fresh exit or settlement price."""
        key = self._account_key(account_key)
        role = str(role or "").upper()
        raw_id = str(fill_id or "").strip()
        if role not in {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}:
            raise ValueError("unknown box spread leg role")
        if not raw_id or len(raw_id) > 96:
            raise ValueError("exit fill_id is required and must be at most 96 characters")
        qty = _positive_quantity(quantity)
        price = _money(fill_price, field="exit price")
        if price < 0 or (price == 0 and not settlement):
            raise ValueError("exit price must be positive; zero is allowed only for official expiry settlement")
        prefix = "SETTLE:" if settlement else "EXIT:"
        stored_fill_id = f"{prefix}{raw_id}"
        entry_key = f"{trade_id}:{stored_fill_id}"
        notional = (price * qty).quantize(CENT, rounding=ROUND_HALF_UP)

        with self._session_factory() as session, session.begin():
            account = self._ensure_account_in_session(session, key)
            trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
            if trade is None or trade.account_key != key:
                raise ValueError("paper trade not found for account")
            existing = session.scalar(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.account_key == key,
                BoxSpreadPaperLedgerEntry.entry_key == entry_key,
            ))
            if existing is not None:
                if (existing.role, existing.quantity, Decimal(existing.fill_price)) != (role, qty, price):
                    raise ValueError("exit fill_id reused with different fill data")
                return self._receipt(existing, Decimal(account.virtual_balance))
            if trade.status in {"CLOSED", "CANCELLED"}:
                raise ValueError("paper trade is already closed or cancelled")
            rows = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                BoxSpreadPaperLedgerEntry.account_key == key,
                BoxSpreadPaperLedgerEntry.role == role,
            )).all()
            signed_position = sum(row.quantity * (1 if row.action == "BUY" else -1) for row in rows)
            if signed_position == 0:
                raise ValueError("no open position exists for this leg")
            if qty > abs(signed_position):
                raise ValueError("exit quantity exceeds open leg quantity")
            action = "SELL" if signed_position > 0 else "BUY"
            charges = self.fee_schedule.calculate(action=action, notional=notional)
            cash_delta = (-notional if action == "BUY" else notional) - charges
            # Closing/settlement must remain possible even if the virtual account is
            # underwater; record the liability instead of stranding exposure.
            entry = BoxSpreadPaperLedgerEntry(
                account_key=key, trade_id=trade.id, entry_key=entry_key,
                fill_id=stored_fill_id, role=role, action=action, quantity=qty,
                fill_price=price, notional=notional, charges=charges,
                cash_delta=cash_delta, fee_schedule_version=self.fee_schedule.version,
                created_at=datetime.now(timezone.utc),
            )
            session.add(entry)
            account.virtual_balance = Decimal(account.virtual_balance) + cash_delta
            session.flush()
            positions = {}
            all_rows = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                BoxSpreadPaperLedgerEntry.account_key == key,
            )).all()
            for row in all_rows:
                positions[row.role] = positions.get(row.role, 0) + row.quantity * (1 if row.action == "BUY" else -1)
            trade.status = "CLOSED" if all(position == 0 for position in positions.values()) else "EXITING"
            return self._receipt(entry, Decimal(account.virtual_balance))

    def settle_expiry(
        self,
        *,
        trade_id: int,
        settlement_prices: dict[str, object],
        settlement_date: date | str,
        account_key: str = "default",
    ) -> dict[str, object]:
        """Settle on/after expiry using explicit official settlement prices, never stale LTP."""
        key = self._account_key(account_key)
        try:
            actual_date = settlement_date if isinstance(settlement_date, date) else date.fromisoformat(str(settlement_date))
        except (TypeError, ValueError):
            raise ValueError("settlement_date must be an ISO date") from None
        with self._session_factory() as session:
            trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
            if trade is None or trade.account_key != key:
                raise ValueError("paper trade not found for account")
            try:
                expiry_date = date.fromisoformat(trade.expiry[:10])
            except ValueError:
                raise ValueError("trade expiry must be normalized to YYYY-MM-DD before settlement") from None
            if actual_date < expiry_date:
                raise ValueError("cannot settle before expiry")
            rows = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                BoxSpreadPaperLedgerEntry.account_key == key,
            )).all()
        positions: dict[str, int] = {}
        for row in rows:
            positions[row.role] = positions.get(row.role, 0) + row.quantity * (1 if row.action == "BUY" else -1)
        open_roles = {role for role, quantity in positions.items() if quantity != 0}
        if open_roles - set(settlement_prices):
            raise ValueError("settlement_prices must include every open leg role")
        receipts = []
        for role in sorted(open_roles):
            receipts.append(self.record_exit_fill(
                trade_id=trade_id, fill_id=f"{actual_date.isoformat()}:{role}",
                role=role, quantity=abs(positions[role]), fill_price=settlement_prices[role],
                account_key=key, settlement=True,
            ))
        return {
            "trade_id": trade_id, "settlement_date": actual_date.isoformat(),
            "settled_legs": len(receipts),
            "status": self.get_trade_summary(trade_id, account_key=key)["status"],
            "receipts": tuple(receipts),
        }

    def list_open_trades(self, account_key: str = "default") -> list[dict[str, object]]:
        """Return durable non-terminal trades for restart recovery."""
        key = self._account_key(account_key)
        with self._session_factory() as session:
            trades = session.scalars(select(BoxSpreadPaperTradeRecord).where(
                BoxSpreadPaperTradeRecord.account_key == key,
                BoxSpreadPaperTradeRecord.status.notin_(("CLOSED", "CANCELLED")),
            ).order_by(BoxSpreadPaperTradeRecord.created_at, BoxSpreadPaperTradeRecord.id)).all()
            return [
                {"trade_id": trade.id, "idempotency_key": trade.idempotency_key,
                 "underlying": trade.underlying, "expiry": trade.expiry,
                 "status": trade.status, "quantity": trade.quantity}
                for trade in trades
            ]

    def get_account_summary(self, account_key: str = "default") -> dict[str, Decimal | int | str]:
        key = self._account_key(account_key)
        with self._session_factory() as session:
            account = session.scalar(select(BoxSpreadPaperAccount).where(BoxSpreadPaperAccount.account_key == key))
            if account is None:
                return {"account_key": key, "starting_balance": self.starting_balance,
                        "virtual_balance": self.starting_balance, "total_charges": Decimal("0.00"),
                        "net_cash_flow": Decimal("0.00"), "fill_count": 0}
            entries = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.account_key == key
            )).all()
            return {
                "account_key": key, "starting_balance": Decimal(account.starting_balance),
                "virtual_balance": Decimal(account.virtual_balance),
                "total_charges": sum((Decimal(row.charges) for row in entries), Decimal("0.00")),
                "net_cash_flow": sum((Decimal(row.cash_delta) for row in entries), Decimal("0.00")),
                "fill_count": len(entries),
            }

    def get_trade_summary(
        self,
        trade_id: int,
        *,
        account_key: str = "default",
        mark_prices: dict[str, object] | None = None,
    ) -> dict[str, Decimal | int | str | None]:
        """Return durable cash/fee totals and optional open-position mark-to-market P&L."""
        key = self._account_key(account_key)
        with self._session_factory() as session:
            trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
            if trade is None or trade.account_key != key:
                raise ValueError("paper trade not found for account")
            entries = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                BoxSpreadPaperLedgerEntry.account_key == key,
            )).all()

        net_cash_flow = sum((Decimal(row.cash_delta) for row in entries), Decimal("0.00"))
        total_charges = sum((Decimal(row.charges) for row in entries), Decimal("0.00"))
        buy_notional = sum((Decimal(row.notional) for row in entries if row.action == "BUY"), Decimal("0.00"))
        sell_notional = sum((Decimal(row.notional) for row in entries if row.action == "SELL"), Decimal("0.00"))
        open_lots: dict[str, list[dict[str, object]]] = {}
        realized_pnl = Decimal("0.00")
        role_positions: dict[str, int] = {}
        for row in entries:
            role_lots = open_lots.setdefault(row.role, [])
            qty_left = row.quantity
            row_price = Decimal(row.fill_price)
            row_charges = Decimal(row.charges)
            row_fee_per_unit = row_charges / Decimal(row.quantity)
            direction = 1 if row.action == "BUY" else -1
            role_positions[row.role] = role_positions.get(row.role, 0) + direction * row.quantity

            # FIFO-match opposite-side fills to open quantities. This also
            # supports partial exits and repeated close fills after restarts.
            while qty_left > 0 and role_lots and role_lots[0]["direction"] != direction:
                lot = role_lots[0]
                matched = min(qty_left, int(lot["quantity"]))
                entry_direction = int(lot["direction"])
                entry_price = Decimal(lot["price"])
                entry_fee_per_unit = Decimal(lot["fee_per_unit"])
                realized_pnl += (
                    (row_price - entry_price) * entry_direction * matched
                    - (entry_fee_per_unit + row_fee_per_unit) * matched
                )
                lot["quantity"] = int(lot["quantity"]) - matched
                qty_left -= matched
                if int(lot["quantity"]) == 0:
                    role_lots.pop(0)
            if qty_left > 0:
                role_lots.append({
                    "quantity": qty_left,
                    "direction": direction,
                    "price": row_price,
                    "fee_per_unit": row_fee_per_unit,
                })

        unrealized_pnl: Decimal | None = None
        open_roles = {role for role, lots in open_lots.items() if lots}
        if mark_prices is not None and open_roles:
            if open_roles - set(mark_prices):
                raise ValueError("mark_prices must include every open leg role")
            unrealized_pnl = Decimal("0.00")
            for role in open_roles:
                try:
                    mark = Decimal(str(mark_prices[role]))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError(f"{role} mark price must be positive") from None
                if not mark.is_finite() or mark <= 0:
                    raise ValueError(f"{role} mark price must be positive")
                for lot in open_lots[role]:
                    unrealized_pnl += (
                        (mark - Decimal(lot["price"])) * int(lot["direction"]) * int(lot["quantity"])
                        - Decimal(lot["fee_per_unit"]) * int(lot["quantity"])
                    )
            unrealized_pnl = unrealized_pnl.quantize(CENT, rounding=ROUND_HALF_UP)
        elif not open_roles:
            unrealized_pnl = Decimal("0.00")

        return {
            "trade_id": trade.id,
            "status": trade.status,
            "quantity": trade.quantity,
            "fill_count": len(entries),
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "total_charges": total_charges,
            "net_cash_flow": net_cash_flow,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl.quantize(CENT, rounding=ROUND_HALF_UP),
            "fee_schedule_version": trade.fee_schedule_version,
        }

    def list_fills(self, trade_id: int, account_key: str = "default") -> list[BoxSpreadPaperFillReceipt]:
        key = self._account_key(account_key)
        with self._session_factory() as session:
            rows = session.scalars(select(BoxSpreadPaperLedgerEntry).where(
                BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                BoxSpreadPaperLedgerEntry.account_key == key,
            ).order_by(BoxSpreadPaperLedgerEntry.id)).all()
            return [self._receipt(row, Decimal("0.00")) for row in rows]

    def _ensure_account_in_session(self, session: Session, key: str) -> BoxSpreadPaperAccount:
        account = session.scalar(select(BoxSpreadPaperAccount).where(BoxSpreadPaperAccount.account_key == key))
        if account is None:
            account = BoxSpreadPaperAccount(
                account_key=key, starting_balance=self.starting_balance,
                virtual_balance=self.starting_balance, created_at=datetime.now(timezone.utc),
            )
            session.add(account)
            session.flush()
        return account

    @staticmethod
    def _receipt(entry: BoxSpreadPaperLedgerEntry, balance: Decimal) -> BoxSpreadPaperFillReceipt:
        return BoxSpreadPaperFillReceipt(
            trade_id=entry.trade_id, fill_id=entry.fill_id, role=entry.role, action=entry.action,
            quantity=entry.quantity, fill_price=Decimal(entry.fill_price), notional=Decimal(entry.notional),
            charges=Decimal(entry.charges), cash_delta=Decimal(entry.cash_delta),
            virtual_balance=balance, fee_schedule_version=entry.fee_schedule_version,
        )

    @staticmethod
    def _account_key(value: str) -> str:
        key = str(value or "").strip()
        if not key or len(key) > 128:
            raise ValueError("account_key is required and must be at most 128 characters")
        return key


__all__ = [
    "BoxSpreadPaperAccount", "BoxSpreadPaperFeeSchedule", "BoxSpreadPaperFillReceipt",
    "BoxSpreadPaperLedger", "BoxSpreadPaperLedgerEntry", "BoxSpreadPaperTradeRecord",
]

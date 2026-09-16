"""Capital, margin, position and P&L accounting for universal backtests."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import math

from app.backtesting.config import DEFAULT_PAPER_CAPITAL
from app.backtesting.execution import ExecutionSide, SimFill


class RiskViolation(ValueError):
    """Raised when a fill would breach configured portfolio risk limits."""


@dataclass(frozen=True)
class RiskConfig:
    initial_margin_rate: float = 1.0
    maintenance_margin_rate: float | None = None
    max_gross_notional: float | None = None
    max_net_notional: float | None = None
    max_leverage: float | None = None
    max_position_quantity: int | None = None
    max_drawdown: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.initial_margin_rate)) or not 0 < self.initial_margin_rate <= 1:
            raise ValueError("initial_margin_rate must be finite and in (0, 1]")
        maintenance = self.initial_margin_rate if self.maintenance_margin_rate is None else self.maintenance_margin_rate
        if not math.isfinite(float(maintenance)) or not 0 < maintenance <= 1:
            raise ValueError("maintenance_margin_rate must be finite and in (0, 1]")
        if maintenance > self.initial_margin_rate:
            raise ValueError("maintenance_margin_rate cannot exceed initial_margin_rate")
        object.__setattr__(self, "maintenance_margin_rate", maintenance)
        for name in ("max_gross_notional", "max_net_notional", "max_leverage", "max_drawdown"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(float(value)) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.max_position_quantity is not None:
            if isinstance(self.max_position_quantity, bool) or not isinstance(self.max_position_quantity, int) or self.max_position_quantity <= 0:
                raise ValueError("max_position_quantity must be a positive integer")


@dataclass(frozen=True)
class Position:
    instrument: str
    quantity: int = 0
    average_price: float = 0.0
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument is required")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise ValueError("quantity must be an integer")
        if not math.isfinite(float(self.average_price)) or self.average_price < 0:
            raise ValueError("average_price must be finite and non-negative")
        if self.quantity != 0 and self.average_price <= 0:
            raise ValueError("average_price must be positive for an open position")
        if not math.isfinite(float(self.realized_pnl)):
            raise ValueError("realized_pnl must be finite")


@dataclass(frozen=True)
class TradeRecord:
    order_id: str
    instrument: str
    side: ExecutionSide
    quantity: int
    price: float
    gross_value: float
    fee: float
    realized_pnl_delta: float
    cash_after: float
    equity_after: float
    timestamp_ns: int


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: tuple[Position, ...]
    gross_notional: float = 0.0
    net_notional: float = 0.0
    initial_margin: float = 0.0
    maintenance_margin: float = 0.0
    available_margin: float = 0.0
    leverage: float = 0.0
    drawdown: float = 0.0
    fees: float = 0.0
    reserved_margin: float = 0.0


class Portfolio:
    """Deterministic capital, margin, risk and P&L accounting."""

    def __init__(self, initial_cash: float = DEFAULT_PAPER_CAPITAL, risk_config: RiskConfig | None = None) -> None:
        if not math.isfinite(float(initial_cash)) or initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.risk_config = risk_config or RiskConfig()
        self._positions: dict[str, Position] = {}
        self._realized_pnl = 0.0
        self._fees = 0.0
        self._peak_equity = float(initial_cash)
        self._reserved_margin: dict[str, float] = {}
        self._trades: list[TradeRecord] = []

    @property
    def reserved_margin(self) -> float:
        return sum(self._reserved_margin.values())

    @property
    def trades(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trades)

    def _metrics(self, marks: dict[str, float]) -> tuple[float, float, float, float]:
        gross = net = unrealized = 0.0
        for p in self._positions.values():
            if p.instrument not in marks:
                raise ValueError(f"missing market mark for {p.instrument}")
            mark = marks[p.instrument]
            if not math.isfinite(float(mark)) or mark <= 0:
                raise ValueError(f"mark must be finite and positive for {p.instrument}")
            notional = p.quantity * mark
            gross += abs(notional)
            net += notional
            unrealized += p.quantity * (mark - p.average_price)
        return gross, net, unrealized, self.cash + net

    def reserve_margin(self, order_id: str, amount: float, marks: dict[str, float] | None = None) -> float:
        if not order_id.strip():
            raise ValueError("order_id is required")
        if not math.isfinite(float(amount)) or amount < 0:
            raise ValueError("reserved margin must be finite and non-negative")
        if order_id in self._reserved_margin:
            raise RiskViolation("margin already reserved for order")
        available = self.snapshot(marks).available_margin
        if amount > available + 1e-9:
            raise RiskViolation("insufficient available margin for order reservation")
        self._reserved_margin[order_id] = float(amount)
        return float(amount)

    def replace_margin_reservation(self, old_order_id: str, new_order_id: str, amount: float, marks: dict[str, float] | None = None) -> float:
        """Atomically replace one pending order's margin reservation."""
        if not old_order_id.strip() or not new_order_id.strip():
            raise ValueError("order ids are required")
        if old_order_id == new_order_id:
            raise ValueError("replacement must use a new order_id")
        if new_order_id in self._reserved_margin:
            raise RiskViolation("margin already reserved for replacement order")
        if not math.isfinite(float(amount)) or amount < 0:
            raise ValueError("reserved margin must be finite and non-negative")
        old_amount = self._reserved_margin.get(old_order_id, 0.0)
        available = self.snapshot(marks).available_margin + old_amount
        if amount > available + 1e-9:
            raise RiskViolation("insufficient available margin for replacement order reservation")
        self._reserved_margin.pop(old_order_id, None)
        self._reserved_margin[new_order_id] = float(amount)
        return float(amount)

    def release_margin(self, order_id: str, amount: float | None = None) -> float:
        current = self._reserved_margin.get(order_id, 0.0)
        if amount is None:
            released = current
            self._reserved_margin.pop(order_id, None)
            return released
        if not math.isfinite(float(amount)) or amount < 0 or amount > current + 1e-9:
            raise ValueError("invalid margin release amount")
        remaining = current - amount
        if remaining <= 1e-9:
            self._reserved_margin.pop(order_id, None)
        else:
            self._reserved_margin[order_id] = remaining
        return amount

    def validate_fill(self, fill: SimFill, marks: dict[str, float] | None = None) -> None:
        if fill.quantity <= 0 or not math.isfinite(float(fill.price)) or fill.price <= 0:
            raise ValueError("fill quantity and price must be finite and positive")
        if not math.isfinite(float(fill.fee)) or fill.fee < 0:
            raise ValueError("fill fee must be finite and non-negative")
        marks = dict(marks or {})
        marks.setdefault(fill.instrument, fill.price)
        old = self._positions.get(fill.instrument, Position(fill.instrument))
        signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
        new_qty = old.quantity + signed
        cfg = self.risk_config
        if cfg.max_position_quantity is not None and abs(new_qty) > cfg.max_position_quantity:
            raise RiskViolation("max position quantity exceeded")
        gross, net, _, equity = self._metrics(marks)
        current_notional = abs(old.quantity * marks[fill.instrument])
        projected_notional = abs(new_qty * marks[fill.instrument])
        projected_gross = gross - current_notional + projected_notional
        projected_net = net - old.quantity * marks[fill.instrument] + new_qty * marks[fill.instrument]
        if cfg.max_gross_notional is not None and projected_gross > cfg.max_gross_notional:
            raise RiskViolation("max gross notional exceeded")
        if cfg.max_net_notional is not None and abs(projected_net) > cfg.max_net_notional:
            raise RiskViolation("max net notional exceeded")
        projected_margin = projected_gross * cfg.initial_margin_rate
        own_reservation = self._reserved_margin.get(fill.order_id, 0.0)
        other_reservations = max(0.0, self.reserved_margin - own_reservation)
        reduces_position_risk = old.quantity != 0 and abs(new_qty) < abs(old.quantity) and (old.quantity * new_qty >= 0 or new_qty == 0)
        if not reduces_position_risk and projected_margin > equity - other_reservations + 1e-9:
            raise RiskViolation("insufficient available margin")
        if cfg.max_leverage is not None and equity > 0 and projected_gross / equity > cfg.max_leverage:
            raise RiskViolation("max leverage exceeded")
        if cfg.max_drawdown is not None:
            projected_equity = self.cash - signed * fill.price - fill.fee + projected_net
            projected_drawdown = max(0.0, self._peak_equity - projected_equity)
            if projected_drawdown > cfg.max_drawdown + 1e-9:
                raise RiskViolation("max drawdown exceeded")

    def _validate_atomic_fills(self, fills: tuple[SimFill, ...], marks: dict[str, float]) -> None:
        cfg = self.risk_config
        projected_qty = {instrument: position.quantity for instrument, position in self._positions.items()}
        projected_marks = dict(marks)
        cash_delta = 0.0
        batch_order_ids: set[str] = set()
        for fill in fills:
            if fill.quantity <= 0 or not math.isfinite(float(fill.price)) or fill.price <= 0:
                raise ValueError("fill quantity and price must be finite and positive")
            if not math.isfinite(float(fill.fee)) or fill.fee < 0:
                raise ValueError("fill fee must be finite and non-negative")
            signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
            projected_qty[fill.instrument] = projected_qty.get(fill.instrument, 0) + signed
            projected_marks.setdefault(fill.instrument, fill.price)
            cash_delta += signed * fill.price + fill.fee
            batch_order_ids.add(fill.order_id)

        if not fills:
            return
        for instrument, quantity in projected_qty.items():
            if cfg.max_position_quantity is not None and abs(quantity) > cfg.max_position_quantity:
                raise RiskViolation("max position quantity exceeded")

        gross = net = 0.0
        for instrument, quantity in projected_qty.items():
            if quantity == 0:
                continue
            mark = projected_marks.get(instrument)
            if mark is None:
                position = self._positions.get(instrument)
                mark = position.average_price if position is not None else None
            if mark is None or not math.isfinite(float(mark)) or mark <= 0:
                raise ValueError(f"mark must be finite and positive for {instrument}")
            notional = quantity * mark
            gross += abs(notional)
            net += notional

        projected_equity = self.cash - cash_delta + net
        if not math.isfinite(projected_equity):
            raise ValueError("atomic projected equity must be finite")
        if cfg.max_gross_notional is not None and gross > cfg.max_gross_notional + 1e-9:
            raise RiskViolation("max gross notional exceeded")
        if cfg.max_net_notional is not None and abs(net) > cfg.max_net_notional + 1e-9:
            raise RiskViolation("max net notional exceeded")
        batch_reservation = sum(self._reserved_margin.get(order_id, 0.0) for order_id in batch_order_ids)
        other_reservations = max(0.0, self.reserved_margin - batch_reservation)
        projected_margin = gross * cfg.initial_margin_rate
        if projected_margin > projected_equity - other_reservations + 1e-9:
            raise RiskViolation("insufficient available margin")
        if cfg.max_leverage is not None and projected_equity > 0 and gross / projected_equity > cfg.max_leverage + 1e-9:
            raise RiskViolation("max leverage exceeded")
        if cfg.max_drawdown is not None:
            projected_drawdown = max(0.0, self._peak_equity - projected_equity)
            if projected_drawdown > cfg.max_drawdown + 1e-9:
                raise RiskViolation("max drawdown exceeded")

    def validate_mark_to_market(self, marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        """Validate current marked equity/exposure without mutating portfolio state."""
        snapshot = self.snapshot(marks)
        cfg = self.risk_config
        if cfg.max_gross_notional is not None and snapshot.gross_notional > cfg.max_gross_notional + 1e-9:
            raise RiskViolation("max gross notional exceeded")
        if cfg.max_net_notional is not None and abs(snapshot.net_notional) > cfg.max_net_notional + 1e-9:
            raise RiskViolation("max net notional exceeded")
        if cfg.max_leverage is not None and snapshot.equity > 0 and snapshot.leverage > cfg.max_leverage + 1e-9:
            raise RiskViolation("max leverage exceeded")
        if snapshot.equity + 1e-9 < snapshot.maintenance_margin:
            raise RiskViolation("maintenance margin breached")
        if cfg.max_drawdown is not None and snapshot.drawdown > cfg.max_drawdown + 1e-9:
            raise RiskViolation("max drawdown exceeded")
        return snapshot

    def apply_fill(self, fill: SimFill, marks: dict[str, float] | None = None) -> Position:
        if not getattr(self, "_atomic_applying", False):
            self.validate_fill(fill, marks)
        old = self._positions.get(fill.instrument, Position(fill.instrument))
        signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
        old_qty = old.quantity
        new_qty = old_qty + signed
        realized_delta = 0.0
        realized = old.realized_pnl
        if old_qty == 0 or (old_qty > 0 and signed > 0) or (old_qty < 0 and signed < 0):
            total_abs = abs(old_qty) + abs(signed)
            avg = ((abs(old_qty) * old.average_price) + (abs(signed) * fill.price)) / total_abs
        elif abs(signed) <= abs(old_qty):
            closed = abs(signed)
            direction = 1 if old_qty > 0 else -1
            realized_delta = closed * (fill.price - old.average_price) * direction
            realized += realized_delta
            self._realized_pnl += realized_delta
            avg = old.average_price if new_qty else 0.0
        else:
            closed = abs(old_qty)
            direction = 1 if old_qty > 0 else -1
            realized_delta = closed * (fill.price - old.average_price) * direction
            realized += realized_delta
            self._realized_pnl += realized_delta
            avg = fill.price
        self.cash -= signed * fill.price + fill.fee
        self._fees += fill.fee
        position = Position(fill.instrument, new_qty, avg, realized)
        if new_qty == 0:
            self._positions.pop(fill.instrument, None)
        else:
            self._positions[fill.instrument] = position
        snapshot_marks = dict(marks or {})
        snapshot_marks.setdefault(fill.instrument, fill.price)
        snapshot = self.snapshot(snapshot_marks)
        self._peak_equity = max(self._peak_equity, snapshot.equity)
        self._trades.append(TradeRecord(fill.order_id, fill.instrument, fill.side, fill.quantity, fill.price, fill.quantity * fill.price, fill.fee, realized_delta, self.cash, snapshot.equity, fill.filled_at_ns))
        return position

    def apply_fills_atomic(self, fills: Iterable[SimFill], marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        """Apply a multi-leg fill batch atomically; validate risk against the final batch state."""
        fills = tuple(fills)
        marks = dict(marks or {})
        state = (self.cash, dict(self._positions), self._realized_pnl, self._fees, self._peak_equity, dict(self._reserved_margin), list(self._trades))
        try:
            self._validate_atomic_fills(fills, marks)
            self._atomic_applying = True
            for fill in fills:
                self.apply_fill(fill, marks)
        except Exception:
            self.cash, positions, self._realized_pnl, self._fees, self._peak_equity, reserved, trades = state
            self._positions = positions
            self._reserved_margin = reserved
            self._trades = trades
            raise
        finally:
            self._atomic_applying = False
        return self.snapshot(marks)

    def forced_liquidation(self, fills: Iterable[SimFill], marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        """Atomically apply only position-reducing fills after a maintenance breach."""
        marks = dict(marks or {})
        before = self.snapshot(marks)
        if before.equity + 1e-9 >= before.maintenance_margin:
            raise RiskViolation("forced liquidation not required")
        fills = tuple(fills)
        if not fills:
            raise RiskViolation("forced liquidation requires at least one reducing fill")
        for fill in fills:
            old = self._positions.get(fill.instrument)
            if old is None or old.quantity == 0:
                raise RiskViolation("forced liquidation must reduce an existing position")
            signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
            new_qty = old.quantity + signed
            if abs(new_qty) >= abs(old.quantity) or (old.quantity * new_qty < 0 and new_qty != 0):
                raise RiskViolation("forced liquidation cannot increase or reverse exposure")
        state = (self.cash, dict(self._positions), self._realized_pnl, self._fees, self._peak_equity, dict(self._reserved_margin), list(self._trades))
        try:
            after = self.apply_fills_atomic(fills, marks)
            if after.equity + 1e-9 < after.maintenance_margin:
                raise RiskViolation("forced liquidation did not restore maintenance margin")
        except Exception:
            self.cash, positions, self._realized_pnl, self._fees, self._peak_equity, reserved, trades = state
            self._positions = positions
            self._reserved_margin = reserved
            self._trades = trades
            raise
        return after

    def restore_state(self, state: Mapping[str, Any]) -> None:
        """Restore a durable checkpoint after strict finite-value validation."""
        if not isinstance(state, Mapping):
            raise ValueError("invalid portfolio checkpoint")
        try:
            cash = float(state["cash"])
            realized_pnl = float(state["realized_pnl"])
            fees = float(state["fees"])
            peak_equity = float(state["peak_equity"])
            raw_positions = state.get("positions", [])
            raw_reserved = state.get("reserved_margin", {})
            if not all(math.isfinite(v) for v in (cash, realized_pnl, fees, peak_equity)):
                raise ValueError("portfolio checkpoint contains non-finite values")
            if not isinstance(raw_positions, (list, tuple)) or not isinstance(raw_reserved, Mapping):
                raise ValueError("invalid portfolio checkpoint collections")
            positions: dict[str, Position] = {}
            for raw in raw_positions:
                if not isinstance(raw, Mapping):
                    raise ValueError("invalid portfolio position checkpoint")
                instrument = str(raw["instrument"])
                quantity = raw["quantity"]
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity == 0:
                    raise ValueError("invalid portfolio position checkpoint values")
                average_price = float(raw["average_price"])
                position_realized = float(raw["realized_pnl"])
                if not instrument or not math.isfinite(average_price) or average_price <= 0 or not math.isfinite(position_realized):
                    raise ValueError("invalid portfolio position checkpoint values")
                positions[instrument] = Position(instrument, quantity, average_price, position_realized)
            reserved = {str(order_id): float(amount) for order_id, amount in raw_reserved.items()}
            if any(not order_id or not math.isfinite(amount) or amount < 0 for order_id, amount in reserved.items()):
                raise ValueError("invalid reserved margin checkpoint")
            if cash < 0 or fees < 0 or peak_equity < 0:
                raise ValueError("invalid portfolio checkpoint values")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid portfolio checkpoint") from exc
        self.cash = cash
        self._positions = positions
        self._realized_pnl = realized_pnl
        self._fees = fees
        self._peak_equity = peak_equity
        self._reserved_margin = reserved
        self._trades = []

    def snapshot(self, marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        marks = dict(marks or {})
        gross, net, unrealized, equity = self._metrics(marks)
        cfg = self.risk_config
        initial_margin = gross * cfg.initial_margin_rate
        maintenance = gross * cfg.maintenance_margin_rate
        available = max(0.0, equity - initial_margin - self.reserved_margin)
        leverage = gross / equity if equity > 0 else 0.0
        drawdown = max(0.0, self._peak_equity - equity)
        return PortfolioSnapshot(self.cash, equity, self._realized_pnl, unrealized, tuple(self._positions.values()), gross, net, initial_margin, maintenance, available, leverage, drawdown, self._fees, self.reserved_margin)

    def export_state(self) -> Mapping[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "risk_config": {
                "initial_margin_rate": self.risk_config.initial_margin_rate,
                "maintenance_margin_rate": self.risk_config.maintenance_margin_rate,
                "max_gross_notional": self.risk_config.max_gross_notional,
                "max_net_notional": self.risk_config.max_net_notional,
                "max_leverage": self.risk_config.max_leverage,
                "max_position_quantity": self.risk_config.max_position_quantity,
                "max_drawdown": self.risk_config.max_drawdown,
            },
            "positions": [{"instrument": p.instrument, "quantity": p.quantity, "average_price": p.average_price, "realized_pnl": p.realized_pnl} for p in self._positions.values()],
            "realized_pnl": self._realized_pnl,
            "fees": self._fees,
            "peak_equity": self._peak_equity,
            "reserved_margin": dict(self._reserved_margin),
        }

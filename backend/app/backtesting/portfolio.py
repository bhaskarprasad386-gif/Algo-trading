"""Capital, margin, position and P&L accounting for universal backtests."""

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.config import DEFAULT_PAPER_CAPITAL
from app.backtesting.execution import ExecutionSide, SimFill


class RiskViolation(ValueError):
    """Raised when a fill would breach configured portfolio risk limits."""


@dataclass(frozen=True)
class RiskConfig:
    initial_margin_rate: float = 1.0
    maintenance_margin_rate: float = 1.0
    max_gross_notional: float | None = None
    max_net_notional: float | None = None
    max_leverage: float | None = None
    max_position_quantity: int | None = None
    max_drawdown: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.initial_margin_rate <= 1:
            raise ValueError("initial_margin_rate must be in (0, 1]")
        if not 0 < self.maintenance_margin_rate <= 1:
            raise ValueError("maintenance_margin_rate must be in (0, 1]")
        if self.maintenance_margin_rate > self.initial_margin_rate:
            raise ValueError("maintenance_margin_rate cannot exceed initial_margin_rate")
        for name in ("max_gross_notional", "max_net_notional", "max_leverage", "max_drawdown"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.max_position_quantity is not None and self.max_position_quantity <= 0:
            raise ValueError("max_position_quantity must be positive")


@dataclass(frozen=True)
class Position:
    instrument: str
    quantity: int = 0
    average_price: float = 0.0
    realized_pnl: float = 0.0


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


class Portfolio:
    """Deterministic capital, margin, risk and P&L accounting."""

    def __init__(self, initial_cash: float = DEFAULT_PAPER_CAPITAL, risk_config: RiskConfig | None = None) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.risk_config = risk_config or RiskConfig()
        self._positions: dict[str, Position] = {}
        self._realized_pnl = 0.0
        self._fees = 0.0
        self._peak_equity = float(initial_cash)

    def _metrics(self, marks: dict[str, float]) -> tuple[float, float, float, float]:
        gross = net = unrealized = 0.0
        for p in self._positions.values():
            mark = marks.get(p.instrument, p.average_price)
            if mark <= 0:
                raise ValueError(f"mark must be positive for {p.instrument}")
            notional = p.quantity * mark
            gross += abs(notional)
            net += notional
            unrealized += p.quantity * (mark - p.average_price)
        return gross, net, unrealized, self.cash + net

    def validate_fill(self, fill: SimFill, marks: dict[str, float] | None = None) -> None:
        if fill.quantity <= 0 or fill.price <= 0:
            raise ValueError("fill quantity and price must be positive")
        marks = dict(marks or {})
        marks.setdefault(fill.instrument, fill.price)
        old = self._positions.get(fill.instrument, Position(fill.instrument))
        signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
        new_qty = old.quantity + signed
        cfg = self.risk_config
        if cfg.max_position_quantity is not None and abs(new_qty) > cfg.max_position_quantity:
            raise RiskViolation("max position quantity exceeded")
        gross, net, unrealized, equity = self._metrics(marks)
        current_notional = abs(old.quantity * marks[fill.instrument])
        projected_notional = abs(new_qty * marks[fill.instrument])
        projected_gross = gross - current_notional + projected_notional
        projected_net = net - old.quantity * marks[fill.instrument] + new_qty * marks[fill.instrument]
        if cfg.max_gross_notional is not None and projected_gross > cfg.max_gross_notional:
            raise RiskViolation("max gross notional exceeded")
        if cfg.max_net_notional is not None and abs(projected_net) > cfg.max_net_notional:
            raise RiskViolation("max net notional exceeded")
        projected_margin = projected_gross * cfg.initial_margin_rate
        if projected_margin > equity + 1e-9:
            raise RiskViolation("insufficient available margin")
        if cfg.max_leverage is not None and equity > 0 and projected_gross / equity > cfg.max_leverage:
            raise RiskViolation("max leverage exceeded")

    def apply_fill(self, fill: SimFill, marks: dict[str, float] | None = None) -> Position:
        self.validate_fill(fill, marks)
        old = self._positions.get(fill.instrument, Position(fill.instrument))
        signed = fill.quantity if fill.side == ExecutionSide.BUY else -fill.quantity
        old_qty = old.quantity
        new_qty = old_qty + signed
        realized = old.realized_pnl
        if old_qty == 0 or (old_qty > 0 and signed > 0) or (old_qty < 0 and signed < 0):
            total_abs = abs(old_qty) + abs(signed)
            avg = ((abs(old_qty) * old.average_price) + (abs(signed) * fill.price)) / total_abs
        elif abs(signed) <= abs(old_qty):
            closed = abs(signed)
            direction = 1 if old_qty > 0 else -1
            pnl = closed * (fill.price - old.average_price) * direction
            realized += pnl
            self._realized_pnl += pnl
            avg = old.average_price if new_qty else 0.0
        else:
            closed = abs(old_qty)
            direction = 1 if old_qty > 0 else -1
            pnl = closed * (fill.price - old.average_price) * direction
            realized += pnl
            self._realized_pnl += pnl
            avg = fill.price
        self.cash -= signed * fill.price + fill.fee
        self._fees += fill.fee
        position = Position(fill.instrument, new_qty, avg, realized)
        if new_qty == 0:
            self._positions.pop(fill.instrument, None)
        else:
            self._positions[fill.instrument] = position
        snapshot = self.snapshot(marks)
        self._peak_equity = max(self._peak_equity, snapshot.equity)
        if self.risk_config.max_drawdown is not None and snapshot.drawdown > self.risk_config.max_drawdown:
            raise RiskViolation("max drawdown exceeded")
        return position

    def snapshot(self, marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        marks = dict(marks or {})
        gross, net, unrealized, equity = self._metrics(marks)
        cfg = self.risk_config
        initial_margin = gross * cfg.initial_margin_rate
        maintenance = gross * cfg.maintenance_margin_rate
        available = max(0.0, equity - initial_margin)
        leverage = gross / equity if equity > 0 else 0.0
        drawdown = max(0.0, self._peak_equity - equity)
        return PortfolioSnapshot(
            cash=self.cash,
            equity=equity,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized,
            positions=tuple(self._positions.values()),
            gross_notional=gross,
            net_notional=net,
            initial_margin=initial_margin,
            maintenance_margin=maintenance,
            available_margin=available,
            leverage=leverage,
            drawdown=drawdown,
            fees=self._fees,
        )

    def apply_fills(self, fills: Iterable[SimFill], marks: dict[str, float] | None = None) -> PortfolioSnapshot:
        for fill in fills:
            self.apply_fill(fill, marks)
        return self.snapshot(marks)

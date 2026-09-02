"""Paper-execution API boundary.

This route intentionally exposes paper execution only. Live broker execution stays
behind the existing confirmation, idempotency, and safety gates.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.execution.dual_engine import DualExecutionEngine, ExecutionConfig, ExecutionMode, Fill

router = APIRouter(prefix="/api/v1/execution", tags=["Execution"])


class PaperEntryRequest(BaseModel):
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)


class PaperExitRequest(BaseModel):
    price: float = Field(..., gt=0)


_paper_position: dict | None = None


def _paper_fill(mode: ExecutionMode, price: float, quantity: float) -> Fill:
    if mode is not ExecutionMode.PAPER:
        raise RuntimeError("paper endpoint cannot execute live orders")
    return Fill(price=price, quantity=quantity)


@router.post("/paper/entry")
def paper_entry(request: PaperEntryRequest):
    """Simulate one paper entry and store the active paper position."""
    global _paper_position
    engine = DualExecutionEngine(
        _paper_fill,
        config=ExecutionConfig(
            stop_loss_pct=request.stop_loss_pct,
            target_pct=request.target_pct,
        ),
    )
    fill = engine.enter(request.price, request.quantity)
    state = engine.paper
    _paper_position = {
        "mode": state.mode.value,
        "quantity": state.quantity,
        "entry_price": state.entry_price,
        "stop_loss": state.stop_loss,
        "target": state.target,
    }
    return {"status": "success", "position": _paper_position}


@router.get("/paper/position")
def paper_position():
    """Return the current in-memory paper position, if any."""
    if _paper_position is None:
        return {"status": "flat", "position": None}
    return {"status": "active", "position": _paper_position}


@router.post("/paper/exit")
def paper_exit(request: PaperExitRequest):
    """Close the active paper position and return realized P&L."""
    global _paper_position
    if _paper_position is None:
        return {"status": "flat", "position": None, "pnl": 0.0}

    entry_price = float(_paper_position["entry_price"])
    quantity = float(_paper_position["quantity"])
    pnl = round((request.price - entry_price) * quantity, 8)
    result = {
        "status": "closed",
        "entry_price": entry_price,
        "exit_price": request.price,
        "quantity": quantity,
        "pnl": pnl,
    }
    _paper_position = None
    return result

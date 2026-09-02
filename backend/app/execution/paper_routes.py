"""Paper-execution API boundary.

This route intentionally exposes paper entry only. Live broker execution stays
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


def _paper_fill(mode: ExecutionMode, price: float, quantity: float) -> Fill:
    if mode is not ExecutionMode.PAPER:
        raise RuntimeError("paper endpoint cannot execute live orders")
    return Fill(price=price, quantity=quantity)


@router.post("/paper/entry")
def paper_entry(request: PaperEntryRequest):
    """Simulate one paper entry and calculate SL/target from the fill price."""
    engine = DualExecutionEngine(
        _paper_fill,
        config=ExecutionConfig(
            stop_loss_pct=request.stop_loss_pct,
            target_pct=request.target_pct,
        ),
    )
    fill = engine.enter(request.price, request.quantity)
    state = engine.paper
    return {
        "status": "success",
        "mode": state.mode.value,
        "fill": {"price": fill.price, "quantity": fill.quantity},
        "entry_price": state.entry_price,
        "stop_loss": state.stop_loss,
        "target": state.target,
    }

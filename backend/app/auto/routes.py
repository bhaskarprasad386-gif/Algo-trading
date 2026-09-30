from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.auto.paper_auto import AutoSignal, GlobalPaperAutoService, position_payload

router = APIRouter(prefix="/api/v1/auto", tags=["Global Paper Auto"])
service = GlobalPaperAutoService()

class AutoControlRequest(BaseModel):
    enabled: bool

class AutoSignalRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=128)
    entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)
    quantity: int = Field(default=1, gt=0)
    expiry: str | None = None
    metadata: dict = Field(default_factory=dict)

@router.get("/status")
def status(db: Session = Depends(get_db)):
    rows = service.positions(db)
    return {
        "live_orders": False,
        "positions": [position_payload(row) for row in rows],
        "count": len(rows),
    }

@router.get("/strategies/{strategy_id}")
def strategy_status(strategy_id: str, db: Session = Depends(get_db)):
    return {
        "strategy": strategy_id.strip().lower(),
        "auto_enabled": service.is_enabled(db, strategy_id),
        "live_orders": False,
        "positions": [position_payload(row) for row in service.positions(db, strategy_id)],
    }

@router.post("/strategies/{strategy_id}")
def set_strategy_auto(strategy_id: str, request: AutoControlRequest, db: Session = Depends(get_db)):
    row = service.set_enabled(db, strategy_id, request.enabled)
    return {
        "status": "success",
        "strategy": row.strategy_id,
        "auto_enabled": bool(row.enabled),
        "execution_mode": "PAPER",
        "live_orders": False,
    }

@router.post("/signal")
def execute_signal(request: AutoSignalRequest, db: Session = Depends(get_db)):
    position = service.qualify_and_enter(db, AutoSignal(**request.model_dump()))
    return {
        "status": "entered" if position else "skipped",
        "reason": None if position else "GLOBAL_AUTO_OFF",
        "live_orders": False,
        "position": None if position is None else position_payload(position),
    }

@router.post("/positions/{position_id}/mark")
def mark(position_id: int, current_price: float = Field(gt=0), db: Session = Depends(get_db)):
    try:
        return {"status": "success", "position": position_payload(service.update_mark(db, position_id, current_price))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/positions/{position_id}/close")
def close(position_id: int, db: Session = Depends(get_db)):
    try:
        return {"status": "success", "position": position_payload(service.close(db, position_id))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

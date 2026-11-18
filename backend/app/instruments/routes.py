from fastapi import APIRouter, HTTPException
from app.core.logger import app_logger
from app.instruments.manager import InstrumentManager

router = APIRouter(
    prefix="/api/v1/instruments",
    tags=["Instruments"],
)

@router.post("/sync")
def sync_instrument_master():
    """Download and sync Angel One instrument master list into database."""
    try:
        app_logger.info("Triggered instrument master sync API.")
        manager = InstrumentManager()
        result = manager.sync_instruments()
        return {"status": "success", "message": "Instruments synced successfully", "data": result}
    except Exception as e:
        app_logger.error(f"Failed to sync instruments via API: {e}")
        raise HTTPException(status_code=500, detail=str(e))

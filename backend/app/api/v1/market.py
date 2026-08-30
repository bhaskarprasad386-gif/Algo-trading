from fastapi import APIRouter

router = APIRouter(prefix="/market-data", tags=["Market Data"])

@router.get("/ltp")
def get_ltp():
    return {"status": "success", "message": "LTP route"}

@router.get("/historical")
def get_historical():
    return {"status": "success", "message": "Historical data route"}

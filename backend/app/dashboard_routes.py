from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["dashboard"])
DASHBOARD_FILE = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"


@router.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_FILE, media_type="text/html")

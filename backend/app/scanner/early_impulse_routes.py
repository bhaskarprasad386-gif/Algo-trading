"""Read-only status and pure score evaluation routes for Early Impulse Pro."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.scanner.early_impulse_engine import evaluate_early_impulse

router = APIRouter(prefix="/api/v1/early-impulse", tags=["early-impulse"])


class EngineScores(BaseModel):
    absorption: float = Field(default=0, ge=0, le=1)
    synthetic_spot: float = Field(default=0, ge=0, le=1)
    failed_auction: float = Field(default=0, ge=0, le=1)
    market_impact: float = Field(default=0, ge=0, le=1)
    cross_instrument: float = Field(default=0, ge=0, le=1)


class EvaluationRequest(BaseModel):
    direction: str = "NEUTRAL"
    engines: EngineScores = Field(default_factory=EngineScores)
    data_valid: bool = False
    spread_ok: bool = False
    entry_not_extended: bool = False
    invalidation_defined: bool = False
    no_strong_opposite: bool = False


@router.get("/status")
def status() -> dict:
    """Explicitly report that feed integration is not active yet."""
    return {
        "strategy": "EARLY_IMPULSE_PRO",
        "status": "WAITING_FOR_FEED_INTEGRATION",
        "feed_connected": False,
        "scanner_active": False,
        "signal_count": 0,
        "latest_signal": None,
        "market_regime": "UNKNOWN",
        "order_routing_enabled": False,
        "iv_scanner_dependency": False,
        "engines": [
            {"id": "absorption", "name": "Absorption Detection", "weight": 30, "status": "WAITING"},
            {"id": "synthetic_spot", "name": "Synthetic Future vs Spot", "weight": 20, "status": "WAITING"},
            {"id": "failed_auction", "name": "Failed Auction / Rejection", "weight": 15, "status": "WAITING"},
            {"id": "market_impact", "name": "Market Impact Efficiency", "weight": 20, "status": "WAITING"},
            {"id": "cross_instrument", "name": "Cross-Instrument Confirmation", "weight": 15, "status": "WAITING"},
        ],
        "notes": [
            "Live feed subscription and runner are not wired in this initial page batch.",
            "No market signal is fabricated; this route is read-only and scanner-only.",
        ],
    }


@router.post("/evaluate")
def evaluate(request: EvaluationRequest) -> dict:
    """Score a normalized sample. This endpoint never routes broker orders."""
    return evaluate_early_impulse(request.model_dump())

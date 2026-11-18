from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class QuoteResponse(BaseModel):
    symbol: str
    exchange: str
    bidPrice: float
    askPrice: float
    ltp: float
    spread: float


@router.get("/quote", response_model=QuoteResponse)
def get_market_quote(symbol: str):
    """Return a quote only when a real market-data provider is wired in."""
    normalized = symbol.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="symbol is required")
    raise HTTPException(
        status_code=503,
        detail="Live market quote provider is not configured; refusing to return fabricated prices.",
    )

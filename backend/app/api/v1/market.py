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
    # यहाँ वास्तविक डेटा या स्मार्टएपी (SmartAPI) का इंटीग्रेशन किया जा सकता है
    # अभी टेस्टिंग के लिए डमी बिड-आस्क वैल्यू रिटर्न की जा रही है
    dummy_bid = 2450.50
    dummy_ask = 2451.25
    dummy_ltp = 2451.00
    spread_value = round(dummy_ask - dummy_bid, 2)
    
    return {
        "symbol": symbol.upper(),
        "exchange": "NSE",
        "bidPrice": dummy_bid,
        "askPrice": dummy_ask,
        "ltp": dummy_ltp,
        "spread": spread_value
    }

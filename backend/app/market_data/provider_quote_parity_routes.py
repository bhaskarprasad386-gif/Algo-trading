"""Read-only API for opt-in shadow quote-parity diagnostics."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore

router = APIRouter(prefix="/api/v1/market-data/provider-quote-parity", tags=["market-data"])
_store: SQLiteQuoteParityStore | None = None


def configure_store(store: SQLiteQuoteParityStore | None) -> None:
    global _store
    _store = store


@router.get("")
def read_provider_quote_parity(
    contract_key: str = Query(..., min_length=2, max_length=512),
    limit: int = Query(100, ge=1, le=1000),
):
    """Return persisted derived parity diagnostics; never returns executable quotes."""
    store = _store
    if store is None:
        raise HTTPException(status_code=503, detail="provider quote parity diagnostics are disabled")
    try:
        return {"items": store.latest(contract_key=contract_key, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

"""Angel One broker-native NIFTY option-chain snapshot persistence.

IV is read only from Angel One Option Greeks; this module never calculates IV.
FULL quotes supply LTP/OI/volume/depth where the broker returns them.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.market_data.client import MarketDataClient
from app.models.iv_option_observation import IVOptionObservation

IST = ZoneInfo("Asia/Kolkata")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("fetched", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _greek_key(row: dict[str, Any]) -> tuple[float, str] | None:
    strike = _number(row.get("strikePrice", row.get("strike")))
    side = str(row.get("optionType", row.get("option_type", ""))).strip().upper()
    if strike is None or strike <= 0 or side not in {"CE", "PE"}:
        return None
    return round(strike, 4), side


def _quote_depth(row: dict[str, Any]) -> tuple[float | None, float | None]:
    depth = row.get("depth")
    if not isinstance(depth, dict):
        depth = row.get("best5Data")
    if not isinstance(depth, dict):
        return None, None
    buys = depth.get("buy") or depth.get("buyDepth") or []
    sells = depth.get("sell") or depth.get("sellDepth") or []
    bid = _number(buys[0].get("price")) if buys and isinstance(buys[0], dict) else None
    ask = _number(sells[0].get("price")) if sells and isinstance(sells[0], dict) else None
    return bid, ask


def capture_angelone_option_snapshot(
    *,
    universe: Any,
    spot_snapshot: Any | None,
    client: MarketDataClient | None = None,
    session_factory: Callable[[], Any],
    captured_at_ns: int | None = None,
) -> dict[str, Any]:
    """Fetch Angel One Greeks + FULL quote and persist the exact resolved universe."""
    import time

    broker = client or MarketDataClient()
    captured_ns = int(captured_at_ns or time.time_ns())
    expiry = datetime.strptime(str(universe.expiry), "%Y-%m-%d").strftime("%d%b%Y").upper()
    greeks_response = broker.option_greeks(universe.underlying, expiry)
    greek_rows = _rows(greeks_response.get("data"))
    greek_by_key: dict[tuple[float, str], dict[str, Any]] = {}
    for row in greek_rows:
        key = _greek_key(row)
        if key is not None:
            greek_by_key[key] = row

    instruments = [{"symboltoken": str(contract.token)} for contract in universe.contracts]
    quote_error = None
    quote_rows: list[dict[str, Any]] = []
    try:
        quote_response = broker.quote_many(universe.option_exchange, instruments)
        quote_data = quote_response.get("data")
        quote_rows = _rows(quote_data)
    except Exception as exc:
        # Keep valid broker IV even if the separate quote endpoint is temporarily
        # unavailable; missing quote fields remain null and are never fabricated.
        quote_error = f"{type(exc).__name__}: {exc}"

    if not greek_rows and not quote_rows:
        raise RuntimeError("Angel One returned no option Greeks and no FULL quote rows; no empty snapshot was saved")

    quote_by_token: dict[str, dict[str, Any]] = {}
    for row in quote_rows:
        token = str(row.get("symbolToken", row.get("symboltoken", ""))).strip()
        if token:
            quote_by_token[token] = row

    spot_price = _number(getattr(spot_snapshot, "price", None))
    spot_timestamp_ns = getattr(spot_snapshot, "timestamp_ns", None)
    if not isinstance(spot_timestamp_ns, int) or isinstance(spot_timestamp_ns, bool) or spot_timestamp_ns <= 0:
        spot_timestamp_ns = 0

    db = session_factory()
    saved = 0
    iv_count = 0
    quote_count = 0
    try:
        for contract in universe.contracts:
            greek = greek_by_key.get((round(float(contract.strike), 4), str(contract.option_type).upper()), {})
            quote = quote_by_token.get(str(contract.token), {})
            iv = _number(greek.get("impliedVolatility"))
            # FULL quote is authoritative for traded price, OI and volume.
            ltp = _number(quote.get("ltp"))
            oi = _integer(quote.get("opnInterest", quote.get("openInterest", quote.get("oi"))))
            volume = _integer(quote.get("tradeVolume", quote.get("volume")))
            if volume is None:
                volume = _integer(greek.get("tradeVolume"))
            bid, ask = _quote_depth(quote)
            mark = (bid + ask) / 2.0 if bid is not None and ask is not None and ask >= bid else ltp
            db.add(IVOptionObservation(
                token=str(contract.token),
                underlying=str(universe.underlying),
                expiry=str(universe.expiry),
                strike=float(contract.strike),
                option_type=str(contract.option_type).upper(),
                # Option Greeks does not expose an exchange tick timestamp.
                # This is the broker-response capture time, not a fabricated tick.
                source_timestamp_ns=captured_ns,
                spot_timestamp_ns=spot_timestamp_ns,
                spot_price=spot_price,
                sampled_at_ns=captured_ns,
                iv=iv,
                iv_source="ANGEL_ONE",
                oi=oi,
                bid=bid,
                ask=ask,
                ltp=ltp,
                mark=mark,
                volume=volume,
            ))
            saved += 1
            iv_count += int(iv is not None)
            quote_count += int(bool(quote))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "status": "ok",
        "source": "ANGEL_ONE_OPTION_GREEKS_AND_FULL_QUOTE",
        "underlying": universe.underlying,
        "expiry": universe.expiry,
        "expiry_type": universe.expiry_type,
        "captured_at": datetime.fromtimestamp(captured_ns / 1_000_000_000, tz=IST).isoformat(),
        "captured_at_ns": captured_ns,
        "expected_contracts": len(universe.contracts),
        "saved_contracts": saved,
        "iv_received_contracts": iv_count,
        "full_quote_received_contracts": quote_count,
        "missing_iv_contracts": saved - iv_count,
        "quote_error": quote_error,
        "orders_enabled": False,
    }

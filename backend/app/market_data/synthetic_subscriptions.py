"""Build concrete live synthetic subscriptions from Angel One instrument master."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.backtesting.arbitrage_scan_policy import ScanPolicy, enumerate_synthetic_strikes
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_stream import SyntheticSubscription

@dataclass(frozen=True)
class SyntheticContractSelection:
    subscriptions: tuple[SyntheticSubscription, ...]
    expiry: str
    atm_strike: float

def _expiry_key(value: object) -> date:
    text = str(value or "").strip()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try: return datetime.strptime(text.upper(), fmt).date()
        except ValueError: pass
    raise ValueError(f"invalid Angel One expiry: {text!r}")

def _strike(value: object) -> float:
    return float(value) / 100.0

def select_synthetic_contracts(master: InstrumentMaster, *, underlying: str,
                               instrument_class: str, atm_strike: float,
                               expiry: str | None = None,
                               allowed_stock_symbols: frozenset[str] = frozenset(),
                               policy: ScanPolicy | None = None,
                               exchange: str | None = None) -> SyntheticContractSelection:
    cls = instrument_class.strip().upper()
    symbol = underlying.strip().upper()
    if cls not in {"STOCK", "INDEX"}:
        raise ValueError("synthetic scanner supports only STOCK and INDEX")
    if cls == "STOCK" and symbol not in {s.strip().upper() for s in allowed_stock_symbols}:
        raise ValueError("stock is outside the configured top-15 liquid stock universe")

    expected_exchange = exchange.upper() if exchange else ("MCX" if cls == "COMMODITY" else "NFO")
    future_type = {"STOCK": "FUTSTK", "INDEX": "FUTIDX", "COMMODITY": "FUTCOM"}[cls]
    option_types = {"STOCK": {"OPTSTK"}, "INDEX": {"OPTIDX"}, "COMMODITY": {"OPTFUT"}}[cls]
    if cls == "INDEX" and exchange is None:
        available_segments = {
            str(item.get("exch_seg","")).upper()
            for item in master.instruments
            if str(item.get("name","")).strip().upper() == symbol
            and str(item.get("instrumenttype","")).upper() == future_type
        }
        if "NFO" not in available_segments and "BFO" in available_segments:
            expected_exchange = "BFO"
    instruments = [
        item for item in master.instruments
        if str(item.get("exch_seg","")).upper() == expected_exchange
        and str(item.get("name","")).strip().upper() == symbol
    ]
    futures = [x for x in instruments if str(x.get("instrumenttype","")).upper() == future_type]
    options = [x for x in instruments if str(x.get("instrumenttype","")).upper() in option_types and str(x.get("symbol","")).strip()]
    if not futures: raise LookupError(f"no {future_type} contract found for {expected_exchange}:{symbol}")
    if not options: raise LookupError(f"no option contracts found for {expected_exchange}:{symbol}")

    future_rows = sorted(
        [x for x in futures if _expiry_key(x.get("expiry")) >= datetime.now(ZoneInfo("Asia/Kolkata")).date()],
        key=lambda x: (_expiry_key(x.get("expiry")), str(x.get("token","")))
    )
    if not future_rows: raise LookupError(f"no current/near {future_type} contracts for {symbol}")
    target_expiry = _expiry_key(expiry) if expiry else _expiry_key(future_rows[0].get("expiry"))
    # Pair the exact future month selected by the monthly future/option expiry
    # resolver. Previously the selector always chose the nearest future first,
    # then rejected a valid later common expiry when the nearest future had no
    # matching option chain. If no future exists for the requested expiry, fail
    # closed rather than silently switching months.
    matching_futures = [x for x in future_rows if _expiry_key(x.get("expiry")) == target_expiry]
    if not matching_futures:
        nearest_expiry = _expiry_key(future_rows[0].get("expiry"))
        raise LookupError(
            f"synthetic option expiry {target_expiry} does not match "
            f"available nearest future expiry {nearest_expiry} for {symbol}"
        )
    current = matching_futures[0]
    current_future_expiry = _expiry_key(current.get("expiry"))
    option_expiries = (current_future_expiry,)
    options_by_expiry = {
        exp: [x for x in options if _expiry_key(x.get("expiry")) == exp]
        for exp in option_expiries
    }
    if not any(options_by_expiry.values()):
        raise LookupError(f"no options for {symbol} {target_expiry}")

    future_token = str(current.get("token", "")).strip()
    future_symbol = str(current.get("symbol", "")).strip()
    if not future_token or not future_symbol:
        raise LookupError(f"current future has incomplete token/symbol metadata for {symbol}")

    subscriptions = [SyntheticSubscription(
        exchange_type={"NFO":2,"BFO":4,"MCX":5}[expected_exchange],
        token=future_token,
        symbol=future_symbol, underlying=symbol,
        instrument_class=cls, expiry=str(current.get("expiry","")).strip(),
        lot_size=int(float(current.get("lotsize",0) or 0)) or None, contract_role="CURRENT")]
    for option_expiry, expiry_options in options_by_expiry.items():
        if not expiry_options:
            continue
        # A synthetic strike is executable only if both CE and PE contracts
        # exist. Do not subscribe a one-sided strike that can never form a pair.
        options_by_strike: dict[float, dict[str, dict]] = {}
        for item in expiry_options:
            option_symbol = str(item.get("symbol", "")).strip()
            option_type = option_symbol[-2:].upper()
            token = str(item.get("token", "")).strip()
            if option_type not in {"CE", "PE"} or not token:
                continue
            strike = _strike(item.get("strike"))
            options_by_strike.setdefault(strike, {})[option_type] = item
        actual_strikes = sorted(
            strike for strike, legs in options_by_strike.items()
            if {"CE", "PE"}.issubset(legs)
        )
        if not actual_strikes:
            continue
        selected = enumerate_synthetic_strikes(actual_strikes, atm_strike=float(atm_strike),
                                               instrument_class=cls, policy=policy or ScanPolicy())
        selected_strikes = {strike for strike, _, _ in selected}
        for strike in sorted(selected_strikes):
            for option_type in ("CE", "PE"):
                item = options_by_strike[strike][option_type]
                subscriptions.append(SyntheticSubscription(
                    exchange_type={"NFO":2,"BFO":4,"MCX":5}[expected_exchange],
                    token=str(item.get("token","")).strip(),
                    symbol=str(item.get("symbol","")).strip(), underlying=symbol,
                    instrument_class=cls, expiry=str(item.get("expiry","")).strip(),
                    option_type=option_type, strike=strike,
                    lot_size=int(float(item.get("lotsize",0) or 0)) or None,
                    contract_role="OPTION"))
    if len(subscriptions) < 3: raise LookupError("selected live chain has no complete CE/PE strike pairs")
    return SyntheticContractSelection(tuple(subscriptions), str(current.get("expiry","")).strip(), float(atm_strike))

__all__=["SyntheticContractSelection","select_synthetic_contracts"]
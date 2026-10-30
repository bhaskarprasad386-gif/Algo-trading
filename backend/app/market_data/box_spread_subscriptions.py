"""Concrete Angel One option subscriptions for the locked Box Spread chain."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_stream import SyntheticSubscription

BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})

def _expiry(value):
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try: return datetime.strptime(text, fmt).date()
        except ValueError: pass
    raise ValueError(f"invalid expiry: {value!r}")

def _strike(value): return float(value) / 100.0

@dataclass(frozen=True)
class BoxContractSelection:
    subscriptions: tuple[SyntheticSubscription, ...]
    expiry: str
    atm_strike: float

def select_box_contracts(master: InstrumentMaster, *, underlying: str, instrument_class: str,
                         atm_strike: float, expiry: str | None = None,
                         allowed_stock_symbols: frozenset[str] = frozenset(),
                         policy: ScanPolicy | None = None,
                         required_strikes: tuple[float, ...] = ()) -> BoxContractSelection:
    symbol, cls = underlying.strip().upper(), instrument_class.strip().upper()
    if cls not in {"STOCK", "INDEX"}: raise ValueError("instrument_class must be STOCK or INDEX")
    if cls == "STOCK" and symbol not in {s.strip().upper() for s in allowed_stock_symbols}:
        raise ValueError("stock is outside the configured Box Spread stock universe")
    exchange_segment = "BFO" if cls == "INDEX" and symbol in BSE_INDEX_SYMBOLS else "NFO"
    exchange_type = 4 if exchange_segment == "BFO" else 2
    rows = [x for x in master.instruments if str(x.get("exch_seg","")).upper()==exchange_segment and str(x.get("name","")).strip().upper()==symbol]
    options = [x for x in rows if str(x.get("instrumenttype","")).upper() in {"OPTSTK","OPTIDX"}]
    if not options: raise LookupError(f"no option contracts found for {symbol}")
    if expiry:
        target = _expiry(expiry)
    else:
        option_expiries = []
        for item in options:
            try:
                option_expiries.append(_expiry(item.get("expiry")))
            except ValueError:
                continue
        if not option_expiries:
            raise LookupError(f"no valid option expiry found for {symbol}")
        target = min(option_expiries)
    selected_options = []
    for item in options:
        try:
            if _expiry(item.get("expiry")) == target:
                selected_options.append(item)
        except ValueError:
            continue
    options = selected_options
    if not options: raise LookupError(f"no options for {symbol} {target}")
    strikes = sorted({_strike(x.get("strike")) for x in options})
    # Keep Box Spread on the exact option window also subscribed by Synthetic:
    # stocks use 7 actual listed strikes either side; NIFTY/SENSEX use 15.
    ordered = sorted(strikes)
    atm_index = min(range(len(ordered)), key=lambda i: abs(ordered[i] - float(atm_strike)))
    radius = 7 if cls == "STOCK" else 15
    selected = set(ordered[max(0, atm_index - radius):atm_index + radius + 1])
    required = {float(strike) for strike in required_strikes}
    missing_required = required.difference(strikes)
    if missing_required:
        raise LookupError(f"held position strikes unavailable for {symbol} {target}: {sorted(missing_required)}")
    selected.update(required)
    rows_by_key = {(str(x.get("symbol","")).strip()[-2:].upper(), _strike(x.get("strike"))): x for x in options}
    subscriptions = []
    for strike in sorted(selected):
        for suffix in ("CE","PE"):
            item = rows_by_key.get((suffix, strike))
            if not item: continue
            token = str(item.get("token","")).strip()
            if not token: continue
            subscriptions.append(SyntheticSubscription(
                exchange_type, token, str(item.get("symbol","")).strip(), symbol, cls,
                str(item.get("expiry","")).strip(), suffix, strike,
                int(float(item.get("lotsize",0) or 0)) or None
            ))
    if not subscriptions: raise LookupError("selected box chain has no concrete option contracts")
    return BoxContractSelection(tuple(subscriptions), str(target), float(atm_strike))

__all__=["BoxContractSelection","select_box_contracts"]

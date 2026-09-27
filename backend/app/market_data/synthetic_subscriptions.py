"""Build concrete synthetic-arbitrage WebSocket subscriptions from Angel One master.

No strike/token is fabricated here. The master is the only source of contract
identity; ScanPolicy decides the locked INDEX +/-15 / STOCK +/-5 radius.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

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
        try:
            from datetime import datetime
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid Angel One expiry: {text!r}")


def _strike(value: object) -> float:
    number = float(value)
    # Angel One option-master strikes are commonly stored in paise.
    return number / 100.0 if abs(number) >= 10000 else number


def select_synthetic_contracts(
    master: InstrumentMaster,
    *,
    underlying: str,
    instrument_class: str,
    atm_strike: float,
    expiry: str | None = None,
    allowed_stock_symbols: frozenset[str] = frozenset(),
    policy: ScanPolicy | None = None,
    exchange: str = "NFO",
) -> SyntheticContractSelection:
    """Select one concrete future plus actual option strikes for live recording."""
    cls = instrument_class.strip().upper()
    symbol = underlying.strip().upper()
    if cls not in {"STOCK", "INDEX"}:
        raise ValueError("instrument_class must be STOCK or INDEX")
    if cls == "STOCK" and symbol not in {s.strip().upper() for s in allowed_stock_symbols}:
        raise ValueError("stock is outside the configured NIFTY-50 universe")

    instruments = [
        item for item in master.instruments
        if str(item.get("exch_seg", "")).upper() == exchange.upper()
        and str(item.get("name", "")).strip().upper() == symbol
    ]
    future_type = "FUTSTK" if cls == "STOCK" else "FUTIDX"
    option_type = {"OPTSTK", "OPTIDX"}

    futures = [
        item for item in instruments
        if str(item.get("instrumenttype", "")).upper() == future_type
    ]
    options = [
        item for item in instruments
        if str(item.get("instrumenttype", "")).upper() in option_type
        and str(item.get("symbol", "")).strip()
    ]
    if not futures:
        raise LookupError(f"no {future_type} contract found for {symbol}")
    if not options:
        raise LookupError(f"no option contracts found for {symbol}")

    target_expiry = _expiry_key(expiry) if expiry else min(_expiry_key(x.get("expiry")) for x in futures)
    futures = [x for x in futures if _expiry_key(x.get("expiry")) == target_expiry]
    options = [x for x in options if _expiry_key(x.get("expiry")) == target_expiry]
    if not futures or not options:
        raise LookupError(f"no matching future/option contracts for {symbol} {target_expiry}")

    actual_strikes = sorted({_strike(x.get("strike")) for x in options})
    selected = enumerate_synthetic_strikes(
        actual_strikes,
        atm_strike=float(atm_strike),
        instrument_class=cls,
        policy=policy or ScanPolicy(),
    )
    selected_strikes = {strike for strike, _, _ in selected}
    selected_options = [
        x for x in options if _strike(x.get("strike")) in selected_strikes
    ]

    future = sorted(
        futures,
        key=lambda x: (str(x.get("expiry", "")), str(x.get("token", ""))),
    )[0]

    subscriptions = [
        SyntheticSubscription(
            exchange_type=2,
            token=str(future.get("token", "")).strip(),
            symbol=str(future.get("symbol", "")).strip(),
            underlying=symbol,
            instrument_class=cls,
            expiry=str(future.get("expiry", "")).strip(),
            lot_size=int(float(future.get("lotsize", 0) or 0)) or None,
        )
    ]
    for item in selected_options:
        token = str(item.get("token", "")).strip()
        if not token:
            continue
        subscriptions.append(
            SyntheticSubscription(
                exchange_type=2,
                token=token,
                symbol=str(item.get("symbol", "")).strip(),
                underlying=symbol,
                instrument_class=cls,
                expiry=str(item.get("expiry", "")).strip(),
                option_type=str(item.get("symbol", "")).strip()[-2:] or None,
                strike=_strike(item.get("strike")),
                lot_size=int(float(item.get("lotsize", 0) or 0)) or None,
            )
        )
    if len(subscriptions) < 2:
        raise LookupError("selected synthetic chain has no concrete option contracts")
    return SyntheticContractSelection(
        subscriptions=tuple(subscriptions),
        expiry=str(future.get("expiry", "")).strip(),
        atm_strike=float(atm_strike),
    )


__all__ = ["SyntheticContractSelection", "select_synthetic_contracts"]

"""Automatic Cash-Future observation collector.

The collector is deliberately separated from scanner calculation logic. It discovers
NSE cash plus the nearest two NFO stock-future expiries and persists one observation
for CURRENT and one for NEAR on each cycle.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.core.logger import app_logger
from app.market_data.client import MarketDataClient
from app.market_data.instruments import InstrumentMaster
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_point
from app.scanner.cash_future import CashQuote, FutureQuote, calculate_cash_future, CashFutureConfig

IST = ZoneInfo("Asia/Kolkata")


def _expiry(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            pass
    return None


def _ltp(response: dict) -> float:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Angel One LTP response has no data object")
    value = data.get("ltp")
    if value is None:
        raise ValueError("Angel One LTP response has no ltp")
    return float(value)


class CashFutureHistoryCollector:
    """Collect and persist CURRENT/NEAR Cash-Future observations."""

    def __init__(self, symbols: Iterable[str], market_client: MarketDataClient | None = None,
                 instrument_master: InstrumentMaster | None = None):
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.market_client = market_client or MarketDataClient()
        self.instrument_master = instrument_master or InstrumentMaster()

    def _future_instruments(self, symbol: str) -> list[dict]:
        master = self.instrument_master
        master.search(exchange="NFO")
        rows = [
            item for item in master.instruments
            if str(item.get("exch_seg", "")).upper() == "NFO"
            and str(item.get("instrumenttype", "")).upper() in {"FUTSTK", "FUTIDX"}
        ]
        candidates: list[dict] = []
        for item in rows:
            name = str(item.get("name", "")).upper()
            tradingsymbol = str(item.get("symbol", "")).upper()
            if name == symbol or tradingsymbol.startswith(symbol):
                exp = _expiry(item.get("expiry"))
                if exp and exp >= date.today():
                    candidates.append((exp, item))
        candidates.sort(key=lambda x: x[0])
        return [item for _, item in candidates[:2]]

    def collect_symbol(self, symbol: str, db) -> list[dict]:
        symbol = symbol.strip().upper()
        cash_symbol = f"{symbol}-EQ"
        cash = self.instrument_master.get_instrument(cash_symbol, "NSE")
        if not cash:
            raise ValueError(f"NSE cash instrument not found: {cash_symbol}")
        cash_ltp = _ltp(self.market_client.ltp("NSE", cash_symbol, str(cash["token"])))

        # One timestamp per collection cycle keeps CURRENT and NEAR observations comparable.
        observation_time = datetime.now(IST).replace(microsecond=0)
        results: list[dict] = []
        for label, future in zip(("CURRENT", "NEAR"), self._future_instruments(symbol)):
            future_symbol = str(future.get("symbol"))
            future_ltp = _ltp(self.market_client.ltp("NFO", future_symbol, str(future["token"])))
            expiry_date = _expiry(future.get("expiry"))
            lot_size = int(float(future.get("lotsize") or future.get("lotSize") or 0))
            if lot_size <= 0:
                raise ValueError(f"invalid lot size for {future_symbol}")

            quote = calculate_cash_future(
                CashQuote(symbol=symbol, ltp=cash_ltp),
                FutureQuote(symbol=symbol, contract_month=label, ltp=future_ltp,
                            lot_size=lot_size, margin_required=0.0),
                CashFutureConfig(),
            )
            point = CashFutureHistoryPoint(
                timestamp=observation_time, symbol=symbol, contract_month=label,
                cash_price=cash_ltp, future_price=future_ltp, gap=quote.gap,
                gap_pct=quote.gap_pct, lot_size=lot_size, margin_required=0.0,
                charges=quote.charges, funding_cost=quote.funding_cost,
                net_profit=quote.net_profit, roi_pct=quote.roi_pct,
                expiry_date=expiry_date,
            )
            row = save_history_point(db, point, expiry_date=expiry_date)
            results.append({"id": row.id, "symbol": symbol, "contract_month": label,
                            "future_symbol": future_symbol, "expiry_date": expiry_date.isoformat() if expiry_date else None,
                            "cash_price": cash_ltp, "future_price": future_ltp, "gap": quote.gap,
                            "timestamp": observation_time.isoformat()})
        return results

    def collect(self, db) -> dict:
        collected: list[dict] = []
        errors: list[dict] = []
        for symbol in self.symbols:
            try:
                collected.extend(self.collect_symbol(symbol, db))
            except Exception as exc:
                app_logger.error(f"Cash-Future history collection failed for {symbol}: {exc}")
                errors.append({"symbol": symbol, "error": str(exc)})
        return {"collected": collected, "errors": errors}

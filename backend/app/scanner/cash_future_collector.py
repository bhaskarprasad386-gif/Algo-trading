"""Automatic Cash-Future observation collector."""

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


def _number(value: Any, default: float | int = 0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _quote_side(value: Any) -> float | None:
    """Preserve malformed numeric quote sides so downstream validation can reject them."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _full_quote(response: dict) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Angel One FULL quote response has no data object")
    fetched = data.get("fetched")
    if not isinstance(fetched, list) or not fetched:
        raise ValueError("Angel One FULL quote response has no fetched quote")
    item = fetched[0]
    if not isinstance(item, dict):
        raise ValueError("Angel One FULL quote fetched item is invalid")
    depth = item.get("depth") if isinstance(item.get("depth"), dict) else {}
    buys = depth.get("buy") if isinstance(depth.get("buy"), list) else []
    sells = depth.get("sell") if isinstance(depth.get("sell"), list) else []
    bid = _quote_side(buys[0].get("price")) if buys and isinstance(buys[0], dict) else None
    ask = _quote_side(sells[0].get("price")) if sells and isinstance(sells[0], dict) else None
    return {
        "ltp": _number(item.get("ltp")),
        "volume": int(_number(item.get("tradeVolume"))),
        "oi": int(_number(item.get("opnInterest"))),
        "bid": bid,
        "ask": ask,
    }


def _margin_required(response: dict) -> float:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Angel One margin response has no data object")
    value = _number(data.get("totalMarginRequired"), -1)
    if value < 0:
        raise ValueError("Angel One margin response has no valid totalMarginRequired")
    return value


class CashFutureHistoryCollector:
    """Collect and persist CURRENT/NEAR Cash-Future observations."""

    def __init__(self, symbols: Iterable[str], market_client: MarketDataClient | None = None,
                 instrument_master: InstrumentMaster | None = None,
                 config: CashFutureConfig | None = None):
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.market_client = market_client or MarketDataClient()
        self.instrument_master = instrument_master or InstrumentMaster()
        self.config = config or CashFutureConfig()

    def _future_instruments(self, symbol: str) -> list[dict]:
        master = self.instrument_master
        master.search(exchange="NFO")
        rows = [item for item in master.instruments
                if str(item.get("exch_seg", "")).upper() == "NFO"
                and str(item.get("instrumenttype", "")).upper() == "FUTSTK"]
        candidates: list[tuple[date, dict]] = []
        for item in rows:
            name = str(item.get("name", "")).upper()
            tradingsymbol = str(item.get("symbol", "")).upper()
            if name == symbol or tradingsymbol.startswith(symbol):
                exp = _expiry(item.get("expiry"))
                if exp and exp >= date.today():
                    candidates.append((exp, item))
        candidates.sort(key=lambda x: x[0])
        return [item for _, item in candidates[:2]]

    def _future_margin(self, future: dict, future_ltp: float, lot_size: int) -> float:
        token = str(future.get("token") or "").strip()
        if not token:
            raise ValueError("future token is required for broker margin calculation")
        response = self.market_client.margin([{
            "exchange": "NFO",
            "qty": lot_size,
            "price": future_ltp,
            "productType": "CARRYFORWARD",
            "token": token,
            "tradeType": "SELL",
        }])
        return _margin_required(response)

    def collect_symbol(self, symbol: str, db) -> list[dict]:
        symbol = symbol.strip().upper()
        cash_symbol = f"{symbol}-EQ"
        cash = self.instrument_master.get_instrument(cash_symbol, "NSE")
        if not cash:
            raise ValueError(f"NSE cash instrument not found: {cash_symbol}")
        cash_quote = _full_quote(self.market_client.quote("NSE", cash_symbol, str(cash["token"])))
        cash_ltp = cash_quote["ltp"]
        if cash_ltp <= 0:
            raise ValueError(f"invalid cash LTP for {cash_symbol}")
        observation_time = datetime.now(IST).replace(microsecond=0)
        results: list[dict] = []
        for label, future in zip(("CURRENT", "NEAR"), self._future_instruments(symbol)):
            future_symbol = str(future.get("symbol"))
            market_quote = _full_quote(self.market_client.quote("NFO", future_symbol, str(future["token"])))
            future_ltp = market_quote["ltp"]
            if future_ltp <= 0:
                raise ValueError(f"invalid future LTP for {future_symbol}")
            expiry_date = _expiry(future.get("expiry"))
            lot_size = int(float(future.get("lotsize") or future.get("lotSize") or 0))
            if lot_size <= 0:
                raise ValueError(f"invalid lot size for {future_symbol}")
            margin_required = self._future_margin(future, future_ltp, lot_size)
            quote = calculate_cash_future(
                CashQuote(symbol=symbol, ltp=cash_ltp, bid=cash_quote["bid"], ask=cash_quote["ask"]),
                FutureQuote(symbol=symbol, contract_month=label, ltp=future_ltp, lot_size=lot_size,
                            margin_required=margin_required, volume=market_quote["volume"], oi=market_quote["oi"],
                            bid=market_quote["bid"], ask=market_quote["ask"], expiry=expiry_date),
                self.config,
            )
            point = CashFutureHistoryPoint(
                timestamp=observation_time, symbol=symbol, contract_month=label,
                cash_price=cash_ltp, future_price=future_ltp, gap=quote.gap, gap_pct=quote.gap_pct,
                lot_size=lot_size, margin_required=margin_required, volume=market_quote["volume"], oi=market_quote["oi"],
                cash_bid=cash_quote["bid"], cash_ask=cash_quote["ask"], future_bid=market_quote["bid"],
                future_ask=market_quote["ask"], charges=quote.charges, funding_cost=quote.funding_cost,
                net_profit=quote.net_profit, roi_pct=quote.roi_pct, expiry_date=expiry_date,
            )
            row = save_history_point(db, point, expiry_date=expiry_date)
            results.append({
                "id": row.id, "symbol": symbol, "contract_month": label, "future_symbol": future_symbol,
                "expiry_date": expiry_date.isoformat() if expiry_date else None,
                "cash_price": cash_ltp, "future_price": future_ltp, "gap": quote.gap, "gap_pct": quote.gap_pct,
                "executable_gap": quote.executable_gap, "executable_gap_pct": quote.executable_gap_pct,
                "cash_execution_price": quote.cash_execution_price, "future_execution_price": quote.future_execution_price,
                "cash_bid_ask_spread_pct": quote.cash_bid_ask_spread_pct,
                "future_bid_ask_spread_pct": quote.future_bid_ask_spread_pct,
                "gross_spread_profit": quote.gross_spread_profit, "net_profit": quote.net_profit,
                "charges": quote.charges, "funding_cost": quote.funding_cost,
                "margin_required": margin_required, "deployed_capital": quote.deployed_capital,
                "roi_pct": quote.roi_pct, "executable": quote.executable,
                "rejection_reasons": list(quote.rejection_reasons), "volume": market_quote["volume"],
                "oi": market_quote["oi"], "cash_bid": cash_quote["bid"], "cash_ask": cash_quote["ask"],
                "future_bid": market_quote["bid"], "future_ask": market_quote["ask"],
                "timestamp": observation_time.isoformat(),
            })
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

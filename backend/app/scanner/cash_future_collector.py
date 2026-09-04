"""Automatic Cash-Future observation collector."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo
import time

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
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(IST)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return _quote_timestamp(float(text))
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


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
    timestamp = None
    for key in ("exchangeTimestamp", "exchange_timestamp", "lastTradedTimestamp", "last_traded_timestamp", "timestamp", "quoteTime", "quote_time"):
        timestamp = _quote_timestamp(item.get(key))
        if timestamp is not None:
            break
    return {"ltp": _number(item.get("ltp")), "volume": int(_number(item.get("tradeVolume"))), "oi": int(_number(item.get("opnInterest"))), "bid": bid, "ask": ask, "quote_timestamp": timestamp}


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

    def __init__(self, symbols: Iterable[str], market_client: MarketDataClient | None = None, instrument_master: InstrumentMaster | None = None, config: CashFutureConfig | None = None, symbol_timeout_seconds: float | None = 15.0, max_quote_age_seconds: float | None = 15.0, max_quote_timestamp_skew_seconds: float | None = 5.0):
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.market_client = market_client or MarketDataClient()
        self.instrument_master = instrument_master or InstrumentMaster()
        self.config = config or CashFutureConfig()
        if symbol_timeout_seconds is not None and symbol_timeout_seconds <= 0:
            raise ValueError("symbol_timeout_seconds must be positive or None")
        if max_quote_age_seconds is not None and max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be positive or None")
        if max_quote_timestamp_skew_seconds is not None and max_quote_timestamp_skew_seconds <= 0:
            raise ValueError("max_quote_timestamp_skew_seconds must be positive or None")
        self.symbol_timeout_seconds = symbol_timeout_seconds
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_quote_timestamp_skew_seconds = max_quote_timestamp_skew_seconds

    def _future_instruments(self, symbol: str) -> list[dict]:
        master = self.instrument_master
        master.search(exchange="NFO")
        rows = [item for item in master.instruments if str(item.get("exch_seg", "")).upper() == "NFO" and str(item.get("instrumenttype", "")).upper() == "FUTSTK"]
        candidates: list[tuple[date, str, dict]] = []
        seen: set[tuple[date, str]] = set()
        for item in rows:
            name = str(item.get("name", "")).strip().upper()
            tradingsymbol = str(item.get("symbol", "")).strip().upper()
            token = str(item.get("token") or "").strip()
            lot_size = int(_number(item.get("lotsize") or item.get("lotSize"), 0))
            if name != symbol or not tradingsymbol:
                continue
            exp = _expiry(item.get("expiry"))
            if exp is None or exp < date.today() or not token or lot_size <= 0:
                continue
            identity = (exp, tradingsymbol)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append((exp, tradingsymbol, item))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return [item for _, _, item in candidates[:2]]

    def _future_margin(self, future: dict, future_ltp: float, lot_size: int) -> float:
        token = str(future.get("token") or "").strip()
        if not token:
            raise ValueError("future token is required for broker margin calculation")
        response = self.market_client.margin([{"exchange": "NFO", "qty": lot_size, "price": future_ltp, "productType": "CARRYFORWARD", "token": token, "tradeType": "SELL"}])
        return _margin_required(response)

    def _validate_quote_freshness(self, label: str, future_symbol: str, quote_timestamp: datetime | None) -> None:
        if self.max_quote_age_seconds is None or quote_timestamp is None:
            return
        age = (datetime.now(IST) - quote_timestamp).total_seconds()
        if age >= 0 and age > self.max_quote_age_seconds:
            raise ValueError(f"stale {label} quote for {future_symbol}: age {age:.1f}s exceeds max {self.max_quote_age_seconds:g}s")

    def _validate_quote_timestamp_skew(self, cash_timestamp: datetime | None, future_timestamp: datetime | None, future_symbol: str) -> None:
        if self.max_quote_timestamp_skew_seconds is None or cash_timestamp is None or future_timestamp is None:
            return
        skew = abs((future_timestamp - cash_timestamp).total_seconds())
        if skew > self.max_quote_timestamp_skew_seconds:
            raise ValueError(f"cash-future quote timestamp skew for {future_symbol}: {skew:.1f}s exceeds max {self.max_quote_timestamp_skew_seconds:g}s")

    def collect_symbol(self, symbol: str, db, errors: list[dict] | None = None) -> list[dict]:
        symbol = symbol.strip().upper()
        started = time.monotonic()
        cash_symbol = f"{symbol}-EQ"
        cash = self.instrument_master.get_instrument(cash_symbol, "NSE")
        if not cash:
            raise ValueError(f"NSE cash instrument not found: {cash_symbol}")
        cash_quote = _full_quote(self.market_client.quote("NSE", cash_symbol, str(cash["token"])))
        self._validate_quote_freshness("CASH", cash_symbol, cash_quote["quote_timestamp"])
        cash_ltp = cash_quote["ltp"]
        if cash_ltp <= 0:
            raise ValueError(f"invalid cash LTP for {cash_symbol}")
        futures = self._future_instruments(symbol)
        if not futures:
            raise ValueError(f"no eligible NFO FUTSTK contracts found: {symbol}")
        observation_time = datetime.now(IST).replace(microsecond=0)
        results: list[dict] = []
        for label, future in zip(("CURRENT", "NEAR"), futures):
            if self.symbol_timeout_seconds is not None and time.monotonic() - started >= self.symbol_timeout_seconds:
                message = f"cash-future symbol scan timeout after {self.symbol_timeout_seconds:g}s"
                app_logger.warning(f"Cash-Future {symbol}: {message}")
                if errors is not None:
                    errors.append({"symbol": symbol, "contract_month": label, "future_symbol": str(future.get("symbol") or "").strip(), "error": message})
                break
            future_symbol = str(future.get("symbol") or "").strip()
            try:
                market_quote = _full_quote(self.market_client.quote("NFO", future_symbol, str(future["token"])))
                self._validate_quote_freshness(label, future_symbol, market_quote["quote_timestamp"])
                self._validate_quote_timestamp_skew(cash_quote["quote_timestamp"], market_quote["quote_timestamp"], future_symbol)
                future_ltp = market_quote["ltp"]
                if future_ltp <= 0:
                    raise ValueError(f"invalid future LTP for {future_symbol}")
                lot_size = int(_number(future.get("lotsize") or future.get("lotSize"), 0))
                margin = self._future_margin(future, future_ltp, lot_size)
                future_quote = FutureQuote(symbol=future_symbol, contract_month=label, ltp=future_ltp, lot_size=lot_size, margin_required=margin, volume=market_quote["volume"], oi=market_quote["oi"], bid=market_quote["bid"], ask=market_quote["ask"], expiry=_expiry(future.get("expiry")))
                result = calculate_cash_future(CashQuote(symbol=cash_symbol, ltp=cash_ltp, bid=cash_quote["bid"], ask=cash_quote["ask"]), future_quote, self.config)
                item = result.__dict__.copy()
                item["contract_month"] = label
                item["timestamp"] = observation_time.isoformat()
                item["cash_quote_timestamp"] = cash_quote["quote_timestamp"].isoformat() if cash_quote["quote_timestamp"] else None
                item["quote_timestamp"] = market_quote["quote_timestamp"].isoformat() if market_quote["quote_timestamp"] else None
                save_history_point(db, CashFutureHistoryPoint(symbol=symbol, contract_month=label, timestamp=observation_time, cash_price=cash_ltp, future_price=future_ltp, gap=result.gap, gap_pct=result.gap_pct, lot_size=lot_size, margin_required=margin, volume=market_quote["volume"], oi=market_quote["oi"], cash_bid=cash_quote["bid"], cash_ask=cash_quote["ask"], future_bid=market_quote["bid"], future_ask=market_quote["ask"], charges=self.config.charges, funding_cost=self.config.funding_cost, net_profit=result.net_profit, roi_pct=result.roi_pct, expiry_date=future_quote.expiry), expiry_date=future_quote.expiry)
                results.append(item)
            except Exception as exc:
                app_logger.warning(f"Cash-Future {symbol} {label} {future_symbol}: {exc}")
                if errors is not None:
                    errors.append({"symbol": symbol, "contract_month": label, "future_symbol": future_symbol, "error": str(exc)})
        return results

    def collect(self, db) -> dict[str, list]:
        collected: list[dict] = []
        errors: list[dict] = []
        for symbol in self.symbols:
            try:
                collected.extend(self.collect_symbol(symbol, db, errors=errors))
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
        return {"collected": collected, "errors": errors}

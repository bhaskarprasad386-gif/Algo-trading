"""Continuous 1-second Cash-Future live market-data collector.

Uses Angel One SmartAPI WebSocket V2 for CURRENT/NEAR NSE cash and NFO futures.
Only source-backed observations are persisted; missing seconds are never
forward-filled. Live orders are not involved.
"""

from __future__ import annotations

from datetime import date, datetime, time
from queue import Empty, Queue
import threading
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.core.logger import app_logger
from app.market_data.instruments import InstrumentMaster
from app.market_data.websocket import MarketDataWebSocket

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)
SOURCE = "angelone-live-1s"
TIMEFRAME = "1s"


def _expiry(value: Any) -> date | None:
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _timestamp_ns(message: dict[str, Any]) -> int | None:
    value = message.get("exchange_timestamp")
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number < 10_000_000_000:
        return number * 1_000_000_000
    if number < 10_000_000_000_000:
        return number * 1_000_000
    return number * 1_000


def _ltp(message: dict[str, Any]) -> float | None:
    value = message.get("last_traded_price")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100.0


class LiveCashFutureOneSecondCollector:
    """Stream current/near Cash-Future observations into the backtest catalog."""

    def __init__(
        self,
        data_db: str,
        *,
        auth: AngelOneAuth | None = None,
        instrument_master: InstrumentMaster | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.data_db = data_db
        self.auth = auth or AngelOneAuth()
        self.instrument_master = instrument_master or InstrumentMaster()
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self._sockets: list[MarketDataWebSocket] = []

    @staticmethod
    def market_open(now: datetime | None = None) -> bool:
        current = now or datetime.now(IST)
        return current.weekday() < 5 and OPEN <= current.time() <= CLOSE

    def _contracts(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = self.instrument_master.download()
        today = datetime.now(IST).date()
        futures: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in rows:
            if str(item.get("exch_seg", "")).upper() != "NFO":
                continue
            if str(item.get("instrumenttype", "")).upper() != "FUTSTK":
                continue
            token = str(item.get("token") or "").strip()
            underlying = str(item.get("name") or "").strip().upper()
            symbol = str(item.get("symbol") or "").strip().upper()
            expiry = _expiry(item.get("expiry"))
            if not token or not underlying or not symbol or expiry is None or expiry < today:
                continue
            key = (underlying, token)
            if key not in seen:
                seen.add(key)
                futures.append({**item, "_expiry": expiry, "_underlying": underlying})
        futures.sort(key=lambda x: (x["_underlying"], x["_expiry"], str(x.get("symbol"))))
        selected: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for future in futures:
            underlying = future["_underlying"]
            if counts.get(underlying, 0) >= 2:
                continue
            try:
                cash = self.instrument_master.resolve_cash_instrument(underlying, "NSE")
            except (LookupError, ValueError):
                continue
            future = dict(future)
            future["_cash_token"] = str(cash["token"])
            future["_cash_symbol"] = str(cash.get("symbol") or f"{underlying}-EQ")
            future["_contract_month"] = "CURRENT" if counts.get(underlying, 0) == 0 else "NEAR"
            selected.append(future)
            counts[underlying] = counts.get(underlying, 0) + 1
        cash = {}
        for future in selected:
            cash[str(future["_cash_token"])] = {
                "token": str(future["_cash_token"]),
                "symbol": str(future["_cash_symbol"]),
                "underlying": future["_underlying"],
            }
        return selected, list(cash.values())

    def _run_market_session(self) -> None:
        futures, cash = self._contracts()
        if not futures:
            app_logger.warning("1-second live Cash-Future collector found no eligible contracts")
            time_module.sleep(30)
            return

        token_meta: dict[str, dict[str, Any]] = {}
        for item in cash:
            token_meta[item["token"]] = {
                "leg": "CASH",
                "underlying": item["underlying"],
                "symbol": item["symbol"],
                "contract_month": None,
                "expiry": None,
            }
        for item in futures:
            token_meta[str(item["token"])] = {
                "leg": "FUTURE",
                "underlying": item["_underlying"],
                "symbol": str(item["symbol"]),
                "contract_month": item["_contract_month"],
                "expiry": item["_expiry"].isoformat(),
            }

        queue: Queue[dict[str, Any]] = Queue()
        self.auth.login()
        nse_socket = MarketDataWebSocket(auth=self.auth)
        nfo_socket = MarketDataWebSocket(auth=self.auth)
        self._sockets = [nse_socket, nfo_socket]

        def receive(message: Any) -> None:
            if isinstance(message, dict):
                queue.put(message)

        threads = [
            threading.Thread(
                target=lambda: nse_socket.connect(
                    exchange_type=1,
                    tokens=[item["token"] for item in cash],
                    mode=1,
                    correlation_id="cf-live-nse",
                    on_data=receive,
                    reconnect_attempts=3,
                    reconnect_delay_seconds=2,
                ),
                daemon=True,
                name="cf-live-nse",
            ),
            threading.Thread(
                target=lambda: nfo_socket.connect(
                    exchange_type=2,
                    tokens=[str(item["token"]) for item in futures],
                    mode=1,
                    correlation_id="cf-live-nfo",
                    on_data=receive,
                    reconnect_attempts=3,
                    reconnect_delay_seconds=2,
                ),
                daemon=True,
                name="cf-live-nfo",
            ),
        ]
        for thread in threads:
            thread.start()

        catalog = HistoricalCatalog(self.data_db)
        latest: dict[str, tuple[int, dict[str, Any]]] = {}
        written = 0
        try:
            while not self.stop_event.is_set() and self.market_open():
                deadline = time_module.monotonic() + self.poll_seconds
                while time_module.monotonic() < deadline:
                    try:
                        message = queue.get(timeout=max(0.01, deadline - time_module.monotonic()))
                    except Empty:
                        break
                    token = str(message.get("token") or "").strip()
                    meta = token_meta.get(token)
                    ts = _timestamp_ns(message)
                    if not token or meta is None or ts is None:
                        continue
                    second_ns = (ts // 1_000_000_000) * 1_000_000_000
                    previous = latest.get(token)
                    if previous is not None and previous[0] != second_ns:
                        previous_payload = previous[1]
                        previous_payload["source_timestamp_ns"] = previous[0]
                        try:
                            written += catalog.ingest(HistoricalRecord(
                                source=SOURCE,
                                instrument=f"{meta['symbol']}|{token}",
                                timeframe=TIMEFRAME,
                                timestamp_ns=previous[0],
                                payload=previous_payload,
                            ))
                        except ValueError as exc:
                            app_logger.error(f"1-second live record rejected {token}: {exc}")
                    payload = dict(message)
                    payload.update({
                        "ltp": _ltp(message),
                        "underlying": meta["underlying"],
                        "leg": meta["leg"],
                        "contract_month": meta["contract_month"],
                        "expiry": meta["expiry"],
                        "received_at_ns": time_module.time_ns(),
                    })
                    latest[token] = (second_ns, payload)
            for token, (second_ns, payload) in latest.items():
                meta = token_meta[token]
                try:
                    written += catalog.ingest(HistoricalRecord(
                        source=SOURCE,
                        instrument=f"{meta['symbol']}|{token}",
                        timeframe=TIMEFRAME,
                        timestamp_ns=second_ns,
                        payload=payload,
                    ))
                except ValueError as exc:
                    app_logger.error(f"1-second final record rejected {token}: {exc}")
        finally:
            catalog.close()
            for socket in self._sockets:
                socket.close()
            self._sockets = []
        app_logger.info(f"1-second live Cash-Future session complete: written={written}")

    def run_forever(self) -> None:
        app_logger.info("1-second live Cash-Future collector started")
        while not self.stop_event.is_set():
            try:
                if self.market_open():
                    self._run_market_session()
                else:
                    time_module.sleep(5)
            except Exception as exc:
                app_logger.error(f"1-second live Cash-Future collector failed: {exc}")
                time_module.sleep(10)
        app_logger.info("1-second live Cash-Future collector stopped")

    def stop(self) -> None:
        self.stop_event.set()
        for socket in self._sockets:
            socket.close()

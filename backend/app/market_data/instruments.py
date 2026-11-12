import requests
from threading import Lock
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger
from app.market_data.master_transport import download_master_rows


class InstrumentMaster:
    """Angel One instrument master manager with process-wide lazy caching."""

    MASTER_URL = (
        "https://margincalculator.angelbroking.com/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    ALTERNATE_MASTER_URL = (
        "https://margincalculator.angelone.in/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    _cache_lock = Lock()
    _cached_instruments: Optional[List[Dict[str, Any]]] = None
    _cached_snapshot_date: Optional[str] = None

    def __init__(self):
        # Each instance remains a cheap view over the process-wide snapshot.
        # This avoids a download for every WebSocket client while preserving
        # the existing constructor/API used by tests and other callers.
        self.instruments: List[Dict[str, Any]] = []
        self._loaded = False
        self._symbol_exchange_index = None
        cached = self._get_cached_snapshot()
        if cached is not None:
            self.instruments = cached
            self._loaded = True

    @classmethod
    def _get_cached_snapshot(cls) -> Optional[List[Dict[str, Any]]]:
        with cls._cache_lock:
            if cls._cached_instruments is None:
                return None
            return cls._cached_instruments

    def download(self, *, force: bool = False, timeout: Any = 30) -> List[Dict[str, Any]]:
        """Download Angel One instrument master at most once per IST day unless forced.

        Daily refresh is intentional because expiry/roll-day contracts can change
        without a process restart.
        """
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        with self._cache_lock:
            if not force and self._loaded and self.instruments and self.__class__._cached_snapshot_date == today:
                return self.instruments
            if not force and self._cached_instruments is not None and self.__class__._cached_snapshot_date == today:
                self.instruments = self._cached_instruments
                self._loaded = True
                return self.instruments
            try:
                source_errors = []
                data = None
                request_timeout = (
                    timeout if isinstance(timeout, tuple) and len(timeout) == 2
                    else (8, float(timeout) if isinstance(timeout, (int, float)) else 90)
                )
                for source_url in (self.MASTER_URL, self.ALTERNATE_MASTER_URL):
                    try:
                        data = download_master_rows(
                            source_url,
                            timeout=request_timeout,
                            retries=3,
                        )
                        app_logger.info("Instrument master downloaded from {}", source_url)
                        break
                    except Exception as source_error:
                        source_errors.append(f"{source_url}: {type(source_error).__name__}: {source_error}")
                        app_logger.warning("Instrument master source failed; trying official alternate host: {}", source_errors[-1])
                if data is None:
                    raise RuntimeError("All official Angel One instrument-master hosts failed: " + " | ".join(source_errors))
                self.__class__._cached_instruments = data
                self.__class__._cached_snapshot_date = today
                self.instruments = data
                self._loaded = True
                self._symbol_exchange_index = None
                app_logger.info(
                    f"Loaded {len(self.instruments)} instruments from Angel One instrument master"
                )
                return self.instruments
            except TradingAppException:
                raise
            except Exception as e:
                app_logger.error(f"Failed to download instrument master: {str(e)}")
                raise TradingAppException("InstrumentMasterDownloadError", str(e), 502)

    def search(
        self,
        tradingsymbol: Optional[str] = None,
        exchange: Optional[str] = None,
        symboltoken: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search instruments by symbol, exchange, or token."""
        # An explicitly supplied empty snapshot is a valid test/offline state and
        # must not trigger an unexpected network request.
        if not self._loaded and not self.instruments:
            self.download()
        results = self.instruments
        if tradingsymbol:
            results = [
                item for item in results
                if item.get("symbol", "").upper() == tradingsymbol.upper()
            ]
        if exchange:
            results = [
                item for item in results
                if item.get("exch_seg", "").upper() == exchange.upper()
            ]
        if symboltoken:
            results = [
                item for item in results
                if str(item.get("token", "")) == str(symboltoken)
            ]
        return results

    def get_token(self, tradingsymbol: str, exchange: str) -> Optional[str]:
        """Return the Angel One token for a trading symbol."""
        results = self.search(tradingsymbol=tradingsymbol, exchange=exchange)
        if not results:
            return None
        return str(results[0].get("token"))

    def get_instrument(
        self,
        tradingsymbol: str,
        exchange: str,
    ) -> Optional[Dict[str, Any]]:
        """Return complete instrument information."""
        results = self.search(tradingsymbol=tradingsymbol, exchange=exchange)
        return results[0] if results else None

    def _ensure_symbol_exchange_index(self):
        index = self._symbol_exchange_index
        if index is not None:
            return index
        built = {}
        for item in self.instruments:
            symbol = str(item.get("symbol", "")).strip().upper()
            exch = str(item.get("exch_seg", "")).strip().upper()
            if not symbol or not exch:
                continue
            built.setdefault((symbol, exch), []).append(item)
        self._symbol_exchange_index = built
        return built

    def resolve_cash_instrument(self, tradingsymbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Resolve exactly one NSE cash instrument and fail closed on ambiguity."""
        symbol = str(tradingsymbol).strip()
        exch = str(exchange).strip().upper()
        if not symbol:
            raise ValueError("cash trading symbol cannot be empty")
        if not exch:
            raise ValueError("cash exchange cannot be empty")

        candidate_symbols = [symbol]
        if not symbol.endswith("-EQ"):
            candidate_symbols.append(f"{symbol}-EQ")
        index = self._ensure_symbol_exchange_index()
        results = [
            item
            for candidate in candidate_symbols
            for item in index.get((candidate.upper(), exch), ())
            if str(item.get("instrumenttype", "")).upper() in {"CASH", "EQ", "EQUITY", ""}
            and str(item.get("exch_seg", "")).upper() == exch
        ]
        if len(results) != 1:
            raise LookupError(
                f"expected exactly one cash instrument for {exch}:{symbol}, found {len(results)}"
            )
        token = str(results[0].get("token", "")).strip()
        if not token:
            raise LookupError(f"cash instrument {exch}:{symbol} has no Angel One token")
        return results[0]

    def resolve_cash_token(self, tradingsymbol: str, exchange: str = "NSE") -> str:
        """Resolve a validated cash instrument to its Angel One token."""
        return str(self.resolve_cash_instrument(tradingsymbol, exchange).get("token"))
    def resolve_index_instrument(self, tradingsymbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Resolve exactly one concrete index instrument from Angel One master."""
        symbol = str(tradingsymbol).strip().upper()
        exch = str(exchange).strip().upper()
        if not symbol:
            raise ValueError("index trading symbol cannot be empty")
        results = [
            item
            for item in self.instruments
            if str(item.get("exch_seg", "")).strip().upper() == exch
            and str(item.get("token", "")).strip()
            and (
                str(item.get("symbol", "")).strip().upper() == symbol
                or str(item.get("name", "")).strip().upper() == symbol
            )
        ]
        # Angel One migrated index tokens to the 999xxxxx series. A
        # legacy single-row entry (for example token 25/26000) must not be
        # accepted as an index: those legacy tokens can collide with NSE
        # cash-instrument tokens and break the shared WebSocket descriptor
        # registry. Prefer exactly one current AMXIDX/999xxxxx entry.
        preferred = [
            item
            for item in results
            if str(item.get("token", "")).strip().startswith("999")
            and (
                not str(item.get("instrumenttype", "")).strip()
                or str(item.get("instrumenttype", "")).strip().upper() == "AMXIDX"
            )
        ]
        if len(preferred) == 1:
            return preferred[0]

        raise LookupError(
            f"expected exactly one index instrument for {exch}:{symbol}, found {len(results)}"
        )

    def resolve_index_token(self, tradingsymbol: str, exchange: str = "NSE") -> str:
        """Resolve a concrete Angel One token for an index underlying."""
        return str(self.resolve_index_instrument(tradingsymbol, exchange)["token"])

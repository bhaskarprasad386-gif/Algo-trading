import json
import os
import tempfile
import time
from pathlib import Path
import requests
from threading import Lock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class InstrumentMaster:
    """Angel One instrument master manager with process-wide lazy caching."""

    MASTER_URL = (
        "https://margincalculator.angelbroking.com/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    _cache_lock = Lock()
    _cached_instruments: Optional[List[Dict[str, Any]]] = None
    _cached_snapshot_date: Optional[str] = None
    _last_download_attempt_at: float = 0.0
    _last_download_error: Optional[str] = None
    _retry_after_seconds: float = 60.0
    _disk_fallback_max_age_seconds: float = 36 * 60 * 60

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

    @classmethod
    def _disk_cache_path(cls) -> Path:
        configured = os.environ.get("INSTRUMENT_MASTER_CACHE_PATH", "").strip()
        if configured:
            return Path(configured).expanduser()
        # Keep the snapshot outside Python package files but within the backend
        # deployment tree by default. Override with the environment variable when
        # the service uses a read-only application directory.
        return Path(__file__).resolve().parents[2] / "data" / "instrument_master_cache.json"

    @classmethod
    def _read_disk_snapshot(cls):
        path = cls._disk_cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("instruments") if isinstance(payload, dict) else None
            fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
                return None
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > cls._disk_fallback_max_age_seconds:
                return None
            # A minimally useful row must carry symbol, exchange and token fields.
            usable = [
                row for row in rows
                if str(row.get("symbol", "")).strip()
                and str(row.get("exch_seg", "")).strip()
                and str(row.get("token", "")).strip()
            ]
            if not usable:
                return None
            return usable, fetched
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @classmethod
    def _write_disk_snapshot(cls, rows: List[Dict[str, Any]]) -> None:
        path = cls._disk_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "instruments": rows,
        }
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(path.parent),
                prefix=path.name + ".", suffix=".tmp", delete=False
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except OSError as exc:
            app_logger.warning(f"Could not persist instrument-master snapshot: {exc}")
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def download(self, *, force: bool = False) -> List[Dict[str, Any]]:
        """Refresh once per IST day; use a validated recent disk snapshot on outage.

        A successful download is atomically persisted. A failed download may use
        a snapshot no older than 36 hours; it never fabricates instrument rows.
        Failed attempts are throttled to avoid one long network timeout per symbol.
        """
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        with self._cache_lock:
            if not force and self._loaded and self.instruments and self.__class__._cached_snapshot_date == today:
                return self.instruments
            if not force and self._cached_instruments is not None and self.__class__._cached_snapshot_date == today:
                self.instruments = self._cached_instruments
                self._loaded = True
                return self.instruments

            now = time.monotonic()
            if (
                not force
                and self.__class__._last_download_error
                and now - self.__class__._last_download_attempt_at < self.__class__._retry_after_seconds
            ):
                fallback = self.__class__._cached_instruments
                if fallback:
                    self.instruments = fallback
                    self._loaded = True
                    return self.instruments
                raise TradingAppException(
                    "InstrumentMasterDownloadCooldown",
                    "Instrument-master download recently failed; retry is temporarily throttled.",
                    502,
                )

            # Read disk before attempting the network so it is available if the
            # provider is unreachable. The snapshot is accepted only within TTL.
            disk_snapshot = self.__class__._read_disk_snapshot()
            self.__class__._last_download_attempt_at = now
            try:
                response = requests.get(self.MASTER_URL, timeout=(5, 15))
                response.raise_for_status()
                data = response.json()
                if (
                    not isinstance(data, list)
                    or not data
                    or not all(isinstance(row, dict) for row in data)
                    or not any(
                        str(row.get("symbol", "")).strip()
                        and str(row.get("exch_seg", "")).strip()
                        and str(row.get("token", "")).strip()
                        for row in data
                    )
                ):
                    raise TradingAppException(
                        "InvalidInstrumentMaster",
                        "Angel One instrument master format is invalid.",
                        502,
                    )
                self.__class__._cached_instruments = data
                self.__class__._cached_snapshot_date = today
                self.__class__._last_download_error = None
                self.instruments = data
                self._loaded = True
                self._symbol_exchange_index = None
                self.__class__._write_disk_snapshot(data)
                app_logger.info(
                    f"Loaded {len(self.instruments)} instruments from Angel One instrument master"
                )
                return self.instruments
            except TradingAppException:
                raise
            except Exception as e:
                self.__class__._last_download_error = str(e)
                app_logger.error(f"Failed to download instrument master: {str(e)}")
                if disk_snapshot is not None:
                    rows, fetched_at = disk_snapshot
                    self.__class__._cached_instruments = rows
                    # Do not mark the fallback as today's successful refresh:
                    # after cooldown, the next caller can retry the provider.
                    self.__class__._cached_snapshot_date = None
                    self.instruments = rows
                    self._loaded = True
                    self._symbol_exchange_index = None
                    app_logger.warning(
                        "Using validated instrument-master disk snapshot after provider failure "
                        f"(fetched_at={fetched_at.isoformat()}, rows={len(rows)})"
                    )
                    return self.instruments
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

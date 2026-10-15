import json
import os
import tempfile
import time
import requests
from pathlib import Path
from threading import Lock
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class InstrumentMaster:
    """Angel One instrument master manager with validated disk fallback."""

    MASTER_URL = (
        "https://margincalculator.angelbroking.com/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    CACHE_SCHEMA_VERSION = 1
    DEFAULT_CACHE_MAX_AGE_HOURS = 12
    MAX_CACHE_MAX_AGE_HOURS = 72
    DOWNLOAD_FAILURE_COOLDOWN_SECONDS = 20.0
    CONNECT_TIMEOUT_SECONDS = 5.0
    READ_TIMEOUT_SECONDS = 15.0

    _cache_lock = Lock()
    # Serialize network refreshes without holding the snapshot lock during I/O.
    _download_lock = Lock()
    _cached_instruments: Optional[List[Dict[str, Any]]] = None
    _cached_snapshot_date: Optional[str] = None
    _last_failed_at_monotonic: Optional[float] = None
    _last_download_error: Optional[str] = None

    def __init__(self):
        self.instruments: List[Dict[str, Any]] = []
        self._loaded = False
        self._symbol_exchange_index = None
        cached = self._get_cached_snapshot()
        if cached is not None:
            self.instruments = cached
            self._loaded = True

    @classmethod
    def _cache_path(cls) -> Path:
        configured = os.getenv("ANGEL_INSTRUMENT_MASTER_CACHE_PATH")
        if configured:
            return Path(configured).expanduser()
        return Path(__file__).resolve().parents[2] / "data" / "instrument_master_cache.json"

    @classmethod
    def _cache_max_age(cls) -> timedelta:
        try:
            hours = float(os.getenv(
                "ANGEL_INSTRUMENT_MASTER_MAX_CACHE_AGE_HOURS",
                str(cls.DEFAULT_CACHE_MAX_AGE_HOURS),
            ))
        except (TypeError, ValueError):
            hours = float(cls.DEFAULT_CACHE_MAX_AGE_HOURS)
        hours = min(float(cls.MAX_CACHE_MAX_AGE_HOURS), max(1.0, hours))
        return timedelta(hours=hours)

    @staticmethod
    def _validate_master_data(data: Any) -> List[Dict[str, Any]]:
        """Reject empty, non-object, or structurally unusable master responses."""
        if not isinstance(data, list) or not data:
            raise ValueError("instrument master must be a non-empty JSON list")
        object_rows = [row for row in data if isinstance(row, dict)]
        if not object_rows:
            raise ValueError("instrument master contains no object rows")
        valid_rows = [
            row for row in object_rows
            if str(row.get("token", "")).strip()
            and str(row.get("symbol", "")).strip()
            and str(row.get("exch_seg", "")).strip()
        ]
        # Reject truncated/error-shaped payloads while tolerating occasional
        # optional/incomplete rows from the upstream catalogue.
        required_valid = max(1, int(len(data) * 0.90 + 0.999999))
        if len(valid_rows) < required_valid:
            raise ValueError(
                f"instrument master structure invalid: {len(valid_rows)}/{len(data)} rows "
                "have token, symbol and exchange segment"
            )
        return object_rows

    @classmethod
    def _read_disk_cache(cls):
        path = cls._cache_path()
        with path.open("r", encoding="utf-8") as handle:
            envelope = json.load(handle)
        if not isinstance(envelope, dict) or envelope.get("schema_version") != cls.CACHE_SCHEMA_VERSION:
            raise ValueError("instrument master cache schema is invalid")
        fetched_raw = envelope.get("fetched_at")
        if not isinstance(fetched_raw, str):
            raise ValueError("instrument master cache timestamp is missing")
        fetched_at = datetime.fromisoformat(fetched_raw.replace("Z", "+00:00"))
        if fetched_at.tzinfo is None:
            raise ValueError("instrument master cache timestamp must include timezone")
        fetched_at = fetched_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if fetched_at > now + timedelta(minutes=5):
            raise ValueError("instrument master cache timestamp is in the future")
        age = now - fetched_at
        if age > cls._cache_max_age():
            raise ValueError(f"instrument master cache is stale ({age.total_seconds() / 3600:.1f} hours old)")
        data = cls._validate_master_data(envelope.get("instruments"))
        snapshot_date = envelope.get("snapshot_date")
        if not isinstance(snapshot_date, str) or not snapshot_date:
            snapshot_date = fetched_at.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
        return data, fetched_at, snapshot_date

    @classmethod
    def _write_disk_cache(cls, data: List[Dict[str, Any]], fetched_at: datetime, snapshot_date: str) -> None:
        path = cls._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": cls.CACHE_SCHEMA_VERSION,
            "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
            "snapshot_date": snapshot_date,
            "instruments": data,
        }
        fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, separators=(",", ":"), ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            try:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)
            except OSError:
                pass

    def _publish_snapshot(
        self,
        data: List[Dict[str, Any]],
        snapshot_date: str,
    ) -> List[Dict[str, Any]]:
        with self._cache_lock:
            self.__class__._cached_instruments = data
            self.__class__._cached_snapshot_date = snapshot_date
            self.instruments = data
            self._loaded = True
            self._symbol_exchange_index = None
        return self.instruments

    @classmethod
    def _get_cached_snapshot(cls) -> Optional[List[Dict[str, Any]]]:
        with cls._cache_lock:
            if cls._cached_instruments is None:
                return None
            return cls._cached_instruments

    def _use_disk_cache(self, reason: str) -> Optional[List[Dict[str, Any]]]:
        try:
            data, fetched_at, snapshot_date = self._read_disk_cache()
            self._publish_snapshot(data, snapshot_date)
            app_logger.warning(
                "Instrument master using validated disk cache "
                f"(age_hours={(datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600:.2f}; "
                f"reason={reason})"
            )
            return self.instruments
        except Exception as cache_error:
            app_logger.warning(f"Instrument master disk cache unavailable: {cache_error}")
            return None

    def download(self, *, force: bool = False) -> List[Dict[str, Any]]:
        """Refresh once per IST day; use only a validated, age-bounded disk fallback."""
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        with self._cache_lock:
            if (
                not force and self._loaded and self.instruments
                and self.__class__._cached_snapshot_date == today
            ):
                return self.instruments
            if (
                not force and self._cached_instruments is not None
                and self.__class__._cached_snapshot_date == today
            ):
                self.instruments = self._cached_instruments
                self._loaded = True
                return self.instruments

        # The lock ensures concurrent runners share one refresh attempt. The
        # snapshot lock is deliberately not held across the HTTP request.
        with self._download_lock:
            with self._cache_lock:
                if (
                    not force and self._loaded and self.instruments
                    and self.__class__._cached_snapshot_date == today
                ):
                    return self.instruments
                if (
                    not force and self._cached_instruments is not None
                    and self.__class__._cached_snapshot_date == today
                ):
                    self.instruments = self._cached_instruments
                    self._loaded = True
                    return self.instruments
                failed_at = self.__class__._last_failed_at_monotonic
                last_error = self.__class__._last_download_error

            if (
                failed_at is not None
                and time.monotonic() - failed_at < self.DOWNLOAD_FAILURE_COOLDOWN_SECONDS
            ):
                cached = self._use_disk_cache("recent refresh failure cooldown")
                if cached is not None:
                    return cached
                raise TradingAppException(
                    "InstrumentMasterDownloadError",
                    f"Instrument master refresh is cooling down after a recent failure: {last_error or 'unknown error'}",
                    502,
                )

            try:
                response = requests.get(
                    self.MASTER_URL,
                    timeout=(self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS),
                )
                response.raise_for_status()
                data = self._validate_master_data(response.json())
                fetched_at = datetime.now(timezone.utc)
                snapshot_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
                try:
                    self._write_disk_cache(data, fetched_at, snapshot_date)
                except Exception as cache_error:
                    # A writable cache is desirable but must not discard a
                    # valid live response when the deployment filesystem is read-only.
                    app_logger.warning(f"Could not persist instrument master cache: {cache_error}")
                with self._cache_lock:
                    self.__class__._last_failed_at_monotonic = None
                    self.__class__._last_download_error = None
                self._publish_snapshot(data, snapshot_date)
                app_logger.info(
                    f"Loaded {len(self.instruments)} validated instruments from Angel One instrument master"
                )
                return self.instruments
            except Exception as error:
                error_text = str(error)
                with self._cache_lock:
                    self.__class__._last_failed_at_monotonic = time.monotonic()
                    self.__class__._last_download_error = error_text
                cached = self._use_disk_cache(error_text)
                if cached is not None:
                    return cached
                app_logger.error(f"Failed to download instrument master and no valid cache exists: {error_text}")
                raise TradingAppException(
                    "InstrumentMasterDownloadError",
                    f"Angel One instrument master unavailable and no valid disk cache could be used: {error_text}",
                    502,
                ) from error

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

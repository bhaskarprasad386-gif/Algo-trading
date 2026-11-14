import json
import os
import tempfile
import time
from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
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
    # Protect only in-memory snapshot reads/writes; never hold this lock over network I/O.
    _cache_lock = Lock()
    # Single-flight refresh: concurrent callers share one download attempt.
    _download_lock = Lock()
    _cached_instruments: Optional[List[Dict[str, Any]]] = None
    _cached_snapshot_date: Optional[str] = None
    _last_download_attempt_at: float = 0.0
    _last_download_error: Optional[str] = None
    _cached_disk_snapshot_fetched_at: Optional[datetime] = None
    _retry_after_seconds: float = 60.0
    _disk_fallback_max_age_seconds: float = 36 * 60 * 60

    def __init__(self):
        # Each instance remains a cheap view over the process-wide snapshot.
        # This avoids a download for every WebSocket client while preserving
        # the existing constructor/API used by tests and other callers.
        self.instruments: List[Dict[str, Any]] = []
        self._loaded = False
        self._symbol_exchange_index = None
        self._disk_fallback_fetched_at: Optional[datetime] = None
        cached = self._get_cached_snapshot()
        if cached is not None:
            self.instruments = cached
            self._loaded = True
            self._disk_fallback_fetched_at = self.__class__._cached_disk_snapshot_fetched_at

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
        return Path(__file__).resolve().parents[2] / "data" / "instrument_master_cache.json"

    @classmethod
    def _read_disk_snapshot(cls):
        path = cls._disk_cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or type(payload.get("schema_version")) is not int
                or payload.get("schema_version") != 1
            ):
                return None
            rows = payload.get("instruments")
            fetched_at = payload.get("fetched_at")
            if (
                not isinstance(rows, list)
                or not rows
                or not all(
                    isinstance(row, dict)
                    and str(row.get("symbol", "")).strip()
                    and str(row.get("exch_seg", "")).strip()
                    and str(row.get("token", "")).strip()
                    for row in rows
                )
            ):
                return None
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > cls._disk_fallback_max_age_seconds:
                return None
            return rows, fetched
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @classmethod
    def _write_disk_snapshot(cls, rows: List[Dict[str, Any]]) -> None:
        path = cls._disk_cache_path()
        payload = {
            "schema_version": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "instruments": rows,
        }
        temp_name = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except OSError as exc:
            app_logger.warning("Could not persist instrument-master snapshot: {}", str(exc))
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def download(self, *, force: bool = False, timeout: Any = 30) -> List[Dict[str, Any]]:
        """Refresh once per IST day without holding the cache lock during network I/O.

        A dedicated single-flight lock prevents duplicate concurrent downloads.
        The short cache lock protects publication only, so lookups and other
        instances can continue reading the last-known-good snapshot while a
        refresh is waiting on Angel One TLS/network timeouts.
        """
        cls = self.__class__
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()

        def cached_for_today():
            with cls._cache_lock:
                if not force and self._loaded and self.instruments and cls._cached_snapshot_date == today:
                    return self.instruments
                if not force and cls._cached_instruments is not None and cls._cached_snapshot_date == today:
                    self.instruments = cls._cached_instruments
                    self._loaded = True
                    self._symbol_exchange_index = None
                    return self.instruments
            return None

        cached = cached_for_today()
        if cached is not None:
            return cached

        # Recheck after acquiring the single-flight lock: another caller may
        # have completed the refresh while this caller was waiting.
        with cls._download_lock:
            cached = cached_for_today()
            if cached is not None:
                return cached

            now = time.monotonic()
            if (
                not force
                and cls._last_download_error
                and now - cls._last_download_attempt_at < cls._retry_after_seconds
            ):
                with cls._cache_lock:
                    disk_fallback = cls._cached_instruments if cls._cached_snapshot_date is None else None
                    fetched_at = cls._cached_disk_snapshot_fetched_at
                    if disk_fallback and fetched_at is not None:
                        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
                        if 0 <= age <= cls._disk_fallback_max_age_seconds:
                            self.instruments = disk_fallback
                            self._loaded = True
                            self._disk_fallback_fetched_at = fetched_at
                            self._symbol_exchange_index = None
                            return self.instruments
                    if cls._cached_snapshot_date is None and cls._cached_disk_snapshot_fetched_at is not None:
                        cls._cached_instruments = None
                        cls._cached_disk_snapshot_fetched_at = None
                        self.instruments = []
                        self._loaded = False
                        self._disk_fallback_fetched_at = None
                        self._symbol_exchange_index = None
                raise TradingAppException(
                    "InstrumentMasterDownloadCooldown",
                    "Instrument-master download recently failed; retry is temporarily throttled.",
                    502,
                )

            disk_snapshot = cls._read_disk_snapshot()
            cls._last_download_attempt_at = now
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
                        source_errors.append(
                            f"{source_url}: {type(source_error).__name__}: {source_error}"
                        )
                        app_logger.warning(
                            "Instrument master source failed; trying official alternate host: {}",
                            source_errors[-1],
                        )
                if data is None:
                    raise RuntimeError(
                        "All official Angel One instrument-master hosts failed: "
                        + " | ".join(source_errors)
                    )

                # Publish atomically under the short-lived cache lock only
                # after the complete payload has passed transport validation.
                with cls._cache_lock:
                    cls._cached_instruments = data
                    cls._cached_snapshot_date = today
                    cls._last_download_error = None
                    cls._cached_disk_snapshot_fetched_at = None
                    self.instruments = data
                    self._loaded = True
                    self._disk_fallback_fetched_at = None
                    self._symbol_exchange_index = None

                # Disk persistence is best-effort and happens only after the
                # complete official payload has passed master_transport validation.
                cls._write_disk_snapshot(data)
                app_logger.info(
                    "Loaded {} instruments from Angel One instrument master",
                    len(data),
                )
                return data
            except TradingAppException:
                raise
            except Exception as exc:
                cls._last_download_error = f"{type(exc).__name__}: {exc}"
                if disk_snapshot is not None:
                    rows, fetched_at = disk_snapshot
                    with cls._cache_lock:
                        cls._cached_instruments = rows
                        # None prevents an old disk snapshot from masquerading
                        # as today's successful provider refresh.
                        cls._cached_snapshot_date = None
                        cls._cached_disk_snapshot_fetched_at = fetched_at
                        self.instruments = rows
                        self._loaded = True
                        self._disk_fallback_fetched_at = fetched_at
                        self._symbol_exchange_index = None
                    app_logger.warning(
                        "Instrument master provider unavailable; using validated disk snapshot fetched_at={}",
                        fetched_at.isoformat(),
                    )
                    return rows
                app_logger.error("Failed to download instrument master: {}", str(exc))
                raise TradingAppException("InstrumentMasterDownloadError", str(exc), 502)

    def search(
        self,
        tradingsymbol: Optional[str] = None,
        exchange: Optional[str] = None,
        symboltoken: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search instruments by symbol, exchange, or token."""
        # An explicitly supplied empty snapshot is a valid test/offline state and
        # must not trigger an unexpected network request.
        if self._disk_fallback_fetched_at is not None:
            age = (datetime.now(timezone.utc) - self._disk_fallback_fetched_at).total_seconds()
            if age < 0 or age > self.__class__._disk_fallback_max_age_seconds:
                self.instruments = []
                self._loaded = False
                self._disk_fallback_fetched_at = None
                self._symbol_exchange_index = None
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

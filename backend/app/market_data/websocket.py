import math
import ssl
import time
from threading import Event, Lock, Thread, current_thread
from typing import Any, Callable, Optional

import websocket as websocket_client
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from app.algo.auth import AngelOneAuth
from app.core.config import settings
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger




def _connect_sdk_socket_with_verified_tls(socket: Any) -> None:
    """Start the transport in a tracked thread and return after handshake outcome.

    The shared broker admission lock may cover this function, so it must return
    after open/error/timeout rather than after the long-lived run_forever loop.
    This serializes connection establishment while allowing healthy sockets to
    run independently once their handshake succeeds.
    """
    headers = {
        "Authorization": socket.auth_token,
        "x-api-key": socket.api_key,
        "x-client-code": socket.client_code,
        "x-feed-token": socket.feed_token,
    }
    ready = Event()
    outcome: dict[str, Any] = {}
    original_open = socket.on_open
    original_error = socket.on_error

    def on_open_and_signal(wsapp):
        try:
            if callable(original_open):
                original_open(wsapp)
            outcome["opened"] = True
        finally:
            ready.set()

    def on_error_and_signal(wsapp, error):
        try:
            if callable(original_error):
                original_error(wsapp, error)
            outcome["error"] = str(error)
        finally:
            ready.set()

    socket.on_open = on_open_and_signal
    socket.on_error = on_error_and_signal
    socket.wsapp = websocket_client.WebSocketApp(
        socket.ROOT_URI,
        header=headers,
        on_open=socket._on_open,
        on_error=socket._on_error,
        on_close=socket._on_close,
        on_data=socket._on_data,
        on_ping=socket._on_ping,
        on_pong=socket._on_pong,
    )

    def run_transport():
        try:
            socket.wsapp.run_forever(
                sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                ping_interval=10,
                ping_timeout=5,
            )
        finally:
            if not ready.is_set():
                outcome["error"] = "transport exited before open"
                ready.set()

    worker = Thread(target=run_transport, name="angel-ws-transport", daemon=True)
    socket._transport_thread = worker
    worker.start()
    timeout = max(1.0, float(getattr(socket, "_connect_handshake_timeout_seconds", 15.0)))
    if not ready.wait(timeout=timeout):
        try:
            socket.close_connection()
        finally:
            raise TimeoutError(f"Angel WebSocket handshake timed out after {timeout:.1f}s")
    if not outcome.get("opened"):
        detail = outcome.get("error") or "transport exited before open"
        raise ConnectionError(f"Angel WebSocket failed before open: {detail}")



class MarketDataWebSocket:
    """Resilient Angel One SmartAPI WebSocket V2 client."""

    def __init__(self, auth: Optional[AngelOneAuth] = None, auto_reconnect: bool = True):
        self.auth = auth or AngelOneAuth()
        self.websocket = None
        self.exchange_type: Optional[int] = None
        self.mode: int = 1
        self.correlation_id: str = "market-data"
        self.tokens: list[str] = []
        self.subscriptions: dict[int, list[str]] = {}
        self.on_data: Optional[Callable] = None
        self.on_failure: Optional[Callable[[str], None]] = None
        self._lock = Lock()
        self._connected = False
        self._stopping = False
        self._reconnect_attempts = 3
        self._reconnect_delay_seconds = 2.0
        self._reconnect_thread = None
        self._is_reconnecting = False
        self._consecutive_failures = 0
        self._error_count = 0
        self._reconnect_count = 0
        self._last_error = None
        self._reconnect_lock = Lock()
        self._auto_reconnect = bool(auto_reconnect)
        # SDK subscribe calls can exceed a quarter-second under ordinary jitter.
        # Keep a bounded failure path without invalidating healthy sockets too eagerly.
        self._socket_operation_timeout_seconds = 5.0
        self._connecting = False

    @property
    def health(self) -> dict:
        with self._lock:
            return {"connected": self._connected, "stopping": self._stopping, "error_count": self._error_count, "reconnect_count": self._reconnect_count, "consecutive_failures": self._consecutive_failures, "last_error": self._last_error}

    @property
    def connecting(self) -> bool:
        with self._lock:
            return self._connecting

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def mark_session_stale(self) -> None:
        """Defer a forced fresh login until the next admitted handshake."""
        marker = getattr(self.auth, "mark_session_stale", None)
        if callable(marker):
            marker()

    def _token_groups(self) -> list[dict]:
        return [{"exchangeType": exchange_type, "tokens": list(tokens)} for exchange_type, tokens in sorted(self.subscriptions.items()) if tokens]

    def _set_subscriptions(self, subscriptions: dict[int, list[str]]) -> None:
        normalized: dict[int, list[str]] = {}
        for exchange_type, tokens in subscriptions.items():
            if not isinstance(exchange_type, int) or isinstance(exchange_type, bool) or exchange_type <= 0:
                raise ValueError("subscription exchange_type must be a positive integer")
            values = [str(token).strip() for token in tokens if str(token).strip()]
            if values:
                normalized[exchange_type] = list(dict.fromkeys(values))
        if not normalized:
            raise ValueError("at least one WebSocket subscription is required")
        self.subscriptions = normalized
        self.exchange_type = next(iter(normalized))
        self.tokens = list(normalized[self.exchange_type])

    def _build_socket(self):
        # A VPS process can outlive the Angel session. Refresh before every
        # socket creation so reconnects never reuse an expired JWT/feed token.
        self.auth.ensure_session_fresh(settings.ANGEL_SESSION_REFRESH_INTERVAL_SECONDS)
        session_data = self.auth.session_data or {}
        auth_token = session_data.get("jwtToken")
        feed_token = session_data.get("feedToken")
        if not auth_token or not feed_token:
            raise TradingAppException("WebSocketAuthError", "JWT token or feed token is missing.", 401)
        socket = SmartWebSocketV2(auth_token, self.auth.api_key, self.auth.client_id, feed_token)

        # SmartWebSocketV2's built-in _on_error() closes and reconnects the
        # same SDK instance. Its on_close callback then triggers our common
        # manager to replace that socket concurrently, creating overlapping
        # sessions and repeated disconnect/reconnect loops. The application
        # already owns recovery, so forward the original SDK error directly
        # and let exactly one recovery owner replace the socket.
        def forward_sdk_error(wsapp, error):
            callback = getattr(socket, "on_error", None)
            if callable(callback):
                callback(wsapp, error)

        socket._on_error = forward_sdk_error

        # Override only the real Angel SDK transport. Test doubles and alternate
        # socket adapters keep their own connect() implementation.
        if all(hasattr(socket, attr) for attr in (
            "ROOT_URI", "auth_token", "api_key", "client_code", "feed_token",
            "_on_open", "_on_close", "_on_data", "_on_ping", "_on_pong",
        )):
            socket.connect = lambda: _connect_sdk_socket_with_verified_tls(socket)

        def handle_open(wsapp):
            with self._lock:
                self._connected = True
                self._connecting = False
            with self._lock:
                self._consecutive_failures = 0
                self._last_error = None
            app_logger.info("Angel One WebSocket connected")
            if self.exchange_type is not None and self.tokens:
                self._subscribe_initial_bounded(socket)

        def handle_data(wsapp, message):
            if self.on_data:
                self.on_data(message)

        def handle_error(wsapp, error):
            with self._lock:
                self._connected = False
                self._connecting = False
                self._error_count += 1
                self._consecutive_failures += 1
                error_detail = (
                    f"{type(error).__name__}: {error}"
                    if isinstance(error, BaseException)
                    else str(error)
                )
                self._last_error = error_detail
                failure_callback = self.on_failure
            app_logger.error("Angel One WebSocket error: %s", error_detail)
            if failure_callback is not None:
                try:
                    failure_callback(error_detail)
                except Exception as exc:
                    app_logger.error(f"Angel One WebSocket failure notification failed: {exc}")
            if self._auto_reconnect:
                self._schedule_reconnect()

        def handle_close(wsapp):
            with self._lock:
                self._connected = False
                self._connecting = False
                stopping = self._stopping
                failure_callback = self.on_failure
            close_code = getattr(wsapp, "close_status_code", None)
            close_reason = getattr(wsapp, "close_reason", None)
            app_logger.warning(
                "Angel One WebSocket connection closed code=%s reason=%s",
                close_code,
                close_reason,
            )
            if not stopping and failure_callback is not None:
                try:
                    failure_callback("socket closed")
                except Exception as exc:
                    app_logger.error(f"Angel One WebSocket close notification failed: {exc}")
            if not stopping and self._auto_reconnect:
                self._schedule_reconnect()

        socket.on_open = handle_open
        socket.on_data = handle_data
        socket.on_error = handle_error
        socket.on_close = handle_close
        return socket

    def _subscribe_initial_bounded(self, socket: Any) -> None:
        """Bound the SDK subscribe call made from Angel's on_open callback."""
        completed = Event()
        failure: dict[str, Exception] = {}
        payload = self._token_groups()
        correlation_id = self.correlation_id
        mode = self.mode

        def _subscribe() -> None:
            try:
                socket.subscribe(correlation_id, mode, payload)
            except Exception as exc:
                failure["exception"] = exc
            finally:
                completed.set()

        Thread(target=_subscribe, name="angel-ws-initial-subscribe", daemon=True).start()
        finished = completed.wait(timeout=max(0.01, self._socket_operation_timeout_seconds))
        error = failure.get("exception")
        if finished and error is None:
            return
        if not finished:
            reason = f"initial subscribe exceeded {self._socket_operation_timeout_seconds:.2f}s"
        else:
            reason = f"initial subscribe failed: {type(error).__name__}: {error}"
        app_logger.error("Angel One WebSocket %s", reason)
        with self._lock:
            self._connected = False
            self._last_error = reason
            self._consecutive_failures += 1
            failure_callback = self.on_failure
        if failure_callback is not None:
            try:
                failure_callback(reason)
            except Exception as exc:
                app_logger.error(f"Angel One WebSocket initial-subscribe notification failed: {exc}")

    def _schedule_reconnect(self) -> None:
        with self._reconnect_lock:
            if self._stopping:
                return
            if self._is_reconnecting:
                return
            self._is_reconnecting = True
            self._reconnect_count += 1
            self._reconnect_thread = Thread(
                target=self._reconnect_after_disconnect,
                name="angel-ws-reconnect",
                daemon=True,
            )
            self._reconnect_thread.start()

    def _reconnect_after_disconnect(self) -> None:
        retry_after_exit = False
        with self._lock:
            exchange_type = self.exchange_type
            tokens = list(self.tokens)
            mode = self.mode
            correlation_id = self.correlation_id
            on_data = self.on_data
            on_failure = self.on_failure
        if exchange_type is None or not tokens:
            return
        reconnect_subscriptions = dict(self.subscriptions) or {exchange_type: tokens}
        try:
            self.connect(
                subscriptions=reconnect_subscriptions,
                mode=mode,
                correlation_id=correlation_id,
                on_data=on_data,
                on_failure=on_failure,
                reconnect_attempts=self._reconnect_attempts,
                reconnect_delay_seconds=self._reconnect_delay_seconds,
            )
        except Exception as exc:
            with self._lock:
                self._error_count += 1
                self._consecutive_failures += 1
                self._last_error = str(exc)
                stopping = self._stopping
                failures = self._consecutive_failures
            app_logger.error(f"Angel One WebSocket reconnect failed: {exc}")
            if not stopping:
                delay = min(60.0, self._reconnect_delay_seconds * (2 ** min(failures - 1, 5)))
                time.sleep(delay)
                retry_after_exit = True
        finally:
            with self._reconnect_lock:
                if self._reconnect_thread is current_thread():
                    self._reconnect_thread = None
                self._is_reconnecting = False
                should_retry = retry_after_exit and not self._stopping
            if should_retry:
                self._schedule_reconnect()

    def connect(self, exchange_type: int | None = None, tokens: list[str] | None = None, mode: int = 1, correlation_id: str = "market-data", on_data: Optional[Callable] = None, reconnect_attempts: int = 3, reconnect_delay_seconds: float = 2.0, subscriptions: Optional[dict[int, list[str]]] = None, on_failure: Optional[Callable[[str], None]] = None):
        """Connect and retry failed starts while preserving validated subscriptions."""
        if exchange_type is not None and (not isinstance(exchange_type, int) or isinstance(exchange_type, bool) or exchange_type <= 0):
            raise ValueError("exchange_type must be a positive integer")
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        if not isinstance(reconnect_attempts, int) or isinstance(reconnect_attempts, bool) or reconnect_attempts < 0:
            raise ValueError("reconnect_attempts must be a non-negative integer")
        if not math.isfinite(float(reconnect_delay_seconds)) or reconnect_delay_seconds < 0:
            raise ValueError("reconnect_delay_seconds must be finite and non-negative")
        if not str(correlation_id).strip():
            raise ValueError("correlation_id is required")
        if subscriptions is None:
            if exchange_type is None or tokens is None:
                raise ValueError("exchange_type and tokens are required when subscriptions is omitted")
            subscriptions = {exchange_type: tokens}
        elif exchange_type is not None or tokens is not None:
            raise ValueError("use either subscriptions or exchange_type/tokens, not both")
        if on_data is not None and not callable(on_data):
            raise ValueError("on_data must be callable")
        with self._lock:
            self._set_subscriptions(subscriptions)
            self.mode = mode
            self.correlation_id = correlation_id.strip()
            self.on_data = on_data
            self.on_failure = on_failure
            self._stopping = False
            self._connecting = True
            self._reconnect_attempts = reconnect_attempts
            self._reconnect_delay_seconds = reconnect_delay_seconds
        last_error = None
        for attempt in range(reconnect_attempts + 1):
            with self._lock:
                if self._stopping:
                    return
            try:
                self.websocket = self._build_socket()
                self.websocket.connect()
                with self._lock:
                    # on_open may report initial-subscription failure before connect returns.
                    if self._connected and self._last_error is None:
                        self._consecutive_failures = 0
                return
            except Exception as exc:
                last_error = exc
                with self._lock:
                    self._connected = False
                    self._connecting = False
                    self._error_count += 1
                    self._consecutive_failures += 1
                    self._last_error = str(exc)
                app_logger.error(f"WebSocket connection attempt {attempt + 1} failed: {exc}")
                if attempt < reconnect_attempts:
                    time.sleep(reconnect_delay_seconds * (attempt + 1))
        if isinstance(last_error, TradingAppException):
            raise last_error
        raise TradingAppException("WebSocketConnectionError", str(last_error), 502)

    def subscribe_groups(self, groups: dict[int, list[str]], mode: Optional[int] = None) -> None:
        """Add exchange/token groups to the shared Angel session."""
        normalized: dict[int, list[str]] = {}
        for exchange_type, tokens in groups.items():
            if not isinstance(exchange_type, int) or isinstance(exchange_type, bool) or exchange_type <= 0:
                raise ValueError("exchange_type must be a positive integer")
            values = [str(token).strip() for token in tokens if str(token).strip()]
            if values:
                normalized[exchange_type] = list(dict.fromkeys(values))
        if not normalized:
            return
        with self._lock:
            for exchange_type, tokens in normalized.items():
                current = self.subscriptions.setdefault(exchange_type, [])
                self.subscriptions[exchange_type] = list(dict.fromkeys(current + tokens))
            self.exchange_type = next(iter(self.subscriptions))
            self.tokens = list(self.subscriptions[self.exchange_type])
            if mode is not None:
                self.mode = mode
            socket = self.websocket
            connected = self._connected
            correlation_id = self.correlation_id
            current_mode = self.mode
            payload = self._token_groups()
        if socket and connected:
            socket.subscribe(correlation_id, current_mode, payload)

    def unsubscribe_groups(self, groups: dict[int, list[str]]) -> None:
        """Remove exchange/token groups from the shared Angel session."""
        normalized: dict[int, list[str]] = {}
        for exchange_type, tokens in groups.items():
            values = [str(token).strip() for token in tokens if str(token).strip()]
            if values:
                normalized[exchange_type] = list(dict.fromkeys(values))
        if not normalized:
            return
        with self._lock:
            for exchange_type, tokens in normalized.items():
                current = self.subscriptions.get(exchange_type, [])
                remaining = [token for token in current if token not in set(tokens)]
                if remaining:
                    self.subscriptions[exchange_type] = remaining
                else:
                    self.subscriptions.pop(exchange_type, None)
            if self.subscriptions:
                self.exchange_type = next(iter(self.subscriptions))
                self.tokens = list(self.subscriptions[self.exchange_type])
            else:
                self.exchange_type = None
                self.tokens = []
            socket = self.websocket
            connected = self._connected
            correlation_id = self.correlation_id
            current_mode = self.mode
        if socket and connected:
            socket.unsubscribe(correlation_id, current_mode, [{"exchangeType": e, "tokens": t} for e, t in normalized.items()])

    def subscribe(self, tokens: list[str], mode: Optional[int] = None):
        """Replace the remembered token set and subscribe when connected."""
        normalized = [str(token).strip() for token in tokens if str(token).strip()]
        if not normalized:
            raise ValueError("at least one WebSocket token is required")
        if mode is not None and (not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}):
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        with self._lock:
            self.tokens = list(dict.fromkeys(normalized))
            exchange_type = self.exchange_type
            if exchange_type is None:
                raise ValueError("exchange_type is not configured")
            self.subscriptions[exchange_type] = list(self.tokens)
            if mode is not None:
                self.mode = mode
            socket = self.websocket
            exchange_type = self.exchange_type
            connected = self._connected
            correlation_id = self.correlation_id
            current_mode = self.mode
            current_tokens = list(self.tokens)
        if socket and connected and exchange_type is not None:
            socket.subscribe(correlation_id, current_mode, self._token_groups())

    def unsubscribe(self, tokens: list[str]):
        """Unsubscribe tokens and remove them from the remembered set."""
        normalized = [str(token).strip() for token in tokens if str(token).strip()]
        if not normalized:
            return
        with self._lock:
            normalized_set = set(normalized)
            self.tokens = [token for token in self.tokens if token not in normalized_set]
            if self.exchange_type is not None:
                if self.tokens:
                    self.subscriptions[self.exchange_type] = list(self.tokens)
                else:
                    self.subscriptions.pop(self.exchange_type, None)
            socket = self.websocket
            exchange_type = self.exchange_type
            connected = self._connected
            correlation_id = self.correlation_id
            current_mode = self.mode
        if socket and connected and exchange_type is not None:
            socket.unsubscribe(correlation_id, current_mode, [{"exchangeType": exchange_type, "tokens": normalized}])

    def close(self):
        """Stop the socket and disable reconnect/start attempts."""
        with self._lock:
            self._stopping = True
            self._connected = False
            self._connecting = False
            socket = self.websocket
            self.websocket = None
        if socket:
            try:
                socket.close_connection()
            finally:
                pass
        app_logger.info("Angel One WebSocket closed")

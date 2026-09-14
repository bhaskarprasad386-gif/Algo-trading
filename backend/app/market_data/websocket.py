import math
import time
from threading import Lock
from typing import Callable, Optional

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class MarketDataWebSocket:
    """Resilient Angel One SmartAPI WebSocket V2 client."""

    def __init__(self, auth: Optional[AngelOneAuth] = None):
        self.auth = auth or AngelOneAuth()
        self.websocket = None
        self.exchange_type: Optional[int] = None
        self.mode: int = 1
        self.correlation_id: str = "market-data"
        self.tokens: list[str] = []
        self.on_data: Optional[Callable] = None
        self._lock = Lock()
        self._connected = False
        self._stopping = False

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def _build_socket(self):
        if not self.auth.smart_api or not self.auth.session_data:
            self.auth.login()
        session_data = self.auth.session_data or {}
        auth_token = session_data.get("jwtToken")
        feed_token = session_data.get("feedToken")
        if not auth_token or not feed_token:
            raise TradingAppException("WebSocketAuthError", "JWT token or feed token is missing.", 401)
        socket = SmartWebSocketV2(auth_token, self.auth.api_key, self.auth.client_id, feed_token)

        def handle_open(wsapp):
            self._connected = True
            app_logger.info("Angel One WebSocket connected")
            if self.exchange_type is not None and self.tokens:
                socket.subscribe(self.correlation_id, self.mode, [{"exchangeType": self.exchange_type, "tokens": self.tokens}])

        def handle_data(wsapp, message):
            if self.on_data:
                self.on_data(message)

        def handle_error(wsapp, error):
            self._connected = False
            app_logger.error(f"Angel One WebSocket error: {error}")

        def handle_close(wsapp):
            self._connected = False
            app_logger.warning("Angel One WebSocket connection closed")

        socket.on_open = handle_open
        socket.on_data = handle_data
        socket.on_error = handle_error
        socket.on_close = handle_close
        return socket

    def connect(self, exchange_type: int, tokens: list[str], mode: int = 1, correlation_id: str = "market-data", on_data: Optional[Callable] = None, reconnect_attempts: int = 3, reconnect_delay_seconds: float = 2.0):
        """Connect and retry failed starts while preserving validated subscriptions."""
        if not isinstance(exchange_type, int) or isinstance(exchange_type, bool) or exchange_type <= 0:
            raise ValueError("exchange_type must be a positive integer")
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}:
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        if not isinstance(reconnect_attempts, int) or isinstance(reconnect_attempts, bool) or reconnect_attempts < 0:
            raise ValueError("reconnect_attempts must be a non-negative integer")
        if not math.isfinite(float(reconnect_delay_seconds)) or reconnect_delay_seconds < 0:
            raise ValueError("reconnect_delay_seconds must be finite and non-negative")
        if not str(correlation_id).strip():
            raise ValueError("correlation_id is required")
        normalized_tokens = [str(token).strip() for token in tokens if str(token).strip()]
        if not normalized_tokens:
            raise ValueError("at least one WebSocket token is required")
        if on_data is not None and not callable(on_data):
            raise ValueError("on_data must be callable")
        with self._lock:
            self.exchange_type = exchange_type
            self.tokens = list(dict.fromkeys(normalized_tokens))
            self.mode = mode
            self.correlation_id = correlation_id.strip()
            self.on_data = on_data
            self._stopping = False
        last_error = None
        for attempt in range(reconnect_attempts + 1):
            if self._stopping:
                return
            try:
                self.websocket = self._build_socket()
                self.websocket.connect()
                return
            except Exception as exc:
                last_error = exc
                self._connected = False
                app_logger.error(f"WebSocket connection attempt {attempt + 1} failed: {exc}")
                if attempt < reconnect_attempts:
                    time.sleep(reconnect_delay_seconds * (attempt + 1))
        if isinstance(last_error, TradingAppException):
            raise last_error
        raise TradingAppException("WebSocketConnectionError", str(last_error), 502)

    def subscribe(self, tokens: list[str], mode: Optional[int] = None):
        """Replace the remembered token set and subscribe when connected."""
        normalized = [str(token).strip() for token in tokens if str(token).strip()]
        if not normalized:
            raise ValueError("at least one WebSocket token is required")
        if mode is not None and (not isinstance(mode, int) or isinstance(mode, bool) or mode not in {1, 2, 3, 4}):
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        with self._lock:
            self.tokens = list(dict.fromkeys(normalized))
            if mode is not None:
                self.mode = mode
            socket = self.websocket
            exchange_type = self.exchange_type
        if socket and self._connected and exchange_type is not None:
            socket.subscribe(self.correlation_id, self.mode, [{"exchangeType": exchange_type, "tokens": self.tokens}])

    def unsubscribe(self, tokens: list[str]):
        """Unsubscribe tokens and remove them from the remembered set."""
        normalized = [str(token).strip() for token in tokens if str(token).strip()]
        if not normalized:
            return
        with self._lock:
            self.tokens = [token for token in self.tokens if token not in set(normalized)]
            socket = self.websocket
            exchange_type = self.exchange_type
        if socket and self._connected and exchange_type is not None:
            socket.unsubscribe(self.correlation_id, self.mode, [{"exchangeType": exchange_type, "tokens": normalized}])

    def close(self):
        """Stop the socket and disable reconnect/start attempts."""
        with self._lock:
            self._stopping = True
            self._connected = False
            socket = self.websocket
            self.websocket = None
        if socket:
            try:
                socket.close_connection()
            finally:
                pass
        app_logger.info("Angel One WebSocket closed")

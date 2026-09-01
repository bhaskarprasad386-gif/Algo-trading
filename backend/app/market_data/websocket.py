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
        return self._connected

    def _build_socket(self):
        if not self.auth.smart_api or not self.auth.session_data:
            self.auth.login()

        session_data = self.auth.session_data or {}
        auth_token = session_data.get("jwtToken")
        feed_token = session_data.get("feedToken")
        if not auth_token or not feed_token:
            raise TradingAppException(
                "WebSocketAuthError",
                "JWT token or feed token is missing.",
                401,
            )

        socket = SmartWebSocketV2(
            auth_token,
            self.auth.api_key,
            self.auth.client_id,
            feed_token,
        )

        def handle_open(wsapp):
            self._connected = True
            app_logger.info("Angel One WebSocket connected")
            if self.exchange_type is not None and self.tokens:
                socket.subscribe(
                    self.correlation_id,
                    self.mode,
                    [{"exchangeType": self.exchange_type, "tokens": self.tokens}],
                )

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

    def connect(
        self,
        exchange_type: int,
        tokens: list[str],
        mode: int = 1,
        correlation_id: str = "market-data",
        on_data: Optional[Callable] = None,
        reconnect_attempts: int = 3,
        reconnect_delay_seconds: float = 2.0,
    ):
        """Connect and retry failed starts while preserving subscriptions."""
        with self._lock:
            self.exchange_type = exchange_type
            self.tokens = list(dict.fromkeys(tokens))
            self.mode = mode
            self.correlation_id = correlation_id
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
                app_logger.error(
                    f"WebSocket connection attempt {attempt + 1} failed: {exc}"
                )
                if attempt < reconnect_attempts:
                    time.sleep(reconnect_delay_seconds * (attempt + 1))

        if isinstance(last_error, TradingAppException):
            raise last_error
        raise TradingAppException(
            "WebSocketConnectionError",
            str(last_error),
            502,
        )

    def subscribe(self, tokens: list[str], mode: Optional[int] = None):
        """Replace the remembered token set and subscribe when connected."""
        with self._lock:
            self.tokens = list(dict.fromkeys(tokens))
            if mode is not None:
                self.mode = mode
            socket = self.websocket
            exchange_type = self.exchange_type

        if socket and self._connected and exchange_type is not None and self.tokens:
            socket.subscribe(
                self.correlation_id,
                self.mode,
                [{"exchangeType": exchange_type, "tokens": self.tokens}],
            )

    def unsubscribe(self, tokens: list[str]):
        """Unsubscribe tokens and remove them from the remembered set."""
        with self._lock:
            self.tokens = [token for token in self.tokens if token not in set(tokens)]
            socket = self.websocket
            exchange_type = self.exchange_type

        if socket and self._connected and exchange_type is not None and tokens:
            socket.unsubscribe(
                self.correlation_id,
                self.mode,
                [{"exchangeType": exchange_type, "tokens": tokens}],
            )

    def close(self):
        """Stop the socket and disable reconnect/start attempts."""
        self._stopping = True
        self._connected = False
        if self.websocket:
            try:
                self.websocket.close_connection()
            finally:
                self.websocket = None
        app_logger.info("Angel One WebSocket closed")

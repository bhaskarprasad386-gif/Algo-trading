from typing import Callable, Optional

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class MarketDataWebSocket:
    """Angel One SmartAPI WebSocket V2 market-data client."""

    def __init__(self, auth: Optional[AngelOneAuth] = None):
        self.auth = auth or AngelOneAuth()
        self.websocket = None

    def connect(
        self,
        exchange_type: int,
        tokens: list[str],
        mode: int = 1,
        correlation_id: str = "market-data",
        on_data: Optional[Callable] = None,
    ):
        """Connect to Angel One WebSocket V2 and subscribe to tokens."""

        try:
            login_data = self.auth.login()

            auth_token = login_data.get("jwt_token")
            feed_token = login_data.get("feed_token")

            if not auth_token or not feed_token:
                raise TradingAppException(
                    "WebSocketAuthError",
                    "JWT token or feed token is missing.",
                    401,
                )

            self.websocket = SmartWebSocketV2(
                auth_token,
                self.auth.api_key,
                self.auth.client_id,
                feed_token,
            )

            token_list = [
                {
                    "exchangeType": exchange_type,
                    "tokens": tokens,
                }
            ]

            def handle_open(wsapp):
                app_logger.info("Angel One WebSocket connected")
                self.websocket.subscribe(
                    correlation_id,
                    mode,
                    token_list,
                )

            def handle_data(wsapp, message):
                app_logger.info(f"Market tick received: {message}")

                if on_data:
                    on_data(message)

            def handle_error(wsapp, error):
                app_logger.error(
                    f"Angel One WebSocket error: {error}"
                )

            def handle_close(wsapp):
                app_logger.info(
                    "Angel One WebSocket connection closed"
                )

            self.websocket.on_open = handle_open
            self.websocket.on_data = handle_data
            self.websocket.on_error = handle_error
            self.websocket.on_close = handle_close

            self.websocket.connect()

        except TradingAppException:
            raise

        except Exception as e:
            app_logger.error(
                f"Failed to start Angel One WebSocket: {str(e)}"
            )
            raise TradingAppException(
                "WebSocketConnectionError",
                str(e),
                502,
            )

    def close(self):
        """Close the active WebSocket connection."""
        if self.websocket:
            self.websocket.close_connection()
            self.websocket = None
            app_logger.info("Angel One WebSocket closed")

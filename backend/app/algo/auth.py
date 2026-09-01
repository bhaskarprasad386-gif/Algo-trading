from typing import Optional, Dict, Any
import pyotp
from SmartApi import SmartConnect

from app.core.config import settings
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class AngelOneAuth:
    """Handles Angel One SmartAPI authentication and session management."""

    def __init__(self):
        self.api_key = settings.angel_api_key
        self.client_id = settings.angel_client_id
        self.password = settings.angel_password
        self.totp_secret = settings.angel_totp_secret

        self.smart_api: Optional[SmartConnect] = None
        self.session_data: Optional[Dict[str, Any]] = None

    def login(self) -> Dict[str, Any]:
        """Login to Angel One and generate session tokens."""
        try:
            if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
                raise TradingAppException(
                    "ConfigError",
                    "Angel One credentials are not properly set in .env",
                    400,
                )

            self.smart_api = SmartConnect(api_key=self.api_key)

            totp = pyotp.TOTP(self.totp_secret).now()

            data = self.smart_api.generateSession(
                self.client_id,
                self.password,
                totp
            )

            if not data or not data.get("status"):
                message = data.get("message", "Login failed") if data else "Empty response from Angel One"
                raise TradingAppException("LoginError", message, 401)

            self.session_data = data.get("data", {})
            app_logger.info("Angel One login successful")

            return {
                "status": "success",
                "message": "Login successful",
                "client_code": self.session_data.get("clientcode"),
                "jwt_token": self.session_data.get("jwtToken"),
                "feed_token": self.session_data.get("feedToken"),
                "refresh_token": self.session_data.get("refreshToken"),
            }

        except TradingAppException:
            raise
        except Exception as e:
            app_logger.error(f"Angel One login failed: {str(e)}")
            raise TradingAppException(
                "LoginError",
                f"Angel One login failed: {str(e)}",
                500,
            )

    def get_client(self) -> SmartConnect:
        """Return authenticated SmartAPI client. Auto-login if needed."""
        if self.smart_api is None or self.session_data is None:
            self.login()
        return self.smart_api

    def get_session_data(self) -> Dict[str, Any]:
        """Return current session data."""
        if not self.session_data:
            self.login()
        return self.session_data or {}

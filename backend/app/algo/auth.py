from typing import Optional, Dict, Any
import pyotp
from SmartApi import SmartConnect

from app.core.config import settings
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class AngelOneAuth:
    """Angel One SmartAPI authentication and session manager."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.api_key = settings.angel_api_key
        self.client_id = settings.angel_client_id
        self.password = settings.angel_password
        self.totp_secret = settings.angel_totp_secret

        self.smart_api: Optional[SmartConnect] = None
        self.session_data: Optional[Dict[str, Any]] = None
        self.is_logged_in: bool = False
        self._initialized = True

    def _validate_credentials(self) -> None:
        if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
            raise TradingAppException(
                "ConfigError",
                "Angel One credentials missing. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET in .env",
                400,
            )

    def login(self) -> Dict[str, Any]:
        """Login to Angel One and store session tokens server-side."""
        try:
            self._validate_credentials()
            self.smart_api = SmartConnect(api_key=self.api_key)
            totp = pyotp.TOTP(self.totp_secret).now()
            data = self.smart_api.generateSession(self.client_id, self.password, totp)

            if not data or not data.get("status"):
                message = data.get("message", "Login failed") if data else "Empty response from Angel One"
                self.is_logged_in = False
                raise TradingAppException("LoginError", message, 401)

            self.session_data = data.get("data", {})
            self.is_logged_in = True
            app_logger.info("Angel One login successful")

            return {
                "status": "success",
                "message": "Login successful",
                "client_code": self.session_data.get("clientcode"),
            }

        except TradingAppException:
            raise
        except Exception as e:
            self.is_logged_in = False
            app_logger.error(f"Angel One login failed: {str(e)}")
            raise TradingAppException("LoginError", f"Angel One login failed: {str(e)}", 500)

    def refresh_session(self) -> Dict[str, Any]:
        """Refresh session using the server-side refresh token."""
        try:
            if not self.smart_api or not self.session_data:
                return self.login()

            refresh_token = self.session_data.get("refreshToken")
            if not refresh_token:
                return self.login()

            data = self.smart_api.generateToken(refresh_token)
            if not data or not data.get("status"):
                app_logger.warning("Refresh failed, doing full login again")
                return self.login()

            self.session_data.update(data.get("data", {}))
            self.is_logged_in = True
            app_logger.info("Angel One session refreshed")
            return {"status": "success", "message": "Session refreshed"}

        except Exception as e:
            app_logger.error(f"Session refresh failed: {str(e)}")
            return self.login()

    def get_client(self) -> SmartConnect:
        """Return authenticated SmartAPI client. Auto-login if needed."""
        if not self.is_logged_in or self.smart_api is None or self.session_data is None:
            self.login()
        return self.smart_api

    def get_session_data(self) -> Dict[str, Any]:
        """Return current server-side session data."""
        if not self.session_data:
            self.login()
        return self.session_data or {}

    def get_feed_token(self) -> Optional[str]:
        return self.get_session_data().get("feedToken")

    def get_jwt_token(self) -> Optional[str]:
        return self.get_session_data().get("jwtToken")

    def status(self) -> Dict[str, Any]:
        """Return safe authentication status without exposing tokens."""
        return {
            "is_logged_in": self.is_logged_in,
            "client_code": (self.session_data or {}).get("clientcode"),
            "has_jwt": bool((self.session_data or {}).get("jwtToken")),
            "has_feed_token": bool((self.session_data or {}).get("feedToken")),
            "has_refresh_token": bool((self.session_data or {}).get("refreshToken")),
        }

    def logout(self) -> Dict[str, Any]:
        """Clear the local server-side session."""
        self.smart_api = None
        self.session_data = None
        self.is_logged_in = False
        app_logger.info("Angel One session cleared")
        return {"status": "success", "message": "Logged out"}

from typing import Optional, Dict, Any
from threading import RLock
import time
import pyotp
from SmartApi import SmartConnect

from app.core.config import settings
from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class AngelOneAuth:
    """Angel One SmartAPI authentication and session manager."""

    _instance = None
    _instance_lock = RLock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        with self._instance_lock:
            if self._initialized:
                return
            self.api_key = settings.angel_api_key
            self.client_id = settings.angel_client_id
            self.password = settings.angel_password
            self.totp_secret = settings.angel_totp_secret
            self.smart_api: Optional[SmartConnect] = None
            self.session_data: Optional[Dict[str, Any]] = None
            self.is_logged_in: bool = False
            self._state_lock = RLock()
            self._session_obtained_at = 0.0
            # A broker connection-limit response can invalidate an otherwise young session.
            # Keep this flag set until a full login succeeds; failed logins must not
            # silently fall back to the stale JWT/feedToken pair.
            self._force_fresh_session = False
            # Coalesce repeated login failures instead of hammering the broker.
            self._login_retry_after = 0.0
            self._login_failure_count = 0
            self._initialized = True

    def _validate_credentials(self) -> None:
        if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
            raise TradingAppException("ConfigError", "Angel One credentials missing. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET in .env", 400)

    def login(self) -> Dict[str, Any]:
        """Login to Angel One and store session tokens server-side."""
        with self._state_lock:
            now = time.monotonic()
            if now < self._login_retry_after:
                remaining = self._login_retry_after - now
                raise TradingAppException(
                    "LoginCooldown",
                    f"Angel One login suppressed after a previous failure; retry in {remaining:.1f}s",
                    429 if remaining >= 60.0 else 503,
                )
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
                if not isinstance(self.session_data, dict) or not self.session_data.get("jwtToken"):
                    self.is_logged_in = False
                    raise TradingAppException("LoginError", "Angel One login response missing session token", 502)
                self.is_logged_in = True
                self._session_obtained_at = time.monotonic()
                self._force_fresh_session = False
                self._login_retry_after = 0.0
                self._login_failure_count = 0
                app_logger.info("Angel One login successful")
                return {"status": "success", "message": "Login successful", "client_code": self.session_data.get("clientcode")}
            except TradingAppException as exc:
                self.is_logged_in = False
                self._login_failure_count += 1
                message = str(exc).lower()
                cooldown = 90.0 if any(term in message for term in (
                    "access rate", "429", "too many requests", "rate limit",
                    "connection limit exceeded",
                )) else min(30.0 * self._login_failure_count, 120.0)
                self._login_retry_after = time.monotonic() + cooldown
                app_logger.warning(
                    "Angel One login failed; retry suppressed for {:.0f}s (failure_count={}, category={})",
                    cooldown, self._login_failure_count,
                    "rate_limited" if cooldown == 90.0 else "authentication_or_transport",
                )
                raise
            except Exception as exc:
                self.is_logged_in = False
                self._login_failure_count += 1
                message = str(exc).lower()
                cooldown = 90.0 if any(term in message for term in (
                    "access rate", "429", "too many requests", "rate limit",
                    "connection limit exceeded",
                )) else min(30.0 * self._login_failure_count, 120.0)
                self._login_retry_after = time.monotonic() + cooldown
                app_logger.warning(
                    "Angel One login transport failure; retry suppressed for {:.0f}s (failure_count={}, category={})",
                    cooldown, self._login_failure_count,
                    "rate_limited" if cooldown == 90.0 else "authentication_or_transport",
                )
                raise TradingAppException("LoginError", "Angel One login failed; retry is temporarily suppressed", 500)

    def refresh_session(self) -> Dict[str, Any]:
        """Refresh session using the server-side refresh token."""
        with self._state_lock:
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
                refreshed = data.get("data", {})
                if not isinstance(refreshed, dict) or not refreshed.get("jwtToken") or not refreshed.get("feedToken"):
                    app_logger.warning("Refresh response missing JWT/feed token, doing full login again")
                    return self.login()
                self.session_data.update(refreshed)
                self.is_logged_in = True
                self._session_obtained_at = time.monotonic()
                self._force_fresh_session = False
                app_logger.info("Angel One session refreshed")
                return {"status": "success", "message": "Session refreshed"}
            except TradingAppException:
                raise
            except Exception:
                # SDK/HTTP exceptions may include request headers or authentication data.
                # Keep secrets out of application logs; the retry path is handled by login().
                app_logger.warning("Angel One session refresh failed; attempting guarded login")
                return self.login()

    def mark_session_stale(self) -> None:
        """Force fresh JWT/feed-token generation before the next WebSocket handshake.

        Use this after broker connection-limit/handshake failures. Do not log in
        here: the caller may still be inside the shared cooldown, and multiple
        socket failures must coalesce into one refresh on the next admitted connect.
        """
        with self._state_lock:
            self._force_fresh_session = True

    def ensure_session_fresh(self, max_age_seconds: float) -> Dict[str, Any]:
        """Refresh the Angel session before using a potentially stale JWT/feed token."""
        try:
            max_age = float(max_age_seconds)
        except (TypeError, ValueError):
            raise ValueError("max_age_seconds must be numeric")
        if max_age <= 0:
            raise ValueError("max_age_seconds must be positive")
        with self._state_lock:
            # A 429/connection-limit failure forces a fresh generateSession login
            # even when the current session is younger than the normal age limit.
            # The flag is cleared only by a successful login, so failure cannot
            # silently reuse stale credentials on the next reconnect attempt.
            if self._force_fresh_session:
                return self.refresh_session()
            if not self.is_logged_in or self.smart_api is None or not self.session_data:
                return self.login()
            age = time.monotonic() - self._session_obtained_at
            if age >= max_age:
                return self.refresh_session()
            return {"status": "success", "message": "Session still fresh"}

    def get_client(self) -> SmartConnect:
        """Return authenticated SmartAPI client. Auto-login if needed."""
        with self._state_lock:
            if not self.is_logged_in or self.smart_api is None or self.session_data is None:
                self.login()
            return self.smart_api

    def get_session_data(self) -> Dict[str, Any]:
        """Return a copy of current server-side session data."""
        with self._state_lock:
            if not self.session_data:
                self.login()
            return dict(self.session_data or {})

    def get_feed_token(self) -> Optional[str]:
        return self.get_session_data().get("feedToken")

    def get_jwt_token(self) -> Optional[str]:
        return self.get_session_data().get("jwtToken")

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {"is_logged_in": self.is_logged_in, "client_code": (self.session_data or {}).get("clientcode"), "has_jwt": bool((self.session_data or {}).get("jwtToken")), "has_feed_token": bool((self.session_data or {}).get("feedToken")), "has_refresh_token": bool((self.session_data or {}).get("refreshToken"))}

    def logout(self) -> Dict[str, Any]:
        """Clear the local server-side session."""
        with self._state_lock:
            self.smart_api = None
            self.session_data = None
            self.is_logged_in = False
            self._session_obtained_at = 0.0
            app_logger.info("Angel One session cleared")
            return {"status": "success", "message": "Logged out"}

import pyotp
from SmartApi import SmartConnect
from app.core.config import settings
from app.core.logger import app_logger
from app.core.exceptions import TradingAppException

class AngelOneAuth:
    def __init__(self):
        self.api_key = settings.ANGEL_ONE_API_KEY
        self.client_id = settings.ANGEL_ONE_CLIENT_ID
        self.pin = settings.ANGEL_ONE_PIN
        self.totp_secret = settings.ANGEL_ONE_TOTP_SECRET
        self.smart_api = None
        self.session_data = None

    def generate_totp(self) -> str:
        """Generate TOTP using the secret key for 2FA login"""
        try:
            if not self.totp_secret:
                raise ValueError("TOTP secret is missing in configuration.")
            totp = pyotp.TOTP(self.totp_secret)
            return totp.now()
        except Exception as e:
            app_logger.error(f"Failed to generate TOTP: {str(e)}")
            raise TradingAppException("TOTPGenerationError", f"Could not generate TOTP: {str(e)}", 400)

    def login(self) -> dict:
        """Authenticate with Angel One SmartAPI and generate session tokens"""
        try:
            if not self.api_key or not self.client_id or not self.pin:
                raise TradingAppException("ConfigError", "Angel One credentials are not fully set in environment variables.", 400)

            # Initialize SmartConnect
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate current TOTP
            totp_code = self.generate_totp()

            # Perform login
            data = self.smart_api.generateSession(self.client_id, self.pin, totp_code)

            if data and data.get('status'):
                self.session_data = data.get('data')
                app_logger.info(f"Successfully logged in to Angel One for client ID: {self.client_id}")
                return {
                    "success": True,
                    "message": "Login successful",
                    "jwt_token": self.session_data.get("jwtToken"),
                    "feed_token": self.session_data.get("feedToken")
                }
            else:
                error_msg = data.get('message', 'Unknown error during Angel One login')
                app_logger.error(f"Angel One Login Failed: {error_msg}")
                raise TradingAppException("SmartAPILoginFailed", error_msg, 401)

        except TradingAppException as te:
            raise te
        except Exception as e:
            app_logger.critical(f"Critical error during Angel One authentication: {str(e)}")
            raise TradingAppException("AuthenticationError", str(e), 500)

    def get_client(self):
        """Return the active SmartConnect instance"""
        if not self.smart_api:
            self.login()
        return self.smart_api

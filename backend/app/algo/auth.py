class AngelOneAuth:
    def __init__(self):
        # ✅ Safe defaults अगर credentials empty हैं
        self.api_key = settings.angel_api_key or "dummy"
        self.client_id = settings.angel_client_id or "dummy"
        self.pin = settings.angel_password or "dummy"
        self.totp_secret = settings.angel_totp_secret or "dummy"
        self.smart_api = None
        self.session_data = None

    def login(self) -> dict:
        """Authenticate with Angel One SmartAPI and generate session tokens"""
        try:
            # ✅ Ab credentials empty hone se nahi fail hoga
            if not self.api_key or self.api_key == "dummy":
                raise TradingAppException(
                    "ConfigError", 
                    "Angel One credentials are not set", 
                    400
                )
            # Rest of login...

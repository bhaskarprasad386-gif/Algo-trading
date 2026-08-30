import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "Algo Trading Platform"
    environment: str = "development"
    
    # Angel One SmartAPI Credentials (Placeholder / Env based)
    angel_api_key: str = os.getenv("ANGEL_API_KEY", "")
    angel_client_id: str = os.getenv("ANGEL_CLIENT_ID", "")
    angel_password: str = os.getenv("ANGEL_PASSWORD", "")
    angel_totp_secret: str = os.getenv("ANGEL_TOTP_SECRET", "")

    class Config:
        env_file = ".env"

settings = Settings()

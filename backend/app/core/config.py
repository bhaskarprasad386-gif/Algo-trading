from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "Algo Trading Platform"
    environment: str = "development"
    debug: bool = True
    SECRET_KEY: str = "change-this-super-secret-key-in-production"
    DATABASE_URL: str = "sqlite:///./algo_trading.db"

    # Angel One Credentials
    angel_api_key: str = ""
    angel_client_id: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

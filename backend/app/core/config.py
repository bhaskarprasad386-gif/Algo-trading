from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Algo Trading Platform"
    environment: str = "development"
    debug: bool = False
    SECRET_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./algo_trading.db"

    # Trading safety limits
    MAX_ORDERS_PER_DAY: int = 20
    MAX_QUANTITY_PER_ORDER: int = 1000
    MAX_POSITION_QUANTITY: int = 5000
    MAX_LOSS: float = 10000.0

    # Angel One credentials
    angel_api_key: str = ""
    angel_client_id: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

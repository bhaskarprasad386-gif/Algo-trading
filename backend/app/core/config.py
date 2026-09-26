from pydantic import ConfigDict, model_validator
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

    # Cash-Future history collector
    CASH_FUTURE_HISTORY_ENABLED: bool = False
    CASH_FUTURE_HISTORY_SYMBOLS: str = ""
    CASH_FUTURE_HISTORY_INTERVAL_SECONDS: int = 60

    # Continuous one-second live Cash-Future feed for backtesting
    LIVE_CASH_FUTURE_DATA_ENABLED: bool = False
    LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT: float = 0.0
    LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS: float = 60.0

    # WhatsApp Cloud API alerts; disabled until deployment credentials are supplied.
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_GRAPH_API_VERSION: str = "v23.0"

    # Durable historical-download progress database
    BACKTEST_STATUS_DB: str = "./backtest_download_status.sqlite3"

    # Durable historical market-data database (separate from API/job status)
    BACKTEST_DATA_DB: str = "./backtest_market_data.sqlite3"

    # Durable historical contract-master snapshots
    BACKTEST_CONTRACT_DB: str = "./backtest_contract_master.sqlite3"
    BACKTEST_CONTRACT_MASTER_AUTO_SYNC: bool = True
    BACKTEST_CONTRACT_MASTER_SYNC_INTERVAL_SECONDS: int = 86400

    # Durable historical strategy-run ledger
    BACKTEST_LEDGER_DB: str = "./backtest_strategy_ledger.sqlite3"

    # Durable Universal backtest result ledger (separate from legacy/Cash-Future ledgers)
    BACKTEST_RESULT_LEDGER_DB: str = "./backtest_result_ledger.sqlite3"

    @model_validator(mode="after")
    def validate_security_config(self):
        if self.environment.strip().lower() != "development":
            if len(self.SECRET_KEY) < 32:
                raise ValueError("SECRET_KEY must be set and at least 32 characters in non-development environments")
        return self

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

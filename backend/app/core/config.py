from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Algo Trading Platform"
    APP_UPDATE_VERSION_CODE: int = 1
    APP_UPDATE_VERSION_NAME: str = "1.0"
    APP_UPDATE_NOTES: str = "Initial release"
    APP_UPDATE_APK_URL: str = "https://github.com/bhaskarprasad386-gif/Algo-trading/releases/latest/download/algo-trading-release.apk"
    APP_UPDATE_SHA256: str = ""
    APP_UPDATE_MANDATORY: bool = False
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
    # New live records are partitioned by IST trading day; legacy base data remains readable.
    LIVE_CASH_FUTURE_DAILY_SHARDS_ENABLED: bool = True
    # Shared latest-snapshot cache; bounded in-process now, Redis-ready interface later.
    MARKET_DATA_CACHE_MAX_ENTRIES: int = 2000
    MARKET_DATA_CACHE_TTL_SECONDS: float = 15.0
    MARKET_DATA_INGEST_QUEUE_MAX: int = 5000
    MARKET_DATA_INGEST_BATCH_SIZE: int = 100
    MARKET_DATA_INGEST_FLUSH_SECONDS: float = 5.0
    MARKET_DATA_INGEST_PUT_TIMEOUT_SECONDS: float = 2.0
    LIVE_CALENDAR_SPREAD_DATA_ENABLED: bool = False
    LIVE_CALENDAR_SPREAD_MIN_GAP_POINTS: float = 0.0
    LIVE_CALENDAR_SPREAD_MIN_GROSS_PROFIT: float = 0.0
    LIVE_CALENDAR_SPREAD_RESULT_RETENTION_DAYS: int = 90
    LIVE_SYNTHETIC_DATA_ENABLED: bool = False
    LIVE_SYNTHETIC_MIN_ARBITRAGE_POINTS: float = 0.0
    LIVE_SYNTHETIC_RESULT_RETENTION_DAYS: int = 90
    LIVE_BOX_SPREAD_DATA_ENABLED: bool = False
    LIVE_BOX_SPREAD_MIN_ARBITRAGE_POINTS: float = 0.0
    LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS: int = 90
    # Box-Spread paper automation is opt-in and only touches active PAPER accounts.
    PAPER_BOX_SPREAD_AUTO_CYCLE_ENABLED: bool = False
    PAPER_BOX_SPREAD_AUTO_CYCLE_INTERVAL_SECONDS: int = 5
    PAPER_BOX_SPREAD_AUTO_CYCLE_MIN_PNL: float = 0.0
    LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT: float = 0.0
    # Restrict the 1-second Cash-Future feed to a configured universe; empty means all active F&O stock futures.
    LIVE_CASH_FUTURE_SYMBOLS: str = ""
    LIVE_CASH_FUTURE_PAIR_TOLERANCE_SECONDS: float = 1.0
    LIVE_CASH_FUTURE_MAX_QUOTE_AGE_SECONDS: float = 3.0
    LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS: float = 60.0
    LIVE_CASH_FUTURE_COST_BPS: float = 10.0
    LIVE_CASH_FUTURE_SLIPPAGE_BPS: float = 5.0
    LIVE_CASH_FUTURE_ESTIMATED_COST_PER_LOT: float = 0.0
    LIVE_CASH_FUTURE_SLIPPAGE_PER_LOT: float = 0.0
    LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS: int = 1
    LIVE_CASH_FUTURE_CAPITAL: float = 10_000_000.0
    LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY: int = 0
    LIVE_CASH_FUTURE_RESULT_RETENTION_DAYS: int = 90
    LIVE_CASH_FUTURE_ALERT_MIN_GROSS_PROFIT: float = 0.0
    LIVE_CASH_FUTURE_ALERT_MIN_NET_PROFIT: float = 0.0
    LIVE_CASH_FUTURE_ALERT_MIN_ANNUALIZED_GAP_PCT: float = 0.0
    LIVE_CASH_FUTURE_ALERT_LOTS: int = 1
    LIVE_CASH_FUTURE_RANK_GAP_WEIGHT: float = 0.30
    LIVE_CASH_FUTURE_RANK_NET_WEIGHT: float = 0.25
    LIVE_CASH_FUTURE_RANK_ANNUALIZED_WEIGHT: float = 0.20
    LIVE_CASH_FUTURE_RANK_STABILITY_WEIGHT: float = 0.10
    LIVE_CASH_FUTURE_RANK_LIQUIDITY_WEIGHT: float = 0.15

    # WhatsApp Cloud API alerts; disabled until deployment credentials are supplied.
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_GRAPH_API_VERSION: str = "v23.0"

    # Durable historical-download progress database
    BACKTEST_STATUS_DB: str = "./backtest_download_status.sqlite3"

    # Durable historical market-data database (separate from API/job status)
    BACKTEST_DATA_DB: str = "./backtest_market_data.sqlite3"

    # Provider-neutral archive root for completed daily market-data shards.
    # Empty means archiving is opt-in; no automatic archive is attempted.
    MARKET_DATA_ARCHIVE_ROOT: str = ""

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

    CASH_FUTURE_HISTORY_ENABLED: bool = False
    CASH_FUTURE_HISTORY_SYMBOLS: str = ""
    CASH_FUTURE_HISTORY_INTERVAL_SECONDS: int = 60

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
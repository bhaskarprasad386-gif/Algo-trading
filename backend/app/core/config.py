from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Algo Trading Platform"
    environment: str = "development"
    debug: bool = True

    # Angel One credentials (environment variables se aayenge)
    angel_api_key: str = ""
    angel_client_id: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

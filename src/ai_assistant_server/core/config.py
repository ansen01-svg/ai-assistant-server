from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 1 day
    auth_cookie_name: str = "access_token"
    cookie_secure: bool = False  # True in production (HTTPS)

    anthropic_api_key: SecretStr
    max_output_tokens: int = 4096

    cors_origins: list[str] = ["http://localhost:3000"]

    vapid_private_key: SecretStr | None = None
    vapid_public_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"

    voyage_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # Values are loaded from environment settings.


settings = get_settings()

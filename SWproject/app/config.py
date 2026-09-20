from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 토스증권 Open API 인증 정보
    toss_api_base_url: str = "https://openapi.tossinvest.com/v1"
    toss_access_token: str
    toss_account_seq: str | None = None

    # 실제 문서에 맞는 경로로 .env에서 지정하세요.
    toss_ranking_rising_path: str = "/rankings/rising"
    toss_ranking_falling_path: str = "/rankings/falling"
    toss_ranking_volume_path: str = "/rankings/trading-value"

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 30
    collection_interval_seconds: int = 20
    ranking_limit: int = 50
    request_spacing_seconds: float = 0.25
    http_timeout_seconds: float = 10.0

    # 예: chrome-extension://abcdefghijklmnop 또는 *
    cors_origins: str = "*"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
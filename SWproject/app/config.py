from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 공식 REST API 호스트. 토큰 발급도 같은 호스트를 사용합니다.
    toss_api_base_url: str = "https://openapi.tossinvest.com"
    toss_client_id: str
    toss_client_secret: str

    # 랭킹 조회는 공식 단일 엔드포인트입니다.
    toss_ranking_path: str = "/api/v1/rankings"

    # 아래 값은 공식 OpenAPI 스키마의 실제 parameter 이름/허용 값에 맞춰 수정하세요.
    # 예시 값: TOP_GAINERS, TOP_LOSERS, TRADING_VALUE
    ranking_type_param: str = "type"
    ranking_market_param: str = "marketCountry"
    ranking_duration_param: str = "duration"
    ranking_limit_param: str = "limit"
    ranking_market: str = "KR"
    ranking_duration: str = "1d"

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 30
    collection_interval_seconds: int = 20
    ranking_limit: int = 50
    request_spacing_seconds: float = 0.25
    http_timeout_seconds: float = 10.0
    token_refresh_margin_seconds: int = 60

    # 쉼표로 여러 origin 지정. 운영에서는 * 대신 확장프로그램 ID를 명시하세요.
    cors_origins: str = "*"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
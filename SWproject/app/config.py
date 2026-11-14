from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 공식 REST API 호스트. 토큰 발급도 같은 호스트를 사용합니다.
    toss_api_base_url: str = "https://openapi.tossinvest.com"
    toss_client_id: str
    toss_client_secret: str
    # 계좌·자산·주문 API의 X-Tossinvest-Account 값(GET /api/v1/accounts의 accountSeq). 비우면 첫 위탁 계좌를 씁니다.
    toss_account_seq: str | None = None
    # 실제 매수·매도 주문을 보낼지. 토스 Open API에는 모의투자가 없어 주문은 실제로 체결됩니다(기본: 꺼짐).
    trading_enabled: bool = False
    # 이 화면에서 한 번에 보낼 수 있는 예상 주문 금액 상한.
    max_order_amount_krw: int = 1_000_000
    max_order_amount_usd: float = 1000.0

    # 여러 사용자 로그인(app/users.py). 사용자 토스 키를 암호화하는 서버 비밀값(32자 이상, 바꾸면 저장된 키를 못 풂).
    app_secret_key: str = ""
    signup_enabled: bool = True
    # 비어 있지 않으면 이 초대 코드를 아는 사람만 가입할 수 있습니다(공개 서버에서 권장).
    signup_invite_code: str = ""
    # 이 서버로 들어오는 요청의 Host 허용 목록(쉼표 구분). 공개 배포 시 도메인을 추가하세요(예: 127.0.0.1,localhost,market.example.com).
    allowed_hosts: str = "127.0.0.1,localhost"
    # HTTPS로 서비스할 때 true(세션 쿠키에 Secure). 공개 배포는 반드시 HTTPS 뒤에서 true로 쓰세요.
    session_cookie_secure: bool = False
    # 기사 수집·요약·AI 분석을 새로 만드는 요청을 사용자·IP별로 제한(app/rate_limit.py). 개발 중 측정할 때만 끄세요.
    rate_limit_enabled: bool = True

    # 랭킹 조회는 공식 단일 엔드포인트입니다.
    toss_ranking_path: str = "/api/v1/rankings"
    toss_stocks_path: str = "/api/v1/stocks"
    sector_map_path: str = "sector_map.json"
    weighted_analysis_enabled: bool = False
    ai_api_key: str | None = None
    ai_model: str = "gpt-4o-mini"
    ai_base_url: str = "https://api.openai.com/v1"
    # AI_MODEL이 과부하·한도 초과일 때 차례로 시도할 예비 모델(쉼표 구분).
    ai_fallback_models: str = ""
    # 두 번째 AI 제공자(기본: Groq). 첫 제공자의 모델이 모두 막히면 이쪽 모델을 차례로 씁니다.
    ai2_api_key: str | None = None
    ai2_base_url: str = "https://api.groq.com/openai/v1"
    ai2_models: str = "qwen/qwen3.8-27b,openai/gpt-oss-120b"
    # SEC EDGAR는 요청에 서비스 이름과 연락처를 담은 User-Agent를 요구합니다.
    sec_user_agent: str = "SWproject market-analysis (student project)"

    # 공식 Ranking API의 query parameter 이름입니다.
    ranking_type_param: str = "type"
    ranking_market_param: str = "marketCountry"
    ranking_duration_param: str = "duration"
    ranking_exclude_caution_param: str = "excludeInvestmentCaution"
    ranking_count_param: str = "count"
    ranking_market: str = "KR"
    ranking_duration: str = "1d"
    ranking_realtime_duration: str = "realtime"
    ranking_exclude_investment_caution: bool = False

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 30
    collection_interval_seconds: int = 20
    ranking_count: int = 50
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

    @property
    def ai_fallback_model_list(self) -> list[str]:
        return [model.strip() for model in self.ai_fallback_models.split(",") if model.strip()]

    @property
    def allowed_host_list(self) -> set[str]:
        return {host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()}

    @property
    def ai_providers(self) -> list[tuple[str, str, list[str]]]:
        """(주소, 키, 모델 목록). 키가 있는 제공자만, 시도할 순서대로."""
        providers = []
        if self.ai_api_key:
            providers.append((self.ai_base_url, self.ai_api_key, [self.ai_model, *self.ai_fallback_model_list]))
        second = [model.strip() for model in self.ai2_models.split(",") if model.strip()]
        if self.ai2_api_key and second:
            providers.append((self.ai2_base_url, self.ai2_api_key, second))
        return providers

    @property
    def safe_ranking_count(self) -> int:
        return min(max(self.ranking_count, 1), 100)


@lru_cache
def get_settings() -> Settings:
    return Settings()
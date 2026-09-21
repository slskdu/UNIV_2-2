# 토스증권 Open API 기반 랭킹 수집 FastAPI 백엔드

> 공식 문서 기준으로 인증과 랭킹 엔드포인트를 반영한 골격입니다. 토스증권 Open API의 기본 REST 주소는 `https://openapi.tossinvest.com`이며, 랭킹 조회는 `GET /api/v1/rankings`입니다. 랭킹별 실제 query parameter 이름과 허용 값은 계정에 노출되는 OpenAPI 스키마를 확인해 `.env`에서 조정할 수 있도록 구성했습니다.

## 프로젝트 구조

```text
toss-ranking-backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 앱, lifespan, CORS
│   ├── config.py            # 환경변수 기반 설정
│   ├── auth.py              # OAuth 2.0 client_credentials 토큰 발급/캐시
│   ├── redis_client.py      # Redis 연결 및 JSON 캐시
│   ├── toss_api.py          # 토큰 자동 갱신 및 랭킹 API 클라이언트
│   ├── collector.py         # 20초 주기 수집기
│   └── api/
│       ├── __init__.py
│       └── routes.py        # Chrome Extension용 API
├── .env.example
├── requirements.txt
└── docker-compose.yml
```

## `app/__init__.py`

```python
# Python 패키지 표시용 파일
```

## `app/config.py`

```python
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
    ranking_market_param: str = "market"
    ranking_duration_param: str = "duration"
    ranking_limit_param: str = "limit"
    ranking_market: str = "KR"
    ranking_duration: str = "realtime"

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
```

## `app/auth.py`

```python
import asyncio
import time

import httpx

from .config import Settings


class TossAuthError(RuntimeError):
    """토큰 발급 실패 예외"""


class TossTokenManager:
    """Client Credentials Grant 토큰을 메모리에 캐시하는 관리자."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            timeout=settings.http_timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._expires_at:
            return self._access_token

        async with self._lock:
            now = time.time()
            if self._access_token and now < self._expires_at:
                return self._access_token

            response = await self._client.post(
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.toss_client_id,
                    "client_secret": self.settings.toss_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.is_error:
                raise TossAuthError(
                    f"Toss token request failed ({response.status_code}): "
                    f"{response.text[:500]}"
                )

            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise TossAuthError("토큰 응답에 access_token이 없습니다.")

            expires_in = int(payload.get("expires_in", 3600))
            self._access_token = token
            self._expires_at = time.time() + max(
                1, expires_in - self.settings.token_refresh_margin_seconds
            )
            return token
```

## `app/redis_client.py`

```python
import json
from typing import Any

from redis.asyncio import Redis

from .config import get_settings

settings = get_settings()
redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)

CACHE_KEYS = {
    "rising": "market:ranking:rising",
    "falling": "market:ranking:falling",
    "trading_value": "market:ranking:trading_value",
}


async def set_json(key: str, value: Any, ttl: int) -> None:
    await redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)


async def get_json(key: str) -> Any | None:
    raw = await redis.get(key)
    return json.loads(raw) if raw else None


async def save_rankings(rankings: dict[str, Any]) -> None:
    # 개별 저장이므로 한 종류가 실패해도 기존의 다른 캐시를 덮어쓰지 않습니다.
    for name, value in rankings.items():
        if name in CACHE_KEYS:
            await set_json(CACHE_KEYS[name], value, settings.cache_ttl_seconds)


async def load_rankings() -> dict[str, Any | None]:
    names = tuple(CACHE_KEYS)
    values = await __import__("asyncio").gather(
        *(get_json(CACHE_KEYS[name]) for name in names)
    )
    return dict(zip(names, values))


async def close_redis() -> None:
    await redis.aclose()
```

## `app/toss_api.py`

```python
from typing import Any

import httpx

from .auth import TossTokenManager
from .config import Settings


class TossApiError(RuntimeError):
    pass


class TossApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_manager = TossTokenManager(settings)
        self.client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            timeout=settings.http_timeout_seconds,
        )

    async def close(self) -> None:
        await self.client.aclose()
        await self.token_manager.close()

    async def fetch_ranking(self, ranking_type: str) -> Any:
        token = await self.token_manager.get_access_token()
        params = {
            self.settings.ranking_type_param: ranking_type,
            self.settings.ranking_market_param: self.settings.ranking_market,
            self.settings.ranking_duration_param: self.settings.ranking_duration,
            self.settings.ranking_limit_param: self.settings.ranking_limit,
        }
        response = await self.client.get(
            self.settings.toss_ranking_path,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

        # 토큰 만료 시 한 번만 새 토큰을 받아 재요청합니다.
        if response.status_code == 401:
            self.token_manager._access_token = None
            token = await self.token_manager.get_access_token()
            response = await self.client.get(
                self.settings.toss_ranking_path,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "1")
            raise TossApiError(f"RANKING rate limit exceeded; Retry-After={retry_after}")
        if response.is_error:
            raise TossApiError(f"Toss API {response.status_code}: {response.text[:500]}")
        return response.json()
```

## `app/collector.py`

```python
import asyncio
import logging

from .config import Settings
from .redis_client import save_rankings
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

# 공식 문서의 RANKING 카테고리 한도는 초당 최대 5회입니다.
# 실제 type 값은 OpenAPI 스키마에 맞춰 환경변수로 바꿀 수 있습니다.
RANKING_TYPES = {
    "rising": "TOP_GAINERS",
    "falling": "TOP_LOSERS",
    "trading_value": "TRADING_VALUE",
}


async def collect_once(api: TossApiClient, settings: Settings) -> None:
    results = {}
    for index, (cache_name, ranking_type) in enumerate(RANKING_TYPES.items()):
        try:
            results[cache_name] = await api.fetch_ranking(ranking_type)
            logger.info("ranking collected: %s (%s)", cache_name, ranking_type)
        except Exception:
            logger.exception("ranking collection failed: %s", cache_name)

        # 순차 호출 + 간격으로 1초 내 5회보다 훨씬 낮게 유지합니다.
        if index < len(RANKING_TYPES) - 1:
            await asyncio.sleep(settings.request_spacing_seconds)

    if results:
        await save_rankings(results)


async def collector_loop(api: TossApiClient, settings: Settings) -> None:
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await collect_once(api, settings)
        except Exception:
            logger.exception("unexpected collector error")

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, settings.collection_interval_seconds - elapsed))
```

## `app/api/routes.py`

```python
from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..redis_client import load_rankings

router = APIRouter(prefix="/api/v1", tags=["market"])
settings = get_settings()


@router.get("/market-trend")
async def market_trend():
    """외부 API를 호출하지 않고 Redis의 최신 랭킹 데이터만 반환합니다."""
    data = await load_rankings()
    if not any(value is not None for value in data.values()):
        raise HTTPException(status_code=503, detail="아직 수집된 랭킹 데이터가 없습니다.")

    return {
        "data": data,
        "source": "redis",
        "cache_ttl_seconds": settings.cache_ttl_seconds,
    }
```

## `app/main.py`

```python
import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .collector import collector_loop
from .config import get_settings
from .redis_client import close_redis, redis
from .toss_api import TossApiClient

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.ping()
    toss_api = TossApiClient(settings)
    collector_task = asyncio.create_task(collector_loop(toss_api, settings))
    try:
        yield
    finally:
        collector_task.cancel()
        with suppress(asyncio.CancelledError):
            await collector_task
        await toss_api.close()
        await close_redis()


app = FastAPI(title="Toss Ranking Backend", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
async def health():
    await redis.ping()
    return {"status": "ok"}
```

## `.env.example`

```dotenv
# WTS > 설정 > Open API에서 발급받은 값
TOSS_CLIENT_ID=발급받은_client_id
TOSS_CLIENT_SECRET=발급받은_client_secret

TOSS_API_BASE_URL=https://openapi.tossinvest.com
TOSS_RANKING_PATH=/api/v1/rankings

# 공식 OpenAPI 문서의 ranking parameter schema에 맞춰 확인/수정하세요.
RANKING_TYPE_PARAM=type
RANKING_MARKET_PARAM=market
RANKING_DURATION_PARAM=duration
RANKING_LIMIT_PARAM=limit
RANKING_MARKET=KR
RANKING_DURATION=realtime

REDIS_URL=redis://localhost:6379/0
CACHE_TTL_SECONDS=30
COLLECTION_INTERVAL_SECONDS=20
RANKING_LIMIT=50
REQUEST_SPACING_SECONDS=0.25
HTTP_TIMEOUT_SECONDS=10
TOKEN_REFRESH_MARGIN_SECONDS=60
CORS_ORIGINS=chrome-extension://확장프로그램ID,http://localhost:3000
```

## `requirements.txt`

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
redis>=5.0.0
pydantic-settings>=2.5.0
```

## `docker-compose.yml`

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: toss-ranking-redis
    ports:
      - "6379:6379"
    restart: unless-stopped
```

## 실행

```bash
cp .env.example .env
# .env에 client_id, client_secret, 허용 IP, 실제 ranking query parameter를 입력합니다.
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
docker compose up -d redis
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
curl http://localhost:8000/api/v1/market-trend
```

## 공식 문서 반영 시 주의사항

- 토큰 발급은 `POST /oauth2/token`에 `grant_type=client_credentials`, `client_id`, `client_secret`을 form-urlencoded로 전송합니다.
- 시세·종목·랭킹 API는 `Authorization: Bearer {access_token}`만 필요합니다. `X-Tossinvest-Account`는 계좌·자산·주문 계열에서 사용합니다.
- 토스증권 Open API의 허용 IP 관리에 서버의 공인 IP를 등록해야 합니다. 등록되지 않은 IP는 403이 될 수 있습니다.
- RANKING 그룹 한도는 공식 문서 기준 초당 최대 5회입니다. 세 요청을 순차 호출하고 0.25초 간격을 둡니다.
- `TOP_GAINERS`와 `TOP_LOSERS`는 `duration=realtime`을 지원하지 않는다는 문서 오류 설명이 있으므로, 실제 스키마에 맞춰 해당 값은 반드시 확인하세요. 지원되지 않으면 `RANKING_DURATION`을 허용 값으로 변경합니다.
- `TOP_GAINERS`, `TOP_LOSERS`, `TRADING_VALUE`는 문서의 랭킹 type enum을 기준으로 작성한 예시입니다. 계정의 OpenAPI JSON에서 실제 enum/parameter 이름이 다르면 `.env`와 `RANKING_TYPES`를 수정하세요.
- 여러 Uvicorn worker를 사용하면 각 worker가 수집기를 하나씩 실행합니다. 수집기는 단일 worker로 운영하거나 별도 scheduler로 분리해야 중복 수집을 피할 수 있습니다.

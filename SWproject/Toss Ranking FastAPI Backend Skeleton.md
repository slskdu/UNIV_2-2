# 토스증권 랭킹 수집 FastAPI 백엔드

> 토스증권 Open API 문서에서 실제 랭킹 리소스 경로와 요청 파라미터를 확인한 뒤 `.env`의 경로를 맞춰 사용하세요. 공개 문서 페이지에서 확인 가능한 기본 API 주소는 `https://openapi.tossinvest.com/v1`이며, 랭킹 세부 경로는 계정/문서 권한에 따라 다를 수 있으므로 코드에서 환경변수로 분리했습니다.

## 프로젝트 구조

```text
toss-ranking-backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 앱, lifespan, CORS, 라우터
│   ├── config.py            # 환경변수 기반 설정
│   ├── redis_client.py      # Redis 연결 및 캐시 함수
│   ├── toss_api.py          # 토스증권 HTTP 클라이언트
│   ├── collector.py         # 20초 주기 백그라운드 수집기
│   └── api/
│       ├── __init__.py
│       └── routes.py        # 클라이언트 API
├── .env.example
├── requirements.txt
└── docker-compose.yml
```

## `app/__init__.py`

```python
# 패키지 표시용 파일
```

## `app/config.py`

```python
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


async def close_redis() -> None:
    await redis.aclose()


async def set_json(key: str, value: Any, ttl: int) -> None:
    await redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)


async def get_json(key: str) -> Any | None:
    raw = await redis.get(key)
    return json.loads(raw) if raw else None


async def save_rankings(rankings: dict[str, Any]) -> None:
    # 한 주기의 세 결과를 각각 저장하므로 일부 API 실패 시 기존 정상 캐시는 보존됩니다.
    for name, value in rankings.items():
        key = CACHE_KEYS[name]
        await set_json(key, value, settings.cache_ttl_seconds)


async def load_rankings() -> dict[str, Any]:
    values = await PromiseGather.gather(*(get_json(CACHE_KEYS[name]) for name in CACHE_KEYS))
    return dict(zip(CACHE_KEYS.keys(), values))


class PromiseGather:
    """asyncio.gather를 Redis 모듈 안에서 명시적으로 감싸는 작은 헬퍼."""

    @staticmethod
    async def gather(*coroutines):
        import asyncio
        return await asyncio.gather(*coroutines)
```

## `app/toss_api.py`

```python
from typing import Any

import httpx

from .config import Settings


class TossApiError(RuntimeError):
    pass


class TossApiClient:
    def __init__(self, settings: Settings):
        headers = {
            "Authorization": f"Bearer {settings.toss_access_token}",
            "Accept": "application/json",
        }
        if settings.toss_account_seq:
            headers["X-Tossinvest-Account"] = settings.toss_account_seq

        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            headers=headers,
            timeout=settings.http_timeout_seconds,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_ranking(self, path: str) -> Any:
        # limit 파라미터명은 토스증권 문서의 실제 명칭에 맞게 조정하세요.
        response = await self.client.get(path, params={"limit": self.settings.ranking_limit})
        if response.is_error:
            raise TossApiError(f"Toss API {response.status_code}: {response.text[:500]}")
        return response.json()
```

## `app/collector.py`

```python
import asyncio
import logging
from contextlib import suppress

from .config import Settings
from .redis_client import save_rankings
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)


async def collect_once(api: TossApiClient, settings: Settings) -> None:
    # 순차 호출 + 0.25초 간격: 초당 최대 5회 제한보다 충분히 여유가 있습니다.
    jobs = (
        ("rising", settings.toss_ranking_rising_path),
        ("falling", settings.toss_ranking_falling_path),
        ("trading_value", settings.toss_ranking_volume_path),
    )
    results = {}
    for index, (name, path) in enumerate(jobs):
        try:
            results[name] = await api.fetch_ranking(path)
            logger.info("ranking collected: %s", name)
        except Exception:
            logger.exception("ranking collection failed: %s", name)
        if index < len(jobs) - 1:
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

        # 작업 시간이 길어져도 주기 간격을 대략 20초로 유지합니다.
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, settings.collection_interval_seconds - elapsed))
```

## `app/api/__init__.py`

```python
# API 패키지
```

## `app/api/routes.py`

```python
from fastapi import APIRouter, HTTPException

from ..redis_client import load_rankings

router = APIRouter(prefix="/api/v1", tags=["market"])


@router.get("/market-trend")
async def market_trend():
    """외부 API를 호출하지 않고 Redis의 최신 랭킹만 반환합니다."""
    data = await load_rankings()
    if not any(value is not None for value in data.values()):
        raise HTTPException(status_code=503, detail="아직 수집된 랭킹 데이터가 없습니다.")

    return {
        "data": data,
        "cache_ttl_seconds": 30,
        "source": "redis",
    }
```

## `app/main.py`

```python
import asyncio
import logging
from contextlib import asynccontextmanager

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
    # 시작 시 Redis 연결을 확인하고 수집 태스크를 실행합니다.
    await redis.ping()
    toss_api = TossApiClient(settings)
    task = asyncio.create_task(collector_loop(toss_api, settings))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await toss_api.close()
        await close_redis()


app = FastAPI(title="Toss Ranking Backend", version="1.0.0", lifespan=lifespan)
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
TOSS_ACCESS_TOKEN=발급받은_액세스_토큰
TOSS_ACCOUNT_SEQ=계좌시퀀스_필요_시_입력
TOSS_API_BASE_URL=https://openapi.tossinvest.com/v1

# 토스증권 공식 문서의 실제 Ranking 경로로 변경
TOSS_RANKING_RISING_PATH=/rankings/rising
TOSS_RANKING_FALLING_PATH=/rankings/falling
TOSS_RANKING_VOLUME_PATH=/rankings/trading-value

REDIS_URL=redis://localhost:6379/0
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
# .env에 토큰과 실제 Ranking API 경로 입력
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
docker compose up -d redis
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

호출 예시:

```bash
curl http://localhost:8000/api/v1/market-trend
```

주의: 토스증권 Open API의 인증 헤더 및 Ranking API의 정확한 path/query/응답 필드는 발급받은 공식 API 문서와 권한에 따라 확인해야 합니다. 이 골격은 그 부분을 환경변수와 `fetch_ranking()` 한 곳에 격리해 두었습니다. 운영 환경에서는 `CORS_ORIGINS=*` 대신 실제 확장프로그램 ID만 허용하고, 로그에 토큰이나 민감한 응답을 남기지 마세요.

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

---

# 통합 프로젝트 기획서 및 AI 개발 실행 프롬프트 — 추가 섹션

> 위 FastAPI 백엔드 골격은 **기존 코드와 설명을 유지하는 기준 구현**이다. 아래는 웹·앱·크롬 확장프로그램까지 완성하기 위한 확장 계획이다. 기존 예시의 토스 인증 경로·랭킹 경로·파라미터·enum·호출 제한은 실제 운영 계정의 최신 공식 문서 및 사용 권한과 반드시 대조한다. 계획에 적힌 미확인 데이터 소스를 토스 API 제공 기능으로 단정하지 않는다.

## 1. 제품 목표와 범위

**목표:** 주식 랭킹과 사용자 관심종목을 바탕으로 오늘/전일의 핵심 종목·테마를 근거와 함께 파악하고, 관련 뉴스·호가·대차/공매도 지표를 확인하며, 위험을 표시한 후보 종목과 모의투자 성과까지 하나의 계정에서 웹·모바일 앱·크롬 확장프로그램으로 제공한다.

- 시작 범위: 한국 주식, 관심종목 수동 등록, 실계좌 연동 없이 모의 포트폴리오로 시작. 해외시장·실계좌 연동은 별도 단계.
- 단말 공통: 서버가 인증·데이터 가공·분석·추천 후보 산출을 담당하고 클라이언트는 같은 버전의 API를 사용한다. 외부 금융·뉴스·AI 서비스 키를 프런트엔드에 노출하지 않는다.
- 용어 구분: '전일'은 직전 **거래일**의 확정 장마감 데이터, '오늘'은 장중 현재까지의 잠정 데이터다. 휴장일, 장전/장후 및 데이터 지연을 표시한다.
- 추천은 수익 보장을 뜻하지 않는다. 근거·시각·데이터 부족·반대 신호·손실 위험을 함께 표기하며 투자자문/규제 검토 전에는 '투자 판단 보조용 후보'로만 제공한다.

## 2. 데이터 소스 및 구현 가능성 검증 게이트

| 데이터 | 필요한 내용 | 우선 소스/확인할 사항 | 불가 시 대안 |
|---|---|---|---|
| 랭킹/시세 | 상승·하락·거래대금 상위 50, 가격·거래량·기준시각 | 기존 토스 랭킹 구현과 계정별 최신 스키마·실시간성·재배포 조건 확인 | 정식 라이선스가 있는 공급자 채택, 미지원 항목은 표시하지 않음 |
| 과거 데이터/테마 | 직전 거래일 종가, 기간별 거래대금, 업종/테마 매핑 | 토스 제공 범위와 테마 분류 사용권 확인 | 별도 허가된 시세·테마 공급자 또는 관리자가 검증한 매핑 |
| 뉴스/공시 | 종목별 기사, 제목, URL, 발행시각, 원문 사용권 | 언론사/뉴스 API·공시 공식 소스의 라이선스와 검색/보관 조건 검토 | 링크·제목·허용된 메타데이터만 노출, AI 요약은 허용된 본문에 한정 |
| 호가/잔량 | 매수·매도 가격대/잔량, 갱신시각 | 호가 API/스트리밍 제공 여부·권한·호출 제한 확인 | 비지원 시 호가 위젯 비활성화, 과거 호가를 실시간처럼 표기 금지 |
| 공매도/대차 | 공매도 거래량·잔고, 대차잔고, 공표주기 | 공식 공개통계 또는 허가된 공급자의 필드·시장 범위·공표 시각 확인 | 공개통계만 노출하고 '실시간 숏포지션'으로 오인될 표현 금지 |
| 모의투자 체결 | 체결 기준 가격, 거래 비용, 호가/유동성 | 시세 라이선스와 장 운영시간·지연 시각 확인 | 실시간 체결 불가 시 다음 이용 가능한 가격으로 체결하고 지연 명시 |

**선행 결정:** 토스의 실제 지원 스키마, 서비스 약관, 시세/뉴스의 보관·가공·외부 서비스 재배포 가능 여부, AI에 제3자 데이터 전송 허용 여부를 확인한 뒤 연동을 확정한다. 토스가 제공하지 않는 기능은 `MarketDataProvider`, `NewsProvider`, `ShortInterestProvider` 등의 별도 공급자 어댑터로 구현한다. 사용 불가능한 데이터를 추론값으로 꾸며 제공하지 않는다.

## 3. 아키텍처 및 저장소 확장

```text
web (Next.js/React) ──────┐
mobile (React Native/Expo) ├── HTTPS ── FastAPI BFF/API ── PostgreSQL (사용자·뉴스·스냅샷·거래)
chrome extension (MV3) ────┘                 │         └── Redis (TTL 캐시·작업 잠금·레이트 제한)
                                           ├── collector worker → 승인된 시세/뉴스/공시 공급자
                                           ├── analysis worker → 결정적 지표 계산 → 근거 검색 → AI 요약
                                           └── simulation worker → 가상 주문/체결/성과 계산
```

기존 `app/main.py`, `config.py`, `auth.py`, `redis_client.py`, `toss_api.py`, `collector.py`, `api/routes.py`는 유지하고 다음 디렉터리를 단계적으로 추가한다.

```text
app/
├── providers/         # 토스, 뉴스/공시, 호가, 공매도 데이터 어댑터
├── models/            # SQLAlchemy ORM 및 마이그레이션(Alembic)
├── schemas/           # 버전별 요청/응답 Pydantic 모델
├── services/          # 정규화, 종목 매핑, 테마 분석, 뉴스 중복 제거
├── ai/                # 근거 패키지, 요약, 검증, 모델 공급자 추상화
├── simulation/        # 주문, 체결, 자산평가, 비용·손익
├── jobs/              # 수집/분석 비동기 작업; API worker와 분리
├── api/               # 인증, 관심종목, 뉴스, 리포트, 호가, 후보, 모의투자
└── tests/             # 공급자 목, 통합/E2E/부하 테스트
clients/
├── web/
├── mobile/
└── extension/
infra/                  # 컨테이너, 배포, 관측, 시크릿 설정
docs/                   # API 명세, 공급자 권한 매트릭스, 의사결정 기록
```

Redis는 30초 TTL의 최신 랭킹 응답 등 단기 캐시에 집중하고, **전일 비교·백테스트·뉴스 이력·모의 계좌는 PostgreSQL에 영속화**한다. 원본 응답(허용된 범위), 정규화 스냅샷, 공급자 ID, 수집시각/거래소 기준시각, 데이터 지연 여부를 분리한다. 여러 작업자 환경에서는 분산 락 또는 단일 스케줄러로 수집 중복을 방지하고 공급자별 공유 레이트 리미터를 둔다. 기존 예시의 401 재시도도 재요청까지 호출 예산에 포함한다.

## 4. 제품 기능 상세 및 수용 기준

### 4.1 오늘/전일 핵심 종목·테마 브리핑

- 거래대금, 등락, 거래량 변화, 지속성, 연관 종목 수, 뉴스/공시 연관도 등 **정의된 규칙 점수**로 후보 테마와 주도 종목을 선별한다. 단순 상승률만으로 '대표 테마'를 단정하지 않는다.
- 장중 잠정 브리핑과 전일 확정 브리핑을 별도로 생성한다. 구성 종목·점수·근거 지표·반대 근거·수집 시각을 저장하고 리포트 버전을 고정한다.
- AI는 저장된 구조화 지표와 사용 허가된 기사/공시만 요약하고, 날짜·숫자·티커/종목명을 검증한다. 근거 부족 시 '판단 유보'를 표시하며 인용 원문 URL로 이동 가능하게 한다.
- **완료 기준:** 휴장일/장전/장중/장후에 기준 거래일이 정확하고, 리포트의 모든 주요 사실에 출처·시각이 있으며, AI 실패 시 지표 기반 요약으로 대체한다.

### 4.2 관심종목/보유종목 뉴스 모니터링

- 로그인 사용자별 관심종목과 **모의 보유종목**을 구분한다. 실계좌 보유종목 연동은 명시적 동의, 공급자 권한, 계좌 API 보안 검토를 완료한 뒤 선택 기능으로 제공한다.
- 종목코드·시장·회사명·이전 상호명을 사용한 엔터티 매핑으로 동명이인 오탐을 줄이고, 기사 URL/발행시각/제목/본문 해시로 중복 제거한다.
- 카드에 매체, 제목, 발행시각, 수집시각, 해당 종목, 관련성, 원문 링크를 표시한다. AI 요약은 허용된 콘텐츠에서만 만들고 원문과 구별한다. 공시는 뉴스와 별도 표시한다.
- 새로운 주요 기사/공시는 구독 설정에 따라 웹 알림·앱 푸시·확장 배지로 보낸다. 사용자별 알림 빈도 제한, 해지, 권한 철회를 지원한다.
- **완료 기준:** 무관한 동명 기업 기사를 제외하고, 기사 중복이 제거되며, 삭제·철회된 관심종목은 이후 개인화 수집/알림 대상에서 빠진다.

### 4.3 판단 근거가 있는 후보 종목 제안

- **선행 조건:** 시세/뉴스 수집 품질, 점수 모델, 검증된 백테스트, 리포트 근거 검사를 통과한 뒤에만 활성화. 초기에는 설명 가능한 규칙 기반 후보 랭킹으로 시작한다.
- 후보 산정 예시: 유동성·가격 변동성·테마 확산·뉴스/공시 신뢰도·시점 적합성·집중 위험을 반영. 실적 보장이나 수익률 예언 금지; 결측/지연 데이터 또는 시장 급변 시 후보 생성 중지.
- 카드에 선정 이유, 사용된 데이터의 기준시각, 반대 시나리오, 변동성/손실 위험, 모델/규칙 버전을 함께 표기. 투자자문/유사투자자문, 개인정보, 광고 문구 등 적용 법적 요건 검토 후 표현·제공 범위를 결정한다.
- **완료 기준:** 미래 정보 누수 없는 워크포워드 검증, 거래비용 포함 비교, 설명 가능성, 데이터 결측 시 비활성화, 모델 드리프트/성과 모니터링 완료.

### 4.4 모의투자 시뮬레이션

- 초기 가상 현금, 관심종목/후보의 가상 주문, 미체결·부분체결·취소·잔고·평가손익·체결 이력을 제공. 주문은 실제 금융기관에 전달하지 않는다.
- 시뮬레이터는 거래소 장 운영시간, 호가 단위, 주문 가능 금액/수량, 수수료·세금, 매수·매도 슬리피지, 정지/상폐, 배당·분할/병합을 명시적으로 처리한다. 이용 가능한 데이터 해상도에 따라 체결 가정과 한계를 표시한다.
- 주문 요청마다 중복 방지 키(idempotency key)와 DB 트랜잭션을 사용하고, 서버 시각이 아닌 기준 시세의 타임스탬프를 기록한다. 수익률은 총수익률과 벤치마크를 함께 표시한다.
- **완료 기준:** 중복 주문에 의한 잔액 이중 차감이 없고, 거래비용 반영 손익/현금/보유 수량이 일치하며, 네트워크 장애 후 주문 내역 복원이 가능하다.

### 4.5 호가 및 공매도 관련 화면

- 호가: 실제 제공 권한이 있는 경우 매수/매도 가격대별 잔량, 호가 스프레드, 합산 잔량, 기준 시각/지연·장 상태를 동시에 보여준다. 호가 잔량은 **실제 체결이나 방향성 확정 신호가 아니다**.
- '숏포지션 물량'은 시장 전체의 공개 공매도 거래량·잔고/대차잔고와 특정 투자자의 실시간 포지션을 엄격히 구분한다. 후자는 일반적인 공개 데이터만으로 알 수 없으므로 확인되지 않으면 구현/표시하지 않는다.
- 공개 공매도 지표가 있으면 지표명·단위·발표 주기·대상 시장·기준일을 명확히 표기하고 호가와 다른 갱신주기로 제공한다.
- **완료 기준:** 호가·공매도 데이터 미제공/지연 시 화면에 사유와 마지막 유효 시각을 표시하고 값이 없는 경우 0으로 대체하지 않는다.

## 5. 공통 API 초안

| 경로 | 인증 | 목적 |
|---|---|---|
| `GET /api/v1/market-trend` | 공개 또는 정책별 제한 | 기존 Redis 랭킹 응답 유지 |
| `GET /api/v1/briefings?date=&mode=` | 선택 | 오늘/직전 거래일 테마·주도주·출처 |
| `GET/POST/DELETE /api/v1/watchlist` | 필수 | 관심종목 조회/등록/해제 |
| `GET /api/v1/stocks/{symbol}/news` | 정책별 | 종목 기사·공시 목록과 출처 |
| `GET /api/v1/stocks/{symbol}/orderbook` | 공급자 권한별 | 제공 가능한 경우 호가 스냅샷/갱신시각 |
| `GET /api/v1/stocks/{symbol}/short-interest` | 정책별 | 공표 가능한 공매도/대차 지표 |
| `GET /api/v1/candidates` | 필수 | 근거와 위험을 포함한 후보 목록 |
| `GET/POST /api/v1/simulation/orders` | 필수 | 가상 주문 조회/등록 |
| `GET /api/v1/simulation/portfolio` | 필수 | 가상 계좌·성과 조회 |

공통 응답은 `data`, `as_of`, `collected_at`, `source`, `is_delayed`, `status`(예: `ok`, `stale`, `unavailable`), `evidence`를 적용 가능한 범위에서 사용한다. 503/권한 없음/공급자 미지원/장마감 상태를 구분한다. 전일 데이터는 TTL 캐시가 아닌 날짜별 영속 저장을 조회한다. 인증은 별도의 사용자 세션/JWT 설계로 처리하고 토스 서버 토큰과 절대 혼용하지 않는다.

## 6. 웹·앱·크롬 확장프로그램 구현 계획

- **웹:** 반응형 대시보드, 테마 브리핑, 종목 상세(뉴스·호가·공매도 지표), 관심종목, 후보 카드, 모의 계좌/거래내역. 로그인과 접근성·시간대 표시를 공통 컴포넌트에 포함한다.
- **앱:** 웹과 같은 API/계정을 사용해 관심종목·알림·가상 주문 중심으로 구성. OS 푸시 권한, 백그라운드 정책, 앱스토어의 금융 정보 표시 정책을 검토한다.
- **확장프로그램:** Manifest V3의 팝업/사이드패널(지원 시)에 간단한 랭킹·브리핑·관심종목 알림을 표시. 현재 페이지 종목 인식이 필요할 때만 해당 사이트 host permission과 content script를 최소 범위로 요청한다. 서비스 워커는 상시 실행을 전제하지 않으며 인증은 안전한 세션 흐름을 사용한다.
- 확장 프로그램의 `host_permissions`에 **자사 백엔드 도메인**을 선언하고 서버의 CORS 허용 origin에는 승인된 `chrome-extension://<id>`를 명시한다. 웹은 허용 도메인만 등록하고 자격증명 전송과 와일드카드 CORS 조합을 피한다. 모바일은 브라우저 CORS 대신 앱 인증·TLS 정책을 적용한다.
- **공유 계약:** OpenAPI에서 타입/클라이언트를 생성하고 버전 호환성 테스트를 수행한다. UI는 stale/unavailable 상태, 뉴스 출처와 시각, 가상투자 표기를 일관되게 노출한다.

## 7. 권장 단계별 실행 순서 및 통과 조건

1. **검증/설계:** 최신 공급자 문서·계정 권한·데이터 재배포/AI 처리 조건·법무 검토; 종목 식별자, 거래일 캘린더, 호가/공매도 가능성, 비용 예산 확정. 미확인 API 필드는 TODO로 남긴다.
2. **기반 안정화:** 기존 FastAPI/Redis 수집기 테스트, 랭킹 타입별 허용 duration 검증, 호출 제한/401/429/재시도, 장 상태, 단일 수집자 보장. OpenAPI 계약 문서와 CI 마련.
3. **영속 저장과 사용자:** PostgreSQL 마이그레이션, 시세 스냅샷/뉴스/관심종목/계정, 최소 권한 인증, 개인정보 삭제·보존 기간 구현.
4. **브리핑·뉴스 MVP:** 승인된 뉴스/공시 소스 연동, 테마 분류와 근거 보관, 오늘/전일 화면과 AI 요약의 환각 검사. 먼저 웹에서 검증.
5. **앱·확장:** 동일 계약 연동, 확장 권한 최소화, 푸시/배지 동작 확인, 웹/앱/확장별 인증·복구·접근성 E2E 테스트.
6. **호가·공매도 지표:** 공급자 권한과 공표 주기 확보 시에만 구현; 미확보 시 UI에서 미지원 안내로 마무리.
7. **후보·시뮬레이터:** 점수/검증 통과 후 후보 베타 공개, 체결 엔진과 손익 검증, 종목별 위험 고지와 서비스 규제 확인.
8. **운영 출시:** 로그/지표/알림, 공급자 장애·429·비용 상한, DB 백업/복구, 비밀키 회전, 개인정보/보안 점검, 점진 배포.

## 8. 테스트·운영·비용 원칙

- 테스트: 시간 고정 테스트(휴장/윤년/장 경계), 공급자 응답 계약 테스트, 기사 중복·종목 매핑, 숫자/날짜 AI 검증, 프롬프트 인젝션 방어, 추천 과최적화·미래 정보 누수 검출, 가상 주문 동시성·중복 방지, 웹/앱/확장 E2E.
- 관측: 수집 성공률/지연, 캐시 미스·stale 비율, 공급자별 401/403/429, 뉴스 매핑 정밀도, AI 생성 비용·거절률, 가상 주문 원장 불일치, UI 오류율을 모니터링한다.
- 안전: 외부 기사/공시 텍스트는 신뢰하지 않는 입력으로 취급하고 지시문 실행 금지; 출처 없는 AI 내용 게시 금지; 비밀키/계좌정보 로그 금지; 사용자 데이터 암호화·접근 통제·삭제 기능 구현.
- 비용: 랭킹 수집, 추가 시세/뉴스/호가 호출, AI 요약, 푸시 비용에 일별 예산과 서킷 브레이커를 설정하고 뉴스/리포트는 변경분만 분석한다.

## 9. 후속 개발을 위한 실행 프롬프트

> 당신은 금융 데이터 백엔드·웹·모바일·Chrome MV3 확장 개발을 담당하는 엔지니어다. **이 문서 위쪽의 기존 FastAPI 소스는 유지하고** 아래 단계의 기능을 점진적으로 구현하라. 먼저 실제 사용 가능한 공식 API 스키마, 인증 방식, 파라미터/enum, 호가·공매도 지원 및 이용약관을 확인하고, 검증 불가능한 엔드포인트·실시간 수치·기사 원문을 만들어내지 마라. 매 단계마다 ① 변경 파일 트리 ② 실행 가능한 코드와 환경변수 예시 ③ 데이터 출처/권한 가정 ④ 마이그레이션 ⑤ 단위·통합·E2E 테스트 ⑥ 실행 방법 ⑦ 남은 위험·미확인 사항을 출력하라. 우선순위는 기존 수집기 안정화 → 사용자/DB → 브리핑/뉴스 → 웹 → 앱/확장 → 허가된 호가/공매도 지표 → 검증된 후보/모의투자다. 추가 데이터가 이용 불가하면 대체 공급자를 별도로 제안하거나 기능을 비활성화하라. 수집 시각·출처·지연·위험 고지는 모든 관련 UI와 API에 포함하라.

import asyncio
import json
from typing import Any

from redis.asyncio import Redis

from .config import get_settings

settings = get_settings()
redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)

CACHE_NAMES = ("rising", "falling", "trading_value")


def cache_key(market: str, name: str) -> str:
    return f"market:ranking:{market}:{name}"


async def set_json(key: str, value: Any, ttl: int) -> None:
    await redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)


async def get_json(key: str) -> Any | None:
    raw = await redis.get(key)
    return json.loads(raw) if raw else None


async def save_rankings(market: str, rankings: dict[str, Any]) -> None:
    # 개별 저장이므로 한 종류가 실패해도 기존의 다른 캐시를 덮어쓰지 않습니다.
    for name, value in rankings.items():
        if name in CACHE_NAMES:
            await set_json(cache_key(market, name), value, settings.cache_ttl_seconds)


async def load_rankings(market: str) -> dict[str, Any | None]:
    values = await asyncio.gather(*(get_json(cache_key(market, name)) for name in CACHE_NAMES))
    return dict(zip(CACHE_NAMES, values))


async def close_redis() -> None:
    await redis.aclose()
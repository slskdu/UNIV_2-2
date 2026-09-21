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
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
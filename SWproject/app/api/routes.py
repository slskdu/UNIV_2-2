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
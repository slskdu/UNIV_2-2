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
    "trading_value": "MARKET_TRADING_AMOUNT",
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
import asyncio
import logging

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
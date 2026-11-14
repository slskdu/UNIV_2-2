"""종목 이름 검색. 토스 Open API에는 이름 검색이 없어, 시장별 전체 종목 목록(GET /api/v1/stocks/all)을
하루 한 번 받아 두고 서버에서 찾습니다(문서 권장: 일 1회 배치 갱신이라 하루 한 번 캐시).
"""
import asyncio
import logging
import re
import time
from typing import Any

from .redis_client import get_json, set_json
from .toss_api import TossApiClient, TossApiError

logger = logging.getLogger(__name__)

MASTER_KEY = "stock-master:v1"
MASTER_TTL = 24 * 60 * 60
MARKETS = ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX")
STOCK_ALL_SPACING = 1.1  # STOCK_ALL 그룹 한도: 초당 1회
SEARCH_LIMIT = 10
TYPE_ORDER = {"STOCK": 0, "FOREIGN_STOCK": 0, "DEPOSITARY_RECEIPT": 1, "ETF": 2, "FOREIGN_ETF": 2, "REIT": 3, "ETN": 4}

_master: list[dict[str, Any]] | None = None
_loaded_at = 0.0
_lock = asyncio.Lock()


def _normalize(text: str) -> str:
    return re.sub(r"[\s·.\-()]+", "", text.lower())


async def load_master(api: TossApiClient) -> list[dict[str, Any]]:
    """[{symbol, name, market, type, common, key}] 전체 종목. 메모리 → Redis → 토스 순서로 찾습니다."""
    global _master, _loaded_at
    if _master is not None and time.monotonic() - _loaded_at < MASTER_TTL:
        return _master
    async with _lock:
        if _master is not None and time.monotonic() - _loaded_at < MASTER_TTL:
            return _master
        rows = await get_json(MASTER_KEY)
        if not rows:
            rows = []
            for index, market in enumerate(MARKETS):
                if index:
                    await asyncio.sleep(STOCK_ALL_SPACING)
                try:
                    items = await api._get_result("/api/v1/stocks/all", {"market": market}) or []
                except TossApiError:
                    logger.exception("stock master failed: %s", market)
                    continue
                rows.extend(
                    {"symbol": item["symbol"], "name": item.get("name") or item["symbol"], "market": market,
                     "type": item.get("securityType"), "common": bool(item.get("isCommonShare", True))}
                    for item in items if item.get("symbol")
                )
            if rows:
                await set_json(MASTER_KEY, rows, MASTER_TTL)
        _master = [{**row, "key": _normalize(row["name"]), "symbol_key": row["symbol"].lower()} for row in rows]
        _loaded_at = time.monotonic() if rows else 0.0  # 받지 못했으면 다음 검색 때 다시 시도
        logger.info("stock master loaded: %d", len(_master))
        return _master


def search_master(master: list[dict[str, Any]], query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    """이름(한글) 또는 종목코드로 찾습니다. 코드 일치 > 코드 앞부분 > 이름 앞부분 > 이름 포함 순, 같으면 보통주·주식 우선."""
    needle = _normalize(query)
    if not needle:
        return []
    scored = []
    for row in master:
        if row["symbol_key"] == needle:
            rank = 0
        elif row["key"] == needle:
            rank = 1
        elif row["symbol_key"].startswith(needle):
            rank = 2
        elif row["key"].startswith(needle):
            rank = 3
        elif needle in row["key"]:
            rank = 4
        else:
            continue
        scored.append((rank, not row["common"], TYPE_ORDER.get(row["type"] or "", 5), len(row["name"]), row["symbol"], row))
    scored.sort(key=lambda item: item[:5])
    return [
        {key: row[key] for key in ("symbol", "name", "market", "type")}
        for *_, row in scored[:limit]
    ]


async def search_stocks(api: TossApiClient, query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    return search_master(await load_master(api), query, limit)

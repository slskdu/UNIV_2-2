import asyncio
import logging

from .config import Settings
from .redis_client import save_rankings
from .sector_map import load_sector_map, sector_for
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

RANKING_TYPES = {
    "rising": "TOP_GAINERS",
    "falling": "TOP_LOSERS",
    "trading_value": "MARKET_TRADING_AMOUNT",
}
MARKETS = (("kr", "KR"), ("us", "US"))


async def collect_once(api: TossApiClient, settings: Settings) -> None:
    for market, market_country in MARKETS:
        results = {}
        for index, (cache_name, ranking_type) in enumerate(RANKING_TYPES.items()):
            try:
                duration = (
                    settings.ranking_realtime_duration
                    if ranking_type == "MARKET_TRADING_AMOUNT"
                    else settings.ranking_duration
                )
                results[cache_name] = await api.fetch_ranking(
                    ranking_type,
                    market_country,
                    duration,
                )
                logger.info("ranking collected: %s/%s (%s)", market, cache_name, ranking_type)
            except Exception:
                logger.exception("ranking collection failed: %s/%s", market, cache_name)

            if index < len(RANKING_TYPES) - 1:
                await asyncio.sleep(settings.request_spacing_seconds)

        if results:
            try:
                symbols = [
                    row.get("symbol")
                    for ranking in results.values()
                    for row in ranking.get("result", {}).get("rankings", [])
                    if isinstance(row, dict)
                ]
                stock_details = await api.fetch_stock_details(symbols)
                sector_map = load_sector_map()
                missing_sector_symbols = sorted({
                    symbol
                    for symbol, stock in stock_details.items()
                    if market_country in {"KR", "US"}
                    and symbol not in sector_map
                    and stock.get("securityType") not in {"ETF", "ETN"}
                })
                public_sectors = await asyncio.gather(
                    *(
                        api.fetch_public_sector(
                            symbol,
                            market_country,
                            stock_details[symbol].get("market"),
                        )
                        for symbol in missing_sector_symbols
                    )
                )
                external_sectors = dict(zip(missing_sector_symbols, public_sectors))
                for ranking in results.values():
                    for row in ranking.get("result", {}).get("rankings", []):
                        if isinstance(row, dict):
                            symbol = row.get("symbol")
                            row["marketCountry"] = market_country
                            stock = stock_details.get(symbol, {})
                            stock_name = next(
                                (
                                    stock[field]
                                    for field in ("name", "stockName", "companyName", "itemName")
                                    if isinstance(stock.get(field), str) and stock[field].strip()
                                ),
                                None,
                            )
                            if stock_name:
                                row["name"] = stock_name
                            row["sector"] = stock.get("securityType")
                            row["market"] = stock.get("market")
                            if external_sectors.get(symbol):
                                row["externalSector"] = external_sectors[symbol]
                            row["industry"] = sector_for(row)
            except Exception:
                logger.exception("stock name enrichment failed: %s", market)
            await save_rankings(market, results)

        await asyncio.sleep(settings.request_spacing_seconds)


async def collector_loop(api: TossApiClient, settings: Settings) -> None:
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await collect_once(api, settings)
        except Exception:
            logger.exception("unexpected collector error")

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, settings.collection_interval_seconds - elapsed))
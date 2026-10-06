import asyncio
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
        self._public_sector_cache: dict[str, str | None] = {}
        self._sector_lookup_semaphore = asyncio.Semaphore(4)

    async def close(self) -> None:
        await self.client.aclose()
        await self.token_manager.close()

    async def fetch_ranking(
        self,
        ranking_type: str,
        market_country: str | None = None,
        duration: str | None = None,
    ) -> Any:
        token = await self.token_manager.get_access_token()
        params = {
            self.settings.ranking_type_param: ranking_type,
            self.settings.ranking_market_param: market_country or self.settings.ranking_market,
            self.settings.ranking_duration_param: duration or self.settings.ranking_duration,
            self.settings.ranking_exclude_caution_param: self.settings.ranking_exclude_investment_caution,
            self.settings.ranking_count_param: self.settings.safe_ranking_count,
        }
        response = await self._get_ranking(token, params)

        # 토큰 만료 시 한 번만 새 토큰을 받아 재요청합니다.
        if response.status_code == 401:
            self.token_manager._access_token = None
            token = await self.token_manager.get_access_token()
            response = await self._get_ranking(token, params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "1"))
            await asyncio.sleep(max(0.0, retry_after))
            response = await self._get_ranking(token, params)
        if response.is_error:
            raise TossApiError(f"Toss API {response.status_code}: {response.text[:500]}")
        return response.json()

    async def _get_ranking(self, token: str, params: dict[str, Any]) -> httpx.Response:
        return await self.client.get(
            self.settings.toss_ranking_path,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def fetch_stock_names(self, symbols: list[str]) -> dict[str, str]:
        details = await self.fetch_stock_details(symbols)
        return {symbol: stock["name"] for symbol, stock in details.items() if stock.get("name")}

    async def fetch_public_sector(
        self,
        symbol: str,
        market_country: str = "US",
        exchange: str | None = None,
    ) -> str | None:
        suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}.get(exchange)
        queries = (
            [f"{symbol}{suffix}"]
            if market_country == "KR" and suffix
            else [f"{symbol}.KS", f"{symbol}.KQ"]
            if market_country == "KR"
            else [symbol]
        )
        cache_key = f"{market_country}:{exchange or ''}:{symbol.upper()}"
        if cache_key in self._public_sector_cache:
            return self._public_sector_cache[cache_key]

        async with self._sector_lookup_semaphore:
            if cache_key in self._public_sector_cache:
                return self._public_sector_cache[cache_key]
            sector = None
            for query in queries:
                try:
                    response = await self.client.get(
                        "https://query1.finance.yahoo.com/v1/finance/search",
                        params={"q": query, "quotesCount": 5, "newsCount": 0},
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    response.raise_for_status()
                    quotes = response.json().get("quotes", [])
                    quote = next(
                        (item for item in quotes if item.get("symbol", "").upper() == query.upper()),
                        {},
                    )
                    sector = quote.get("sectorDisp") or quote.get("sector")
                except (httpx.HTTPError, ValueError):
                    continue
                if sector:
                    break

            self._public_sector_cache[cache_key] = sector
            return sector

    async def fetch_stock_details(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        unique_symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
        if not unique_symbols:
            return {}

        token = await self.token_manager.get_access_token()
        params = {"symbols": ",".join(unique_symbols)}
        response = await self.client.get(
            self.settings.toss_stocks_path,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code == 401:
            self.token_manager._access_token = None
            token = await self.token_manager.get_access_token()
            response = await self.client.get(
                self.settings.toss_stocks_path,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        if response.is_error:
            raise TossApiError(f"Toss stock info {response.status_code}: {response.text[:500]}")

        payload = response.json()
        result = payload.get("result", [])
        stocks = result if isinstance(result, list) else result.get("stocks", [])
        return {
            stock.get("symbol"): stock
            for stock in stocks
            if stock.get("symbol")
        }
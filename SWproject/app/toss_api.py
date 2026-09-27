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
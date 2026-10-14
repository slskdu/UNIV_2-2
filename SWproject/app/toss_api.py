import asyncio
from typing import Any

import httpx

from .auth import TossTokenManager
from .config import Settings


class TossApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TossApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_manager = TossTokenManager(settings)
        self.client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            timeout=settings.http_timeout_seconds,
        )
        self._public_sector_cache: dict[str, str | None] = {}
        self._public_name_cache: dict[str, str | None] = {}
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

    async def _get_result(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """인증 GET 공통 처리. 401은 토큰 재발급, 429는 Retry-After 대기 후 1회씩 재요청합니다."""
        token = await self.token_manager.get_access_token()

        async def send(access_token: str) -> httpx.Response:
            return await self.client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )

        response = await send(token)
        if response.status_code == 401:
            self.token_manager._access_token = None
            token = await self.token_manager.get_access_token()
            response = await send(token)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "1"))
            await asyncio.sleep(max(0.0, retry_after))
            response = await send(token)
        if response.is_error:
            raise TossApiError(
                f"Toss API {path} {response.status_code}: {response.text[:500]}",
                response.status_code,
            )
        return response.json().get("result")

    async def fetch_candles(
        self,
        symbol: str,
        interval: str = "1d",
        count: int = 200,
        before: str | None = None,
        indicator: bool = False,
        adjusted: bool = True,
    ) -> dict[str, Any]:
        """캔들 한 페이지(최신순, 최대 200개). indicator=True면 지수(KOSPI/KOSDAQ) 캔들입니다.

        adjusted=True(기본)는 분할·병합 등을 반영한 수정주가, False는 당시 거래된 원주가입니다.
        """
        params: dict[str, Any] = {"interval": interval, "count": min(max(count, 1), 200)}
        if before:
            params["before"] = before
        if indicator:
            path = f"/api/v1/market-indicators/{symbol}/candles"
        else:
            path = "/api/v1/candles"
            params["symbol"] = symbol
            params["adjusted"] = "true" if adjusted else "false"
        return await self._get_result(path, params) or {}

    async def fetch_stock_warnings(self, symbol: str) -> list[dict[str, Any]]:
        """현재 활성인 매수 유의사항(정리매매, 투자경고·위험, 단기과열, VI 등)."""
        return await self._get_result(f"/api/v1/stocks/{symbol}/warnings") or []

    async def fetch_sectors(self) -> list[dict[str, Any]]:
        result = await self._get_result("/api/v1/sectors") or {}
        return result.get("sectors", [])

    async def fetch_sector_rankings(
        self,
        market_country: str,
        duration: str = "1d",
        ranking_type: str = "TOP_GAINERS",
    ) -> list[dict[str, Any]]:
        result = await self._get_result(
            "/api/v1/sectors/rankings",
            {"type": ranking_type, "marketCountry": market_country, "duration": duration},
        ) or {}
        return result.get("rankings", [])

    async def fetch_sector_detail(self, sector_id: str, market_country: str) -> dict[str, Any]:
        return await self._get_result(
            f"/api/v1/sectors/{sector_id}", {"marketCountry": market_country}
        ) or {}

    async def fetch_sector_stocks(self, sector_id: str, market_country: str) -> list[dict[str, Any]]:
        result = await self._get_result(
            f"/api/v1/sectors/{sector_id}/stocks", {"marketCountry": market_country}
        ) or {}
        return result.get("stocks", [])

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

    async def fetch_public_name(self, symbol: str, exchange: str | None = None) -> str | None:
        """국내 종목의 영문 정식 회사명(예: 'Samsung Electronics Co., Ltd.')을 Yahoo 검색에서 찾습니다.

        토스 종목 정보의 englishName은 'SamsungElec' 같은 약칭이라 해외 기사 검색에 쓸 수 없습니다.
        """
        cache_key = f"{exchange or ''}:{symbol}"
        if cache_key in self._public_name_cache:
            return self._public_name_cache[cache_key]
        suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}.get(exchange or "")
        name = None
        async with self._sector_lookup_semaphore:
            for query in ([f"{symbol}{suffix}"] if suffix else [f"{symbol}.KS", f"{symbol}.KQ"]):
                try:
                    response = await self.client.get(
                        "https://query1.finance.yahoo.com/v1/finance/search",
                        params={"q": query, "quotesCount": 5, "newsCount": 0},
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    response.raise_for_status()
                    quote = next(
                        (item for item in response.json().get("quotes", []) if item.get("symbol", "").upper() == query),
                        {},
                    )
                    name = quote.get("longname") or quote.get("shortname")
                except (httpx.HTTPError, ValueError):
                    continue
                if name:
                    break
        if name:  # 일시 오류로 못 찾은 경우는 기억하지 않고 다음에 다시 찾습니다.
            self._public_name_cache[cache_key] = name
        return name

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
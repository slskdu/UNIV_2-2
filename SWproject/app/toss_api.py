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

    async def fetch_ranking(self, ranking_type: str) -> Any:
        token = await self.token_manager.get_access_token()
        params = {
            self.settings.ranking_type_param: ranking_type,
            self.settings.ranking_market_param: self.settings.ranking_market,
            self.settings.ranking_duration_param: self.settings.ranking_duration,
            self.settings.ranking_limit_param: self.settings.ranking_limit,
        }
        response = await self.client.get(
            self.settings.toss_ranking_path,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

        # 토큰 만료 시 한 번만 새 토큰을 받아 재요청합니다.
        if response.status_code == 401:
            self.token_manager._access_token = None
            token = await self.token_manager.get_access_token()
            response = await self.client.get(
                self.settings.toss_ranking_path,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "1")
            raise TossApiError(f"RANKING rate limit exceeded; Retry-After={retry_after}")
        if response.is_error:
            raise TossApiError(f"Toss API {response.status_code}: {response.text[:500]}")
        return response.json()
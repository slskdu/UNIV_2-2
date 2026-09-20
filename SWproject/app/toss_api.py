from typing import Any

import httpx

from .config import Settings


class TossApiError(RuntimeError):
    pass


class TossApiClient:
    def __init__(self, settings: Settings):
        headers = {
            "Authorization": f"Bearer {settings.toss_access_token}",
            "Accept": "application/json",
        }
        if settings.toss_account_seq:
            headers["X-Tossinvest-Account"] = settings.toss_account_seq

        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            headers=headers,
            timeout=settings.http_timeout_seconds,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_ranking(self, path: str) -> Any:
        # limit 파라미터명은 토스증권 문서의 실제 명칭에 맞게 조정하세요.
        response = await self.client.get(path, params={"limit": self.settings.ranking_limit})
        if response.is_error:
            raise TossApiError(f"Toss API {response.status_code}: {response.text[:500]}")
        return response.json()
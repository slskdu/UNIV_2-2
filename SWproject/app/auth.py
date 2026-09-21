import asyncio
import time

import httpx

from .config import Settings


class TossAuthError(RuntimeError):
    """토큰 발급 실패 예외"""


class TossTokenManager:
    """Client Credentials Grant 토큰을 메모리에 캐시하는 관리자."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=settings.toss_api_base_url.rstrip("/"),
            timeout=settings.http_timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._expires_at:
            return self._access_token

        async with self._lock:
            now = time.time()
            if self._access_token and now < self._expires_at:
                return self._access_token

            response = await self._client.post(
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.toss_client_id,
                    "client_secret": self.settings.toss_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.is_error:
                raise TossAuthError(
                    f"Toss token request failed ({response.status_code}): "
                    f"{response.text[:500]}"
                )

            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise TossAuthError("토큰 응답에 access_token이 없습니다.")

            expires_in = int(payload.get("expires_in", 3600))
            self._access_token = token
            self._expires_at = time.time() + max(
                1, expires_in - self.settings.token_refresh_margin_seconds
            )
            return token
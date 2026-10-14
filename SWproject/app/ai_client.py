import logging
import time
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

# 모델 쪽 사정(과부하·한도 초과·일시 장애)으로 보는 응답. 이때만 다음 예비 모델로 넘어갑니다.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 방금 실패한 모델은 잠시 건너뛰어, 요청마다 같은 실패를 기다리지 않게 합니다.
COOLDOWN_SECONDS = 300.0
_unavailable_until: dict[str, float] = {}


def ai_enabled() -> bool:
    return bool(get_settings().ai_api_key)


async def chat_completion(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout: float = 20.0,
) -> str:
    """OpenAI 호환 Chat Completions를 호출해 답변 본문을 돌려줍니다.

    AI_MODEL이 과부하(503)·한도 초과(429)이거나 timeout초 안에 답하지 않으면
    AI_FALLBACK_MODELS에 적힌 모델을 차례로 시도합니다. 모두 실패하면 마지막 오류를 올립니다.
    """
    settings = get_settings()
    models = [settings.ai_model, *settings.ai_fallback_model_list]
    now = time.monotonic()
    # 전부 쉬는 중이면 건너뛸 수 없으니 원래 순서대로 다시 시도합니다.
    candidates = [model for model in models if _unavailable_until.get(model, 0.0) <= now] or models

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for model in candidates:
            payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                response = await client.post(
                    f"{settings.ai_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                    json=payload,
                )
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    return str(response.json()["choices"][0]["message"]["content"]).strip()
                last_error = httpx.HTTPStatusError(
                    f"AI model {model} returned {response.status_code}", request=response.request, response=response
                )
                reason = str(response.status_code)
            except httpx.TransportError as error:  # 시간 초과·연결 끊김
                last_error = error
                reason = type(error).__name__
            _unavailable_until[model] = time.monotonic() + COOLDOWN_SECONDS
            logger.warning("AI model %s unavailable (%s); trying next model", model, reason)
    raise last_error or RuntimeError("AI 모델이 설정되지 않았습니다.")

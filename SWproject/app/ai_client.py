import logging
import time
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

# 모델 쪽 사정(과부하·한도 초과·일시 장애)으로 보는 응답. 이때만 다음 예비 모델로 넘어갑니다.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MODEL_REJECTED_STATUS = {400, 413, 422}
# 방금 실패한 모델은 잠시 건너뛰어, 요청마다 같은 실패를 기다리지 않게 합니다.
COOLDOWN_SECONDS = 300.0
_unavailable_until: dict[str, float] = {}


class AIUnavailableError(RuntimeError):
    """모든 모델이 방금 실패해 쉬는 중이라 요청을 보내지 않았습니다."""


def ai_enabled() -> bool:
    return bool(get_settings().ai_providers)


async def chat_completion(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout: float = 20.0,
) -> str:
    """OpenAI 호환 Chat Completions를 호출해 답변 본문을 돌려줍니다.

    AI_MODEL이 과부하(503)·한도 초과(429)이거나 timeout초 안에 답하지 않으면
    AI_FALLBACK_MODELS에 적힌 모델을, 그것도 모두 막히면 두 번째 제공자(AI2_*)의 모델을 차례로 시도합니다.
    모두 실패하면 마지막 오류를 올립니다.
    모든 모델이 쉬는 중(COOLDOWN_SECONDS)이면 기다리지 않고 곧바로 AIUnavailableError를 올려,
    호출한 쪽이 규칙 기반 결과로 넘어가게 합니다.
    """
    now = time.monotonic()
    # 쉬는 중 표시는 제공자 주소와 모델을 함께 씁니다(제공자마다 같은 이름의 모델이 있을 수 있음).
    candidates = [
        (base_url, api_key, model, f"{base_url}|{model}")
        for base_url, api_key, models in get_settings().ai_providers
        for model in models
        if _unavailable_until.get(f"{base_url}|{model}", 0.0) <= now
    ]
    if not candidates:
        raise AIUnavailableError("모든 AI 모델이 한도 초과·장애로 잠시 쉬는 중입니다.")

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for base_url, api_key, model, slot in candidates:
            payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                if response.status_code in MODEL_REJECTED_STATUS:
                    # 요청이 그 모델에 너무 크거나(413) JSON 형식을 못 맞춘 경우(400) 등. 다른 모델은 받아 줄 수 있어
                    # 쉬게 하지 않고 다음 모델로 넘어갑니다(키 오류 401/403은 그대로 올림).
                    last_error = httpx.HTTPStatusError(
                        f"AI model {model} rejected the request ({response.status_code})", request=response.request, response=response
                    )
                    logger.warning("AI model %s rejected request (%s): %s", model, response.status_code, response.text[:300])
                    continue
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    return str(response.json()["choices"][0]["message"]["content"]).strip()
                last_error = httpx.HTTPStatusError(
                    f"AI model {model} returned {response.status_code}", request=response.request, response=response
                )
                reason = str(response.status_code)
                cooldown = _retry_after(response)
            except httpx.TransportError as error:  # 시간 초과·연결 끊김
                last_error = error
                reason = type(error).__name__
                cooldown = COOLDOWN_SECONDS
            _unavailable_until[slot] = time.monotonic() + cooldown
            logger.warning("AI model %s unavailable (%s); trying next model", model, reason)
    raise last_error or RuntimeError("AI 모델이 설정되지 않았습니다.")


def _retry_after(response: httpx.Response) -> float:
    """분당 한도(Groq 등)는 몇 초 뒤 풀리므로, 서버가 Retry-After로 알려 주면 그만큼만 쉽니다."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return COOLDOWN_SECONDS
    return min(max(seconds, 1.0), COOLDOWN_SECONDS)


def seconds_until_available() -> float | None:
    """설정된 모델 중 가장 먼저 다시 쓸 수 있게 되는 데 남은 초(이미 쓸 수 있으면 0, AI가 없으면 None)."""
    slots = [f"{base_url}|{model}" for base_url, _, models in get_settings().ai_providers for model in models]
    if not slots:
        return None
    now = time.monotonic()
    return max(0.0, min(_unavailable_until.get(slot, 0.0) - now for slot in slots))

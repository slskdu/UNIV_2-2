import json
from typing import Any

import httpx

from .config import get_settings


def fallback_summary(analysis: dict[str, Any]) -> str:
    hot = analysis.get("top_sectors", [])[:2]
    caution = analysis.get("caution_sectors", [])[:2]
    hot_text = ", ".join(item["sector"] for item in hot) or "뚜렷한 강세 분야 없음"
    caution_text = ", ".join(item["sector"] for item in caution) or "뚜렷한 주의 분야 없음"
    return f"현재 강세로 분류된 분야는 {hot_text}이며, 상대적으로 주의할 분야는 {caution_text}입니다. 이는 랭킹 데이터 기반 참고용 분석이며 투자 판단을 대신하지 않습니다."


async def create_summary(analysis: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.ai_api_key:
        return fallback_summary(analysis)

    prompt = (
        "다음 주식 랭킹 분석을 한국어로 3문장 이내 요약하세요. "
        "강세 분야와 주의 분야를 수치와 함께 설명하고 투자 조언이 아닌 데이터 해석임을 밝혀 주세요.\n"
        + json.dumps(analysis, ensure_ascii=False)
    )
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(
            f"{settings.ai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            json={
                "model": settings.ai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()
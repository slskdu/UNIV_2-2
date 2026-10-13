import asyncio
import hashlib
import logging
from typing import Any

import httpx

from .redis_client import get_json, set_json

logger = logging.getLogger(__name__)

# 키 없이 쓰는 Google 번역 웹 엔드포인트(비공식)라 언제든 막힐 수 있습니다. 그래서 두 경로를 차례로
# 시도하고, 호출 수를 줄이려고 번역 결과를 오래 캐시하며, 실패하면 원문 제목을 그대로 둡니다.
BATCH_URL = "https://clients5.google.com/translate_a/t"
SINGLE_URL = "https://translate.googleapis.com/translate_a/single"
TRANSLATION_TTL_SECONDS = 30 * 24 * 60 * 60
BATCH_SIZE = 15


def _cache_key(text: str) -> str:
    return f"insight:translation:ko:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


def parse_translation(payload: Any) -> str:
    """응답의 문장 조각들을 이어 붙여 번역문 전체를 만듭니다."""
    try:
        return "".join(segment[0] for segment in payload[0] if segment and segment[0])
    except (TypeError, IndexError):
        return ""


async def _translate_batch(client: httpx.AsyncClient, texts: list[str]) -> list[str | None]:
    """제목 묶음을 한 번에 번역합니다. 결과 개수가 원문과 맞지 않으면 그 경로는 버립니다."""
    try:
        response = await client.get(
            BATCH_URL,
            params={"client": "dict-chrome-ex", "sl": "auto", "tl": "ko", "q": texts},
        )
        response.raise_for_status()
        # 여러 나라 기사를 다루므로 원문 언어는 자동 감지합니다. 이때 항목은 [번역문, 언어] 형태로 옵니다.
        lines = [item[0] if isinstance(item, list) else item for item in response.json()]
        if len(lines) == len(texts) and all(isinstance(line, str) and line.strip() for line in lines):
            return [line.strip() for line in lines]
    except (httpx.HTTPError, ValueError, TypeError, IndexError):
        pass

    try:
        # GET은 차단되는 경우가 있어 POST로 보냅니다. 줄바꿈으로 묶어 보내고 줄 단위로 되돌립니다.
        response = await client.post(
            SINGLE_URL,
            data={"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": "\n".join(texts)},
        )
        response.raise_for_status()
        lines = [line.strip() for line in parse_translation(response.json()).split("\n") if line.strip()]
        if len(lines) == len(texts):
            return lines
    except (httpx.HTTPError, ValueError):
        pass

    logger.warning("title translation failed for %d titles", len(texts))
    return [None] * len(texts)


async def translate_to_korean(texts: list[str]) -> dict[str, str]:
    """외국어 문장들을 한국어로 번역해 {원문: 번역문}으로 반환합니다. 실패한 문장은 빠집니다."""
    unique = list(dict.fromkeys(text.replace("\n", " ").strip() for text in texts if text and text.strip()))
    cached = await asyncio.gather(*(get_json(_cache_key(text)) for text in unique))
    translations = {text: value for text, value in zip(unique, cached) if value}
    missing = [text for text in unique if text not in translations]
    if not missing:
        return translations

    async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        batches = [missing[index:index + BATCH_SIZE] for index in range(0, len(missing), BATCH_SIZE)]
        results = await asyncio.gather(*(_translate_batch(client, batch) for batch in batches))

    for batch, translated in zip(batches, results):
        for text, value in zip(batch, translated):
            if value:
                translations[text] = value
                await set_json(_cache_key(text), value, TRANSLATION_TTL_SECONDS)
    return translations


async def attach_korean_titles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한국어가 아닌 기사(lang != "ko")에 번역 제목 title_ko를 붙입니다. 번역 실패 시 원문만 남습니다."""
    def foreign(article: dict[str, Any]) -> bool:
        return article.get("lang", "ko") != "ko"

    titles = [article["title"] for article in articles if foreign(article)]
    if not titles:
        return articles
    translations = await translate_to_korean(titles)
    return [
        {**article, "title_ko": translations[key]}
        if foreign(article) and (key := article["title"].replace("\n", " ").strip()) in translations
        else article
        for article in articles
    ]

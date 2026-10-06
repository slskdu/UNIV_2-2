import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx


async def fetch_related_news(
    targets: list[dict[str, Any]],
    max_articles: int = 8,
) -> list[dict[str, Any]]:
    unique_targets = []
    seen_targets = set()
    for target in targets:
        key = (target.get("market"), target.get("symbol"))
        if target.get("symbol") and key not in seen_targets:
            unique_targets.append(target)
            seen_targets.add(key)
        if len(unique_targets) >= 5:
            break

    semaphore = asyncio.Semaphore(3)
    cutoff = time.time() - 30 * 24 * 60 * 60

    async with httpx.AsyncClient(
        timeout=8.0,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        async def search_target(target: dict[str, Any]) -> list[dict[str, Any]]:
            symbol = str(target["symbol"])
            name = str(target.get("name") or symbol)
            market = str(target.get("market") or "US")
            query = symbol if market == "US" else name
            params = {"q": query, "quotesCount": 5, "newsCount": 5}
            try:
                async with semaphore:
                    response = await client.get(
                        "https://query1.finance.yahoo.com/v1/finance/search",
                        params=params,
                    )
                response.raise_for_status()
                news = response.json().get("news", [])
            except (httpx.HTTPError, ValueError):
                return []

            normalized_symbol = symbol.upper().removesuffix(".KS").removesuffix(".KQ")
            items = []
            for article in news:
                title = str(article.get("title") or "").strip()
                link = str(article.get("link") or "").strip()
                related = {
                    str(value).upper().removesuffix(".KS").removesuffix(".KQ")
                    for value in article.get("relatedTickers", [])
                }
                title_matches_name = bool(name and name.casefold() in title.casefold())
                if not title or not link or not (
                    normalized_symbol in related or title_matches_name
                ):
                    continue

                published = article.get("providerPublishTime")
                if published and published < cutoff:
                    continue
                published_at = (
                    datetime.fromtimestamp(published, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if published
                    else ""
                )
                items.append({
                    "symbol": symbol,
                    "name": name,
                    "title": title,
                    "publisher": article.get("publisher") or "출처 미상",
                    "url": link,
                    "published_at": published_at,
                })
            return items

        results = await asyncio.gather(*(search_target(target) for target in unique_targets))

    articles = []
    seen_urls = set()
    for group in results:
        for article in group:
            if article["url"] not in seen_urls:
                articles.append(article)
                seen_urls.add(article["url"])
    articles.sort(key=lambda article: article["published_at"], reverse=True)
    return articles[:max_articles]

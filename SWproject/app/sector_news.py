import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
KST = timezone(timedelta(hours=9))
SIMILAR_TITLE_THRESHOLD = 0.4


def _bigrams(title: str) -> set[str]:
    text = re.sub(r"\W+", "", title)
    return {text[index:index + 2] for index in range(len(text) - 1)}


def merge_similar(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 사건을 다룬 비슷한 제목은 먼저 나온 기사 하나로 합치고 similar_count에 건수를 남깁니다."""
    kept: list[tuple[set[str], dict[str, Any]]] = []
    for article in articles:
        grams = _bigrams(article["title"])
        for kept_grams, kept_article in kept:
            union = grams | kept_grams
            if union and len(grams & kept_grams) / len(union) >= SIMILAR_TITLE_THRESHOLD:
                kept_article["similar_count"] += 1 + article.get("similar_count", 0)
                break
        else:
            kept.append((grams, {**article, "similar_count": article.get("similar_count", 0)}))
    return [article for _, article in kept]


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """Google 뉴스 RSS 본문을 기사 목록으로 바꿉니다. 제목 끝의 ' - 매체명'은 떼어 냅니다."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    articles = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        publisher = (item.findtext("source") or "").strip()
        if publisher:
            # "제목 - 매체 - 매체"처럼 매체명이 반복되는 경우까지 정리합니다.
            title = re.sub(rf"(\s+-\s+{re.escape(publisher)})+\s*$", "", title).strip()
        if not title or not link.startswith("https://"):
            continue

        published_ts, published_at = 0.0, ""
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            published_ts = published.timestamp()
            published_at = published.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass

        articles.append({
            "title": title,
            "publisher": publisher or "출처 미상",
            "url": link,
            "published_at": published_at,
            "published_ts": published_ts,
        })
    return articles


# Google 뉴스의 나라별 판. 같은 종목이라도 나라마다 다루는 매체와 관점이 다릅니다.
EDITIONS = {
    "kr": {"hl": "ko", "gl": "KR", "ceid": "KR:ko", "lang": "ko", "country": "한국"},
    "us": {"hl": "en-US", "gl": "US", "ceid": "US:en", "lang": "en", "country": "미국"},
    "gb": {"hl": "en-GB", "gl": "GB", "ceid": "GB:en", "lang": "en", "country": "영국"},
    "in": {"hl": "en-IN", "gl": "IN", "ceid": "IN:en", "lang": "en", "country": "인도"},
    "jp": {"hl": "ja", "gl": "JP", "ceid": "JP:ja", "lang": "ja", "country": "일본"},
    "de": {"hl": "de", "gl": "DE", "ceid": "DE:de", "lang": "de", "country": "독일"},
    "fr": {"hl": "fr", "gl": "FR", "ceid": "FR:fr", "lang": "fr", "country": "프랑스"},
}
WORLD_EDITIONS = ("gb", "in", "jp", "de", "fr")


async def search_news(
    client: httpx.AsyncClient,
    query: str,
    days: int,
    edition: str = "kr",
) -> list[dict[str, Any]]:
    """검색어 하나당 Google 뉴스 RSS 한 번(최대 100건)을 조회합니다."""
    settings = EDITIONS.get(edition, EDITIONS["kr"])
    try:
        response = await client.get(
            GOOGLE_NEWS_RSS,
            params={"q": f"{query} when:{days}d", **{key: settings[key] for key in ("hl", "gl", "ceid")}},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    return parse_rss(response.text)


US_EXCHANGE_PREFIXES = {
    "NYSE", "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "AMEX", "NYSEARCA", "NYSEAMERICAN",
    "NYSEMKT", "OTC", "OTCMKTS", "BATS", "CBOE",
}
COMPANY_SUFFIX = re.compile(
    r"[,\s]+(?:co\.?,?\s*ltd\.?|corporation|corp\.?|inc\.?|ltd\.?|plc|company|holdings co\.?,?\s*ltd\.?|"
    r"class [a-c]|\(adr\)|adr)\s*$",
    re.IGNORECASE,
)


def clean_company_name(name: str | None) -> str:
    """'Samsung Electronics Co., Ltd.' → 'Samsung Electronics'처럼 기사 제목에 쓰이는 형태로 줄입니다."""
    cleaned = (name or "").strip()
    while True:
        shorter = COMPANY_SUFFIX.sub("", cleaned).strip(" ,")
        if shorter == cleaned or not shorter:
            return cleaned
        cleaned = shorter


def _normalize(text: str) -> str:
    return re.sub(r"[-‐‑–\s]+", " ", text)


def _mentions(title: str, alias: str) -> bool:
    # 영문명은 다른 단어의 일부로 걸리지 않게 단어 경계로 확인하고, 하이픈·공백 차이는 무시합니다.
    if alias.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(_normalize(alias))}(?![A-Za-z0-9])"
        return re.search(pattern, _normalize(title), re.IGNORECASE) is not None
    return alias in title


def _mentions_ticker(title: str, ticker: str) -> bool:
    """제목의 티커가 이 종목을 가리키는지 확인합니다.

    'LSE:ADM'처럼 미국 밖 거래소가 붙은 티커는 같은 기호를 쓰는 다른 회사이므로 제외합니다.
    """
    pattern = rf"(?:(?<![A-Za-z])([A-Za-z]{{2,12}})\s*:\s*)?(?<![A-Za-z0-9.]){re.escape(ticker)}(?![A-Za-z0-9])"
    found = False
    for match in re.finditer(pattern, title):
        exchange = match.group(1)
        if exchange and exchange.upper() not in US_EXCHANGE_PREFIXES:
            return False
        found = True
    return found


def is_relevant(title: str, target: dict[str, Any]) -> bool:
    """기사 제목이 검색 대상을 실제로 다루는지 판단합니다.

    회사 이름이 있으면 인정합니다. 티커만 있는 제목은 미국판에서만, 그리고 미국 밖 거래소 표기가
    없을 때만 인정합니다(다른 나라에서는 같은 티커가 현지 회사를 가리키는 일이 많음).
    """
    names = target.get("names") or [target["label"]]
    if any(_mentions(title, name) for name in names):
        return True
    if target.get("edition", "kr") != "us":
        return False
    return any(_mentions_ticker(title, ticker) for ticker in target.get("tickers", []))


async def collect_news(
    targets: list[dict[str, Any]],
    days: int = 7,
    per_target: int = 20,
    max_articles: int = 80,
) -> list[dict[str, Any]]:
    """targets: [{"label": 표시 이름, "query": 검색어, "kind": "sector"|"stock",
                  "names": 제목에 있어야 하는 이름들(기본 [label]), "tickers": 티커들(선택),
                  "edition": EDITIONS의 키(기본 "kr")}].

    검색은 본문까지 걸리므로, 제목이 대상을 실제로 다루는 기사만 남깁니다(is_relevant 참고).
    (예: 분야 '조선' 검색에 매체명 '조선일보'만 걸린 기사 제외)
    """
    semaphore = asyncio.Semaphore(3)

    async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        async def run(target: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                found = await search_news(client, target["query"], days, target.get("edition", "kr"))
            edition = EDITIONS.get(target.get("edition", "kr"), EDITIONS["kr"])
            relevant = [article for article in found if is_relevant(article["title"], target)]
            relevant.sort(key=lambda article: article["published_ts"], reverse=True)
            return [
                {
                    **article,
                    "matched": target["label"],
                    "matched_kind": target.get("kind", "sector"),
                    "lang": edition["lang"],
                    "country": edition["country"],
                }
                for article in merge_similar(relevant)[:per_target]
            ]

        groups = await asyncio.gather(*(run(target) for target in targets))

    articles = [article for group in groups for article in group]
    articles.sort(key=lambda article: article["published_ts"], reverse=True)
    seen_urls: set[str] = set()
    unique = []
    for article in articles:
        if article["url"] not in seen_urls:
            seen_urls.add(article["url"])
            unique.append(article)
    return merge_similar(unique)[:max_articles]


def foreign_targets(
    name: str,
    english_name: str | None,
    symbol: str | None = None,
    us_listed: bool = False,
    world: bool = True,
) -> list[dict[str, Any]]:
    """한국 밖 기사 검색 대상.

    미국 상장 종목은 미국판에서 회사명·티커로 찾고, world=True면 다른 나라 판에서 회사명으로 찾습니다.
    국내 종목은 영문 회사명으로 미국판을 포함한 모든 해외 판에서 찾습니다(해외에서 어떻게 보는지).
    """
    english = clean_company_name(english_name)
    names = [english] if len(english) >= 4 else []
    targets: list[dict[str, Any]] = []
    if us_listed:
        tickers = [symbol] if symbol and len(symbol) >= 2 else []
        base = {"label": name, "kind": "stock", "names": names, "tickers": tickers, "edition": "us"}
        if names:
            targets.append({**base, "query": f'"{english}" stock'})
        if tickers:
            targets.append({**base, "query": f"{symbol} stock"})
    editions = (WORLD_EDITIONS if us_listed else ("us", *WORLD_EDITIONS)) if world else ()
    if names:
        targets.extend(
            {"label": name, "kind": "stock", "names": names, "query": f'"{english}"', "edition": edition}
            for edition in editions
        )
    return targets


def stock_targets(
    name: str,
    symbol: str | None = None,
    english_name: str | None = None,
    us_listed: bool = False,
) -> list[dict[str, Any]]:
    """종목 하나에 대한 검색 대상: 국내 기사 2종 + 해외 기사(foreign_targets)."""
    return [
        {"label": name, "query": f'"{name}"', "kind": "stock"},
        {"label": name, "query": f'"{name}" 주가 OR 실적 OR 전망 OR 특징주', "kind": "stock"},
        *foreign_targets(name, english_name, symbol, us_listed),
    ]


def sector_targets(
    sector_name: str,
    stocks: list[dict[str, Any]],
    us_listed: bool = False,
    world_stocks: int = 0,
    include_sector: bool = True,
) -> list[dict[str, Any]]:
    """분야 검색 대상: 분야명 2종('반도체 관련주', '반도체주')과 시총 상위 종목들.

    world_stocks: 앞에서부터 이 개수의 종목은 다른 나라 판 기사도 찾습니다(english_name이 있어야 함).
    """
    targets: list[dict[str, Any]] = [
        {"label": sector_name, "query": f'"{sector_name}" 주가 OR 관련주 OR 업종', "kind": "sector"},
        {"label": sector_name, "query": f'"{sector_name}주" OR "{sector_name} 업계" OR "{sector_name} 산업"', "kind": "sector"},
    ] if include_sector else []
    for index, stock in enumerate(stocks):
        name = stock.get("name")
        if not name or name == sector_name:
            continue
        targets.append({"label": name, "query": f'"{name}"', "kind": "stock"})
        targets.extend(foreign_targets(
            name, stock.get("english_name"), stock.get("symbol"), us_listed, world=index < world_stocks
        ))
    return targets

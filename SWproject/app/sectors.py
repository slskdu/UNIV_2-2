import asyncio
import logging
import time
from collections import Counter
from datetime import date, datetime
from typing import Any

from .history import build_equal_weight_index, fetch_daily_candles, summarize_trend
from .news_classifier import classify_and_digest, tone_price_signal
from .redis_client import get_json, set_json
from .sector_news import KST, collect_news, sector_targets
from .toss_api import TossApiClient
from .translator import attach_korean_titles

logger = logging.getLogger(__name__)

FLOW_DURATIONS = ("1d", "1w", "1mo", "3mo")
DURATION_LABELS = {"1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월", "6mo": "6개월", "1y": "1년"}
SECTOR_SPACING_SECONDS = 0.22  # SECTOR 그룹 한도(초당 5회) 안쪽으로 호출
SECTOR_LIST_TTL_SECONDS = 24 * 60 * 60  # 공식 문서 권장: 섹터 목록은 하루 한 번 조회
SECTOR_FLOW_REFRESH_SECONDS = 600
SECTOR_FLOW_TTL_SECONDS = SECTOR_FLOW_REFRESH_SECONDS * 3
MAX_CHILD_SECTORS = 12
WORLD_NEWS_STOCKS = 2  # 분야마다 다른 나라 판 기사까지 찾는 대표 종목 수

_sector_list_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_sector_call_lock = asyncio.Lock()  # 연속 상세 조회가 서로 끼어들어 한도를 넘지 않게 합니다.
_flow_build_lock = asyncio.Lock()


def _rate(value: Any) -> float | None:
    """토스의 소수비율 등락률을 % 단위로 바꿉니다."""
    try:
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


def _amount(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def get_sector_list(api: TossApiClient) -> list[dict[str, Any]]:
    global _sector_list_cache
    loaded_at, sectors = _sector_list_cache
    if not sectors or time.monotonic() - loaded_at > SECTOR_LIST_TTL_SECONDS:
        sectors = await api.fetch_sectors()
        _sector_list_cache = (time.monotonic(), sectors)
    return sectors


def flow_label(rates: dict[str, float | None]) -> str:
    """1일·1주·1개월 등락률의 조합으로 분야의 현재 흐름 단계를 이름 붙입니다."""
    day, week, month = rates.get("1d"), rates.get("1w"), rates.get("1mo")
    if day is None or week is None or month is None:
        return "자료 부족"
    if week > 0 and month > 0:
        return "상승 지속" if day >= 0 else "상승 중 단기 조정"
    if week < 0 and month < 0:
        return "하락 지속" if day <= 0 else "하락 중 반등 시도"
    if week > 0:
        return "반등 전환"
    if week < 0:
        return "상승 후 꺾임"
    return "횡보"


async def _sector_rates(api: TossApiClient, sector_ids: list[str], market_country: str) -> dict[str, dict[str, Any]]:
    """섹터 상세를 한도(초당 5회) 안쪽 간격으로 차례로 조회해 기간별 등락률(%)을 모읍니다."""
    details: dict[str, dict[str, Any]] = {}
    async with _sector_call_lock:
        for sector_id in sector_ids:
            detail = await api.fetch_sector_detail(sector_id, market_country)
            details[sector_id] = {
                "name": detail.get("name"),
                "depth": detail.get("depth"),
                "summary": detail.get("summary"),
                "rates": {
                    item.get("duration"): _rate(item.get("changeRate"))
                    for item in detail.get("performance", {}).get("changeRates", [])
                },
            }
            await asyncio.sleep(SECTOR_SPACING_SECONDS)
    return details


async def build_sector_flow(api: TossApiClient, market_country: str) -> dict[str, Any]:
    """대분류 섹터 전체의 기간별 등락률 표와 흐름 단계를 만듭니다.

    섹터 랭킹 API는 상위 100개만 주기 때문에 하락장에서는 약한 분야가 잘려 나갑니다.
    그래서 등락률은 대분류마다 섹터 상세를 조회해 채우고, 랭킹은 거래대금·시가총액 보강에만 씁니다.
    """
    sector_list = await get_sector_list(api)
    majors = [sector for sector in sector_list if sector.get("depth") == 0]
    amounts = {
        row.get("sectorId"): row
        for row in await api.fetch_sector_rankings(market_country, "1d", "MARKET_TRADING_AMOUNT")
    }
    details = await _sector_rates(api, [sector["sectorId"] for sector in majors], market_country)

    sectors = []
    for sector in majors:
        sector_id = sector["sectorId"]
        rates = details[sector_id]["rates"]
        if all(rate is None for rate in rates.values()):
            continue  # 이 시장에 구성 종목이 없는 분야
        amount_row = amounts.get(sector_id, {})
        sectors.append({
            "sector_id": sector_id,
            "name": sector.get("name"),
            "depth": 0,
            "rates": rates,
            "flow": flow_label(rates),
            "trading_amount": _amount(amount_row.get("tradingAmount")),
            "market_cap": _amount(amount_row.get("marketCapitalization")),
            "currency": amount_row.get("currency") or "KRW",
        })
    sectors.sort(key=lambda item: (item["rates"].get("1d") is None, -(item["rates"].get("1d") or 0)))

    return {
        "market": market_country,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "durations": list(FLOW_DURATIONS),
        "sector_count": len(sectors),
        "overview": _flow_overview(sectors),
        "sectors": sectors,
        "note": (
            "토스증권 공식 섹터(대분류) 기준입니다. 금액은 US도 원화 환산값이며 거래대금 상위 100위 밖 분야는 "
            "금액이 비어 있습니다. 구성 종목이 적은 분야는 등락률이 한두 종목에 좌우될 수 있습니다."
        ),
    }


def sector_flow_key(market: str) -> str:
    return f"insight:sector-flow:{market.lower()}"


async def get_sector_flow(api: TossApiClient, market: str, force: bool = False) -> dict[str, Any]:
    """캐시된 분야 흐름을 반환하고, 없거나 force면 새로 만들어 저장합니다."""
    key = sector_flow_key(market)
    if not force:
        cached = await get_json(key)
        if cached is not None:
            return cached
    async with _flow_build_lock:
        if not force:
            cached = await get_json(key)
            if cached is not None:
                return cached
        flow = await build_sector_flow(api, market.upper())
        await set_json(key, flow, SECTOR_FLOW_TTL_SECONDS)
        return flow


async def sector_flow_loop(api: TossApiClient) -> None:
    """분야 흐름은 만드는 데 10초쯤 걸리므로 화면 요청과 무관하게 주기적으로 미리 갱신합니다."""
    while True:
        for market in ("kr", "us"):
            try:
                await get_sector_flow(api, market, force=True)
                logger.info("sector flow refreshed: %s", market)
            except Exception:
                logger.exception("sector flow refresh failed: %s", market)
        await asyncio.sleep(SECTOR_FLOW_REFRESH_SECONDS)


def _flow_overview(sectors: list[dict[str, Any]]) -> dict[str, Any]:
    major = [item for item in sectors if item["rates"].get("1d") is not None]
    counts = Counter(item["flow"] for item in major)
    advancing = sum(1 for item in major if item["rates"]["1d"] > 0)

    def brief(item: dict[str, Any]) -> dict[str, Any]:
        return {"sector_id": item["sector_id"], "name": item["name"], "rates": item["rates"], "flow": item["flow"]}

    by_day = sorted(major, key=lambda item: item["rates"]["1d"], reverse=True)
    strongest, weakest = by_day[:3], by_day[::-1][:3]
    turning_up = [item for item in major if item["flow"] in {"반등 전환", "하락 중 반등 시도"}]
    turning_down = [item for item in major if item["flow"] in {"상승 후 꺾임", "상승 중 단기 조정"}]

    if not major:
        summary = "섹터 랭킹 자료가 없습니다."
    else:
        ratio = advancing / len(major)
        mood = "상승 분야가 우세" if ratio >= 0.6 else "하락 분야가 우세" if ratio <= 0.4 else "상승·하락 분야가 엇갈림"
        strong_text = ", ".join(f"{item['name']} {item['rates']['1d']:+.2f}%" for item in strongest)
        weak_text = ", ".join(f"{item['name']} {item['rates']['1d']:+.2f}%" for item in weakest)
        summary = (
            f"대분류 {len(major)}개 중 {advancing}개가 오늘 상승해 {mood}입니다. "
            f"강한 분야는 {strong_text}, 약한 분야는 {weak_text}입니다. "
            f"1주·1개월 모두 오른 '상승 지속' 분야는 {counts['상승 지속']}개, "
            f"모두 내린 '하락 지속' 분야는 {counts['하락 지속']}개입니다."
        )

    return {
        "sector_count": len(major),
        "advancing": advancing,
        "flow_counts": dict(counts),
        "strongest": [brief(item) for item in strongest],
        "weakest": [brief(item) for item in weakest],
        "turning_up": [brief(item) for item in turning_up[:5]],
        "turning_down": [brief(item) for item in turning_down[:5]],
        "summary": summary,
    }


async def english_name_for(api: TossApiClient, stock: dict[str, Any]) -> str | None:
    """해외 기사 검색에 쓸 영문 회사명. stock은 종목 기본 정보(symbol, market, currency, englishName)."""
    if stock.get("currency") == "USD":
        return stock.get("englishName")
    return await api.fetch_public_name(stock["symbol"], stock.get("market"))


async def with_english_names(api: TossApiClient, stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """분야 구성 종목에 해외 기사 검색용 영문 회사명(english_name)을 붙입니다."""
    try:
        details = await api.fetch_stock_details([stock["symbol"] for stock in stocks if stock.get("symbol")])
        names = await asyncio.gather(*(
            english_name_for(api, details[stock["symbol"]]) if stock.get("symbol") in details else asyncio.sleep(0)
            for stock in stocks
        ))
    except Exception:
        logger.exception("english name lookup failed")
        return stocks
    return [{**stock, "english_name": name} for stock, name in zip(stocks, names)]


async def build_sector_report(
    api: TossApiClient,
    sector_id: str,
    market_country: str,
    days: int = 7,
    news_stock_count: int = 5,
) -> dict[str, Any]:
    """분야 한 곳의 기간별 등락률, 구성 종목, 분류된 기사와 논조 요약을 묶습니다."""
    sector_list = await get_sector_list(api)
    child_ids = [
        sector["sectorId"] for sector in sector_list if sector.get("parentSectorId") == sector_id
    ][:MAX_CHILD_SECTORS]
    details = await _sector_rates(api, [sector_id, *child_ids], market_country)
    detail = details[sector_id]
    rates = detail["rates"]
    children = [
        {
            "sector_id": child_id,
            "name": details[child_id]["name"],
            "rates": details[child_id]["rates"],
            "flow": flow_label(details[child_id]["rates"]),
        }
        for child_id in child_ids
        if any(rate is not None for rate in details[child_id]["rates"].values())
    ]
    children.sort(key=lambda item: -(item["rates"].get("1d") or 0))
    stocks = await api.fetch_sector_stocks(sector_id, market_country)
    top_stocks = [
        {
            "symbol": stock.get("symbol"),
            "name": stock.get("name"),
            "trading_amount": _amount(stock.get("tradingAmount")),
            "market_cap": _amount(stock.get("marketCapitalization")),
            "currency": stock.get("currency"),
        }
        for stock in stocks[:10]
    ]

    name = detail.get("name") or sector_id
    news_stocks = top_stocks[:news_stock_count]
    news_stocks = await with_english_names(api, news_stocks[:WORLD_NEWS_STOCKS]) + news_stocks[WORLD_NEWS_STOCKS:]
    targets = sector_targets(
        name, news_stocks, us_listed=market_country == "US", world_stocks=WORLD_NEWS_STOCKS
    )
    articles = await collect_news(targets, days=days, per_target=15, max_articles=100)
    # 번역을 먼저 붙여야 일본어·독일어 등 기사도 번역 제목으로 분류할 수 있습니다.
    articles = await attach_korean_titles(articles)
    articles, digest = await classify_and_digest(articles, name, days)

    # 기사 조회 기간과 가장 가까운 공식 등락률 기간으로 논조-가격 방향을 비교합니다.
    compare_duration = "1d" if days <= 1 else "1w" if days <= 10 else "1mo"
    return {
        "sector_id": sector_id,
        "name": name,
        "market": market_country,
        "depth": detail.get("depth"),
        "summary": detail.get("summary"),
        "rates": rates,
        "flow": flow_label(rates),
        "children": children,
        "stock_count": len(stocks),
        "top_stocks": top_stocks,
        "news_days": days,
        "news_digest": digest,
        "tone_price_signal": tone_price_signal(
            digest, rates.get(compare_duration), f"분야 {DURATION_LABELS[compare_duration]}"
        ),
        "articles": articles,
    }


async def build_sector_range_trend(
    api: TossApiClient,
    sector_id: str,
    market_country: str,
    start: date,
    end: date,
    stock_count: int = 5,
) -> dict[str, Any]:
    """임의 기간의 분야 흐름. 공식 섹터 지수 캔들이 없어 시총 상위 종목의 동일 가중 지수로 근사합니다."""
    detail = (await _sector_rates(api, [sector_id], market_country))[sector_id]
    stocks = (await api.fetch_sector_stocks(sector_id, market_country))[:stock_count]
    series = await asyncio.gather(*(
        fetch_daily_candles(api, stock["symbol"], start, end) for stock in stocks
    ))
    series_by_symbol = {stock["symbol"]: rows for stock, rows in zip(stocks, series)}
    index_rows = build_equal_weight_index(series_by_symbol)

    members = []
    for stock in stocks:
        rows = series_by_symbol[stock["symbol"]]
        change = (rows[-1]["close"] / rows[0]["close"] - 1) * 100 if len(rows) >= 2 and rows[0]["close"] else None
        members.append({
            "symbol": stock["symbol"],
            "name": stock.get("name"),
            "change_rate": round(change, 2) if change is not None else None,
        })

    return {
        "sector_id": sector_id,
        "name": detail.get("name") or sector_id,
        "market": market_country,
        "candles": index_rows,
        "trend": summarize_trend(index_rows),
        "members": members,
        "note": (
            f"시가총액 상위 {len(stocks)}개 종목을 시작일 100으로 맞춰 동일 가중 평균한 근사 지수입니다. "
            "공식 섹터 지수가 아니며, 공통 거래일만 사용합니다."
        ),
    }

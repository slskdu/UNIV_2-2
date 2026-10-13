import hashlib
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request

from ..article_summary import ai_enabled, google_article_id, summarize_google_article
from ..briefing import build_combined_briefing, build_market_briefing
from ..history import (
    INDICATOR_NAMES,
    describe_warnings,
    detect_price_adjustments,
    fetch_daily_candles,
    summarize_trend,
)
from ..news_classifier import classify_and_digest, tone_price_signal
from ..redis_client import cached_json, get_json, set_json
from ..sector_news import collect_news, stock_targets
from ..sectors import build_sector_range_trend, build_sector_report, english_name_for, get_sector_flow
from ..toss_api import TossApiClient, TossApiError
from ..translator import attach_korean_titles

router = APIRouter(prefix="/api/v1", tags=["insight"])

SYMBOL_PATTERN = r"^[A-Za-z0-9.\-]{1,15}$"
SECTOR_ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"
MAX_RANGE_DAYS = 365 * 5

SECTOR_REPORT_TTL = 600
HISTORY_TTL = 300
NEWS_TTL = 600
ARTICLE_SUMMARY_TTL = 7 * 24 * 60 * 60
BRIEFING_TTL = 300
BRIEFING_RETRY_TTL = 90
ARTICLE_SUMMARY_FAILURE_TTL = 600


def _api(request: Request) -> TossApiClient:
    return request.app.state.toss_api


def _toss_error(error: TossApiError) -> HTTPException:
    # 잘못된 심볼·섹터 등 요청 오류는 그대로 전달하고, 나머지는 상위 API 장애로 봅니다.
    if error.status_code in {400, 404}:
        return HTTPException(status_code=error.status_code, detail="토스 API에서 찾을 수 없는 심볼 또는 분야입니다.")
    return HTTPException(status_code=502, detail="토스 API 호출에 실패했습니다.")


def _resolve_range(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or date.today()
    start = start or end - timedelta(days=90)
    if start >= end:
        raise HTTPException(status_code=422, detail="시작일은 종료일보다 앞서야 합니다.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail="조회 기간은 최대 5년입니다.")
    return start, end


@router.get("/sector-flow")
async def sector_flow(request: Request, market: Literal["kr", "us"] = Query("kr")):
    """공식 섹터(대분류)별 기간 등락률과 흐름 단계. 백그라운드에서 10분마다 갱신됩니다."""
    try:
        return await get_sector_flow(_api(request), market)
    except TossApiError as error:
        raise _toss_error(error) from error


@router.get("/sectors/{sector_id}/report")
async def sector_report(
    request: Request,
    sector_id: str = Path(pattern=SECTOR_ID_PATTERN),
    market: Literal["kr", "us"] = Query("kr"),
    days: int = Query(7, ge=1, le=30),
):
    """분야 상세: 기간별 등락률, 구성 종목, 호재/악재로 분류된 기사와 논조 요약."""
    try:
        return await cached_json(
            f"insight:sector-report:v6:{market}:{sector_id}:{days}",
            SECTOR_REPORT_TTL,
            lambda: build_sector_report(_api(request), sector_id, market.upper(), days),
        )
    except TossApiError as error:
        raise _toss_error(error) from error


@router.get("/sectors/{sector_id}/trend")
async def sector_trend(
    request: Request,
    sector_id: str = Path(pattern=SECTOR_ID_PATTERN),
    market: Literal["kr", "us"] = Query("kr"),
    start: date | None = Query(None),
    end: date | None = Query(None),
):
    """임의 기간의 분야 흐름(시총 상위 종목 동일 가중 근사 지수)."""
    start, end = _resolve_range(start, end)
    try:
        result = await cached_json(
            f"insight:sector-trend:{market}:{sector_id}:{start}:{end}",
            HISTORY_TTL,
            lambda: build_sector_range_trend(_api(request), sector_id, market.upper(), start, end),
        )
    except TossApiError as error:
        raise _toss_error(error) from error
    if not result["trend"]:
        raise HTTPException(status_code=404, detail="해당 기간에 비교할 수 있는 구성 종목 일봉이 없습니다.")
    return result


@router.get("/price-history")
async def price_history(
    request: Request,
    symbol: str = Query(pattern=SYMBOL_PATTERN),
    start: date | None = Query(None),
    end: date | None = Query(None),
):
    """종목 또는 지수(KOSPI·KOSDAQ)의 임의 기간 일봉과 흐름 요약."""
    start, end = _resolve_range(start, end)
    api = _api(request)
    symbol = symbol.upper()
    indicator = symbol in INDICATOR_NAMES

    async def load():
        candles = await fetch_daily_candles(api, symbol, start, end)
        adjustments, warnings = [], []
        if indicator:
            name, market, currency = INDICATOR_NAMES[symbol], "INDEX", None
        else:
            stock = (await api.fetch_stock_details([symbol])).get(symbol, {})
            name, market = stock.get("name") or symbol, stock.get("market")
            currency = stock.get("currency") or (candles[-1]["currency"] if candles else None)
            # 차트는 수정주가 기준입니다. 원주가와 비교해 기간 안의 병합·분할을 찾아 알려 줍니다.
            try:
                raw_candles = await fetch_daily_candles(api, symbol, start, end, adjusted=False)
                adjustments = detect_price_adjustments(candles, raw_candles)
                warnings = describe_warnings(await api.fetch_stock_warnings(symbol))
            except TossApiError:
                pass  # 부가 정보라 실패해도 주가 흐름은 그대로 보여 줍니다.
        return {
            "symbol": symbol,
            "name": name,
            "market": market,
            "currency": currency,
            "price_basis": "지수" if indicator else "수정주가",
            "adjustments": adjustments,
            "warnings": warnings,
            "requested": {"start": start.isoformat(), "end": end.isoformat()},
            "trend": summarize_trend(candles),
            "candles": candles,
        }

    try:
        result = await cached_json(f"insight:price-history:v2:{symbol}:{start}:{end}", HISTORY_TTL, load)
    except TossApiError as error:
        raise _toss_error(error) from error
    if not result["trend"]:
        raise HTTPException(status_code=404, detail="해당 기간의 일봉이 2개 미만입니다.")
    return result


@router.get("/stock-news")
async def stock_news(
    request: Request,
    symbol: str = Query(pattern=SYMBOL_PATTERN),
    days: int = Query(7, ge=1, le=30),
):
    """지정 종목의 최근 기사를 호재/악재/중립과 사건 유형으로 분류해 나열합니다."""
    api = _api(request)
    symbol = symbol.upper()

    async def load():
        stock = (await api.fetch_stock_details([symbol])).get(symbol)
        if not stock or not stock.get("name"):
            return {"found": False}
        name = stock["name"]
        try:
            english_name = await english_name_for(api, stock)
        except Exception:
            english_name = None
        articles = await collect_news(
            stock_targets(name, symbol, english_name, us_listed=stock.get("currency") == "USD"),
            days=days, per_target=40, max_articles=100,
        )
        articles = await attach_korean_titles(articles)
        articles, digest = await classify_and_digest(articles, name, days)

        change_rate = None
        candles = await fetch_daily_candles(api, symbol, date.today() - timedelta(days=days + 5), date.today())
        window = [candle for candle in candles if candle["date"] >= (date.today() - timedelta(days=days)).isoformat()]
        if len(window) >= 2 and window[0]["close"]:
            change_rate = round((window[-1]["close"] / window[0]["close"] - 1) * 100, 2)
        return {
            "found": True,
            "symbol": symbol,
            "name": name,
            "news_days": days,
            "change_rate": change_rate,
            "news_digest": digest,
            "tone_price_signal": tone_price_signal(digest, change_rate, f"최근 {days}일"),
            "articles": articles,
        }

    try:
        result = await cached_json(f"insight:stock-news:v6:{symbol}:{days}", NEWS_TTL, load)
    except TossApiError as error:
        raise _toss_error(error) from error
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="종목 기본 정보를 찾을 수 없습니다.")
    return result


@router.get("/article-summary")
async def article_summary(url: str = Query(max_length=2200)):
    """기사 한 건의 본문을 읽어 요약합니다. 영문 기사는 요약문의 한국어 번역을 함께 줍니다.

    서버가 임의 주소를 대신 조회하지 않도록 기사 목록에 실린 Google 뉴스 링크만 받습니다.
    """
    article_id = google_article_id(url)
    if not article_id:
        raise HTTPException(status_code=422, detail="기사 목록에 있는 기사 주소만 요약할 수 있습니다.")
    # AI 키를 넣거나 빼면 이전 방식으로 만든 요약이 남지 않도록 방식별로 따로 캐시합니다.
    mode = "ai" if ai_enabled() else "rule"
    key = f"insight:article-summary:v2:{mode}:{hashlib.sha1(article_id.encode()).hexdigest()}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    result = await summarize_google_article(url)
    # 차단·일시 오류, 그리고 AI가 실패해 규칙 기반으로 대신한 요약은 잠시 뒤 다시 시도할 수 있게 짧게만 기억합니다.
    settled = result["ok"] and (mode == "rule" or result.get("source") == "ai")
    await set_json(key, result, ARTICLE_SUMMARY_TTL if settled else ARTICLE_SUMMARY_FAILURE_TTL)
    return result


async def _single_market_briefing(api: TossApiClient, market: str, refresh: bool) -> dict:
    key = f"insight:market-briefing:v5:{market}"
    if not refresh:
        cached = await get_json(key)
        if cached is not None:
            return cached
    briefing = await build_market_briefing(api, market)
    # AI가 실패해 규칙 기반으로 대신한 요약은 곧 다시 시도할 수 있게 짧게만 기억합니다.
    # 랭킹 캐시가 잠깐 비어 표본 해석이 빠진 경우도 마찬가지입니다.
    settled = (briefing["narrative"]["method"] == "ai" or not ai_enabled()) and "interpretation" in briefing
    await set_json(key, briefing, BRIEFING_TTL if settled else BRIEFING_RETRY_TTL)
    return briefing


@router.get("/market-briefing")
async def market_briefing(
    request: Request,
    market: Literal["kr", "us", "all"] = Query("kr"),
    refresh: bool = Query(False),
):
    """시장 종합 분석: 지수 흐름, 분야 흐름, 분야 기사 논조, 랭킹 표본 해석과 이를 엮은 요약.

    market=all은 두 시장을 합치지 않고 나란히 비교합니다(같은 분야끼리 동조·엇갈림).
    """
    api = _api(request)
    try:
        if market != "all":
            return await _single_market_briefing(api, market, refresh)

        key = "insight:market-briefing:v5:all"
        if not refresh:
            cached = await get_json(key)
            if cached is not None:
                return cached
        # 두 시장을 동시에 만들면 섹터 API 한도(초당 5회)를 넘을 수 있어 차례로 만듭니다.
        kr = await _single_market_briefing(api, "kr", refresh)
        us = await _single_market_briefing(api, "us", refresh)
        combined = await build_combined_briefing(api, kr, us)
    except TossApiError as error:
        raise _toss_error(error) from error
    settled = combined["narrative"]["method"] == "ai" or not ai_enabled()
    await set_json(key, combined, BRIEFING_TTL if settled else BRIEFING_RETRY_TTL)
    return combined

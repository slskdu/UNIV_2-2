import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from ..article_summary import MAX_HTML_BYTES, ai_enabled, google_article_id, summarize_page, summarize_with_fallbacks
from ..briefing import build_combined_briefing, build_market_briefing
from ..history import (
    INDICATOR_NAMES,
    describe_warnings,
    detect_price_adjustments,
    fetch_daily_candles,
    summarize_trend,
)
from ..news_classifier import classify_and_digest, tone_price_signal
from ..rate_limit import enforce
from ..recommendations import build_recommendations, with_combined
from ..simulation import get_simulation, record_recommendations, run_simulation, track_recommendations
from ..briefing import KST
from ..redis_client import cached_json, get_json, redis, set_json
from ..sector_news import collect_news, stock_targets
from ..sectors import build_sector_range_trend, build_sector_report, english_name_for, get_sector_flow, sector_tone_signal
from ..stock_master import search_stocks
from ..toss_api import TossApiClient, TossApiError
from ..translator import attach_korean_titles
from ..virtual import session_close

router = APIRouter(prefix="/api/v1", tags=["insight"])
logger = logging.getLogger(__name__)

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
TITLE_EXPLANATION_TTL = 24 * 60 * 60
# AI 분류를 기다리는 동안의 규칙 기반 결과. 덧입히기가 서버 재시작 등으로 끊겨도 이 시간 뒤엔 새로 만듭니다.
AI_PENDING_TTL = 180
_ai_upgrades: dict[str, asyncio.Task] = {}


async def staged_news_json(
    key: str,
    ttl: int,
    build_fast: Callable[[], Awaitable[dict[str, Any]]],
    upgrade: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """기사 목록을 규칙 기반 분류로 먼저 돌려주고, AI 분류는 백그라운드에서 덧입혀 같은 캐시 키에 저장합니다.

    AI 결과를 기다리는 동안에는 ai_pending=True가 붙고, 화면은 이 값이 풀릴 때까지 같은 주소를 다시 조회합니다.
    """
    cached = await get_json(key)
    if cached is not None:
        if cached.get("ai_pending") and key not in _ai_upgrades:
            _start_upgrade(key, ttl, cached, upgrade)
        return cached
    result = await build_fast()
    if not ai_enabled() or not result.get("articles"):
        await set_json(key, result, ttl)
        return result
    result["ai_pending"] = True
    await set_json(key, result, AI_PENDING_TTL)
    _start_upgrade(key, ttl, result, upgrade)
    return result


def _start_upgrade(key: str, ttl: int, result: dict[str, Any], upgrade: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> None:
    async def run() -> None:
        try:
            upgraded = await upgrade(dict(result))
        except Exception:
            logger.exception("AI news upgrade failed: %s", key)
            upgraded = dict(result)  # AI가 끝내 실패하면 규칙 기반 결과를 그대로 확정합니다.
        upgraded["ai_pending"] = False
        await set_json(key, upgraded, ttl)

    task = asyncio.create_task(run())
    _ai_upgrades[key] = task
    task.add_done_callback(lambda _: _ai_upgrades.pop(key, None))


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


@router.get("/stocks/search")
async def stock_search(request: Request, q: str = Query(min_length=1, max_length=40)):
    """종목 이름(한글)이나 코드로 찾기. 토스 전체 종목 목록(하루 한 번 받아 둠)에서 최대 10개."""
    try:
        return {"query": q, "results": await search_stocks(_api(request), q)}
    except TossApiError as error:
        raise _toss_error(error) from error


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
    async def build_report() -> dict[str, Any]:
        await enforce(request, "news")  # 캐시에 없어 새로 모을 때만 셉니다
        return await build_sector_report(_api(request), sector_id, market.upper(), days, use_ai=False)

    async def upgrade(result: dict[str, Any]) -> dict[str, Any]:
        result["articles"], digest = await classify_and_digest(result["articles"], result["name"], days)
        result["news_digest"] = digest
        result["tone_price_signal"] = sector_tone_signal(digest, result["rates"], days)
        return result

    try:
        return await staged_news_json(
            f"insight:sector-report:v10:{market}:{sector_id}:{days}",
            SECTOR_REPORT_TTL,
            build_report,
            upgrade,
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
        await enforce(request, "news")  # 캐시에 없어 새로 모을 때만 셉니다
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
        articles, digest = await classify_and_digest(articles, name, days, use_ai=False)

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

    async def upgrade(result: dict[str, Any]) -> dict[str, Any]:
        result["articles"], digest = await classify_and_digest(result["articles"], result["name"], days)
        result["news_digest"] = digest
        result["tone_price_signal"] = tone_price_signal(digest, result["change_rate"], f"최근 {days}일")
        return result

    try:
        result = await staged_news_json(f"insight:stock-news:v10:{symbol}:{days}", NEWS_TTL, load, upgrade)
    except TossApiError as error:
        raise _toss_error(error) from error
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="종목 기본 정보를 찾을 수 없습니다.")
    return result


@router.get("/article-summary")
async def article_summary(
    request: Request,
    url: str = Query(max_length=2200),
    subject: str = Query("", max_length=80),
    title: str = Query("", max_length=400),
    edition: str = Query("", max_length=4),
    alt: list[str] = Query([], max_length=3),
    published: str = Query("", max_length=20),
    ai: bool = Query(True),
):
    """기사 한 건의 본문을 읽어 요약합니다. 영문 기사는 요약문의 한국어 번역을 함께 줍니다.

    서버가 임의 주소를 대신 조회하지 않도록 기사 목록에 실린 Google 뉴스 링크만 받습니다.
    ai=false면 AI 요약 없이 본문 추출·매체 설명문으로만 요약합니다(화면이 자동으로 불러오는 요약. AI 한도를 아낌).
    응답의 ai_available은 AI 요약을 따로 요청할 수 있는지 알려 줍니다.
    """
    article_id = google_article_id(url)
    if not article_id:
        raise HTTPException(status_code=422, detail="기사 목록에 있는 기사 주소만 요약할 수 있습니다.")
    use_ai = ai and ai_enabled()
    key = _summary_key(article_id, subject, use_ai)
    result = await get_json(key)
    if result is None:
        await enforce(request, "ai_summary" if use_ai else "summary")  # 캐시에 없어 새로 요약할 때만 셉니다
        alternates = [item for item in alt if len(item) <= 2200 and google_article_id(item)]
        result = await _store_summary(
            key,
            await summarize_with_fallbacks(
                url, subject.strip(), title.strip(), edition, alternates, _parse_day(published), use_ai
            ),
            use_ai,
        )
    return {**result, "ai_available": ai_enabled()}


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _summary_key(article_id: str, subject: str, use_ai: bool) -> str:
    # AI 요약과 규칙 기반 요약은 따로 캐시합니다(AI 키를 넣거나 빼도 서로 섞이지 않음).
    mode = "ai" if use_ai else "rule"
    # subject(종목·분야)마다 본문 기준 논조가 달라질 수 있어 함께 키에 넣습니다.
    return f"insight:article-summary:v13:{mode}:{hashlib.sha1(f'{article_id}|{subject.strip()}'.encode()).hexdigest()}"


async def _store_summary(key: str, result: dict, use_ai: bool) -> dict:
    # 차단·일시 오류, 그리고 AI가 실패해 규칙 기반으로 대신한 요약은 잠시 뒤 다시 시도할 수 있게 짧게만 기억합니다.
    settled = result["ok"] and (not use_ai or result.get("source") in {"ai", "sec"})
    # 제목 기반 설명은 확장 프로그램으로 원문을 읽으면 바뀔 수 있어 하루만 기억합니다.
    ttl = TITLE_EXPLANATION_TTL if result.get("source") == "title" else ARTICLE_SUMMARY_TTL if settled else ARTICLE_SUMMARY_FAILURE_TTL
    await set_json(key, result, ttl)
    return result


class BrowserPage(BaseModel):
    url: str = Field(max_length=2200)  # 기사 목록의 Google 뉴스 링크(캐시 키)
    subject: str = Field("", max_length=80)
    page_url: str = Field(max_length=2200)  # 브라우저가 실제로 연 언론사 주소
    html: str = Field(max_length=MAX_HTML_BYTES)


@router.post("/article-summary/from-browser")
async def article_summary_from_browser(request: Request, page: BrowserPage, x_toss_extension: str = Header("")):
    """확장 프로그램이 사용자 브라우저에서 연 기사 페이지를 받아 요약합니다.

    서버가 자동으로 받지 못한(차단·유료·스크립트 렌더링) 기사를 위한 경로입니다. 결과는 GET /article-summary와
    같은 캐시 키에 저장되므로, 화면은 평소처럼 요약을 다시 불러오면 됩니다.
    X-Toss-Extension 헤더를 요구해 다른 웹사이트가 이 주소로 요청을 보내지 못하게 합니다(헤더가 있으면 브라우저가
    사전 확인 요청을 보내고, CORS 설정이 POST를 허용하지 않아 막힙니다).
    """
    if x_toss_extension != "1":
        raise HTTPException(status_code=403, detail="확장 프로그램 요청만 받습니다.")
    article_id = google_article_id(page.url)
    if not article_id:
        raise HTTPException(status_code=422, detail="기사 목록에 있는 기사 주소만 요약할 수 있습니다.")
    if not page.page_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="기사 페이지 주소가 올바르지 않습니다.")
    await enforce(request, "browser")
    result = await summarize_page(page.html, page.page_url, page.subject.strip())
    result["via"] = "browser"
    # 서버로는 다시 읽을 수 없는 페이지라, 화면이 자동 요약(규칙)으로 묻든 AI 요약으로 묻든 이 결과를 받게 둘 다 저장합니다.
    use_ai = ai_enabled()
    await _store_summary(_summary_key(article_id, page.subject, False), result, False)
    if use_ai:
        await _store_summary(_summary_key(article_id, page.subject, True), result, True)
    return result


async def _single_market_briefing(api: TossApiClient, market: str, refresh: bool, request: Request | None = None) -> dict:
    key = f"insight:market-briefing:v9:{market}"
    if not refresh:
        cached = await get_json(key)
        if cached is not None:
            return cached
    if request is not None:
        await enforce(request, "analysis")
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
            return await _single_market_briefing(api, market, refresh, request)

        key = "insight:market-briefing:v9:all"
        if not refresh:
            cached = await get_json(key)
            if cached is not None:
                return cached
        # 두 시장을 동시에 만들면 섹터 API 한도(초당 5회)를 넘을 수 있어 차례로 만듭니다.
        await enforce(request, "analysis")
        kr = await _single_market_briefing(api, "kr", refresh)
        us = await _single_market_briefing(api, "us", refresh)
        combined = await build_combined_briefing(api, kr, us)
    except TossApiError as error:
        raise _toss_error(error) from error
    settled = combined["narrative"]["method"] == "ai" or not ai_enabled()
    await set_json(key, combined, BRIEFING_TTL if settled else BRIEFING_RETRY_TTL)
    return combined


RECOMMENDATION_TTL = 30 * 60
RECOMMENDATION_RETRY_TTL = 5 * 60
_recommendation_locks = {"kr": asyncio.Lock(), "us": asyncio.Lock()}


@router.get("/recommendations")
async def recommendations(
    request: Request,
    market: Literal["kr", "us"] = Query("kr"),
    refresh: bool = Query(False),
):
    """추천 테마·종목: 분야 흐름·기사 논조·종목 주가 흐름을 규칙으로 점수화한 순위, AI가 고른 추천 종목, 근거·주의점.

    참고용이며 수익을 보장하지 않습니다. 만드는 데 20~40초 걸려 30분 동안 캐시하고, 동시에 들어온 요청은 한 번만 만듭니다.
    """
    key = f"insight:recommendations:v4:{market}"
    if not refresh:
        cached = await get_json(key)
        if cached is not None:
            return with_combined(cached)
    async with _recommendation_locks[market]:
        if not refresh:
            cached = await get_json(key)
            if cached is not None:
                return with_combined(cached)
        await enforce(request, "analysis")
        try:
            return await _compute_recommendations(_api(request), market)
        except TossApiError as error:
            raise _toss_error(error) from error


async def _compute_recommendations(api: TossApiClient, market: str) -> dict[str, Any]:
    """추천을 새로 만들어 캐시하고 성과 추적용으로 기록합니다(같은 날 다시 만들면 그날 기록을 덮어씀)."""
    result = await build_recommendations(api, market)
    settled = result["method"] == "ai" or not ai_enabled()
    await set_json(f"insight:recommendations:v4:{market}", result, RECOMMENDATION_TTL if settled else RECOMMENDATION_RETRY_TTL)
    try:
        await record_recommendations(market, result)
    except Exception:
        logger.exception("recommendation record failed")
    return result


# --- 추천 매일 자동 기록 ------------------------------------------------------------------------
# 화면에서 AI분석을 누른 날만 기록되면 성과 추적 표본이 들쭉날쭉해, 장이 끝난 뒤 하루 한 번 서버가 직접 만들어 남깁니다.
# 기록 가격은 그날 종가. 장 마감 15분 뒤부터 6시간 안에만 시도하고(서버를 늦게 켠 날은 건너뜀), Redis 표시로 하루 한 번만.
AUTO_RECORD_DELAY = timedelta(minutes=15)
AUTO_RECORD_WINDOW = timedelta(hours=6)
AUTO_RECORD_CHECK_SECONDS = 10 * 60


def due_record_session(market: str, now: datetime) -> str | None:
    """지금 자동 기록할 장(그 시장의 거래일, YYYY-MM-DD). 없으면 None. 주말은 건너뛰고 공휴일은 따지지 않습니다."""
    for offset in (0, 1):
        day = now.date() - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        ready = session_close(market, day.isoformat()) + AUTO_RECORD_DELAY
        if ready <= now < ready + AUTO_RECORD_WINDOW:
            return day.isoformat()
    return None


async def daily_record_loop(api: TossApiClient) -> None:
    while True:
        now = datetime.now(KST)
        for market in ("kr", "us"):
            session = due_record_session(market, now)
            if not session:
                continue
            # 이미 기록했거나 다른 프로세스가 기록 중이면 건너뜀(실패하면 표시를 지워 다음 확인 때 다시)
            flag = f"insight:auto-record:{market}:{session}"
            if not await redis.set(flag, "running", nx=True, ex=2 * 24 * 3600):
                continue
            try:
                async with _recommendation_locks[market]:
                    result = await _compute_recommendations(api, market)
                await redis.set(flag, result.get("generated_at") or "done", ex=2 * 24 * 3600)
                logger.info("daily recommendation recorded: %s %s", market, session)
            except Exception:
                await redis.delete(flag)
                logger.exception("daily recommendation record failed: %s", market)
        await asyncio.sleep(AUTO_RECORD_CHECK_SECONDS)


# --- 추천 성과 검증 -------------------------------------------------------------------------

_simulation_tasks: dict[str, asyncio.Task] = {}
SIMULATION_HORIZONS = {5, 20}


@router.get("/simulation")
async def simulation_status(
    market: Literal["kr", "us"] = Query("kr"),
    horizon: int = Query(20),
    top: int = Query(5, ge=3, le=10),
):
    """오늘 돌린 시뮬레이션 결과(또는 진행 상황). 아직 없으면 status=none."""
    if horizon not in SIMULATION_HORIZONS:
        raise HTTPException(status_code=422, detail="보유 기간은 5 또는 20거래일만 고를 수 있습니다.")
    return await get_simulation(market, horizon, top) or {"status": "none"}


@router.post("/simulation")
async def simulation_start(
    request: Request,
    market: Literal["kr", "us"] = Query("kr"),
    horizon: int = Query(20),
    top: int = Query(5, ge=3, le=10),
    x_toss_order: str = Header(""),
):
    """과거 데이터로 추천 규칙을 시뮬레이션합니다(1~3분, 백그라운드). 같은 날 같은 조건은 한 번만 계산합니다."""
    if horizon not in SIMULATION_HORIZONS:
        raise HTTPException(status_code=422, detail="보유 기간은 5 또는 20거래일만 고를 수 있습니다.")
    if x_toss_order != "1":
        raise HTTPException(status_code=403, detail="이 화면에서 보낸 요청만 받습니다.")
    existing = await get_simulation(market, horizon, top)
    if existing and existing.get("status") in {"running", "done"}:
        return existing
    task_key = f"{market}:{horizon}:{top}"
    if task_key in _simulation_tasks:
        return {"status": "running", "progress": "계산 중"}
    await enforce(request, "simulation")
    task = asyncio.create_task(run_simulation(_api(request), market, horizon, top))
    _simulation_tasks[task_key] = task
    task.add_done_callback(lambda _: _simulation_tasks.pop(task_key, None))
    return {"status": "running", "progress": "시작했습니다"}


@router.get("/recommendations/track")
async def recommendation_track(request: Request, market: Literal["kr", "us"] = Query("kr")):
    """AI분석 탭이 남긴 실제 추천·위험 종목의 그 뒤 성과(30분 캐시)."""
    key = f"rec:track:v1:{market}:{date.today().isoformat()}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    await enforce(request, "track")
    try:
        result = await track_recommendations(_api(request), market)
    except TossApiError as error:
        raise _toss_error(error) from error
    await set_json(key, result, 30 * 60)
    return result

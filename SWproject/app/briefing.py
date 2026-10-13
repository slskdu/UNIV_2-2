import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from .ai_client import ai_enabled, chat_completion
from .analytics import analyze_rankings, build_detailed_insights
from .history import fetch_daily_candles, summarize_trend
from .market_insights import interpret_market
from .news_classifier import NEGATIVE, POSITIVE, build_digest, classify_articles, tone_price_signal
from .redis_client import load_rankings
from .sector_news import KST, collect_news, sector_targets
from .sectors import get_sector_flow, with_english_names
from .toss_api import TossApiClient
from .translator import attach_korean_titles

logger = logging.getLogger(__name__)

# 토스 시장 지표는 국내 지수만 제공하므로, 미국은 지수를 따르는 대표 ETF로 대신합니다.
MARKET_GAUGES = {
    "KR": [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")],
    "US": [("SPY", "S&P 500 (SPY ETF)"), ("QQQ", "나스닥100 (QQQ ETF)")],
}
MARKET_LABELS = {"KR": "국장", "US": "미장"}
PERIODS = (("1d", 1), ("1w", 5), ("1mo", 21), ("3mo", 63))  # 거래일 수
NEWS_DAYS = 7
FOCUS_PER_SIDE = 2
NEWS_STOCKS = 3
AI_TIMEOUT_SECONDS = 40.0


async def gauge_snapshot(api: TossApiClient, symbol: str, label: str) -> dict[str, Any] | None:
    """지수(또는 대표 ETF)의 현재 수준, 기간별 등락률, 최근 3개월 흐름."""
    today = date.today()
    candles = await fetch_daily_candles(api, symbol, today - timedelta(days=140), today)
    if len(candles) < 2:
        return None
    closes = [candle["close"] for candle in candles]
    rates = {
        key: round((closes[-1] / closes[-1 - days] - 1) * 100, 2) if len(closes) > days and closes[-1 - days] else None
        for key, days in PERIODS
    }
    trend = summarize_trend(candles[-64:])
    return {
        "symbol": symbol,
        "name": label,
        "date": candles[-1]["date"],
        "close": closes[-1],
        "currency": candles[-1].get("currency"),
        "rates": rates,
        "flow": trend["flow"] if trend else None,
        "max_drawdown": trend["max_drawdown"]["rate"] if trend else None,
    }


async def sector_news_tone(api: TossApiClient, sector: dict[str, Any], market_country: str) -> dict[str, Any]:
    """분야 하나의 최근 기사 논조. 여러 분야를 한꺼번에 보므로 규칙 기반으로만 분류합니다."""
    overseas = market_country == "US"
    try:
        stocks = (await api.fetch_sector_stocks(sector["sector_id"], market_country))[:NEWS_STOCKS]
    except Exception:
        logger.exception("sector stocks failed: %s", sector["sector_id"])
        stocks = []
    if stocks:
        stocks = await with_english_names(api, stocks[:1]) + stocks[1:]
    # 한글 분야명 검색은 국내 기사가 대부분이라, 미장은 그 분야의 미국 대표 종목 기사만 봅니다.
    targets = sector_targets(
        sector["name"], stocks, us_listed=overseas, world_stocks=1, include_sector=not (overseas and stocks)
    )
    articles = await attach_korean_titles(await collect_news(targets, days=NEWS_DAYS, per_target=10, max_articles=30))
    classified = classify_articles(articles)
    digest = build_digest(classified, sector["name"], NEWS_DAYS)

    def pick(tone: str, reverse: bool) -> list[dict[str, Any]]:
        chosen = sorted(
            (article for article in classified if article["tone"] == tone),
            key=lambda article: article["tone_score"],
            reverse=reverse,
        )[:2]
        return [
            {
                key: article[key]
                for key in ("title", "title_ko", "url", "publisher", "published_at", "tone", "category", "lang", "country")
                if key in article
            }
            for article in chosen
        ]

    shown = pick(POSITIVE, True) + pick(NEGATIVE, False)

    return {
        "sector_id": sector["sector_id"],
        "name": sector["name"],
        "rates": sector["rates"],
        "flow": sector["flow"],
        "tone": digest["tone"],
        "positive": digest["positive"],
        "negative": digest["negative"],
        "neutral": digest["neutral"],
        "article_count": digest["article_count"],
        "news_basis": list(dict.fromkeys(target["label"] for target in targets))[:4],
        "signal": tone_price_signal(digest, sector["rates"].get("1w"), "분야 1주"),
        "articles": shown,
    }


def _rate_text(value: float | None) -> str:
    return "자료 없음" if value is None else f"{value:+.2f}%"


def rule_narrative(briefing: dict[str, Any]) -> dict[str, Any]:
    """AI 없이 수치만으로 만드는 요약. AI 호출이 실패했을 때도 이 내용을 씁니다."""
    gauges = briefing["gauges"]
    overview = briefing["sector_overview"]
    interpretation = briefing.get("interpretation")
    label = MARKET_LABELS[briefing["market"]]

    lead = gauges[0] if gauges else None
    headline = (
        f"{label}: {lead['name']} {_rate_text(lead['rates']['1d'])}, " if lead else f"{label}: "
    ) + (
        "상승 분야 우세" if overview["sector_count"] and overview["advancing"] / overview["sector_count"] >= 0.6
        else "하락 분야 우세" if overview["sector_count"] and overview["advancing"] / overview["sector_count"] <= 0.4
        else "분야별 혼조"
    )

    points = []
    for gauge in gauges:
        points.append(
            f"{gauge['name']}: 1일 {_rate_text(gauge['rates']['1d'])}, 1주 {_rate_text(gauge['rates']['1w'])}, "
            f"1개월 {_rate_text(gauge['rates']['1mo'])}. 최근 3개월 흐름은 {gauge['flow'] or '자료 없음'}입니다."
        )
    if overview["turning_up"] or overview["turning_down"]:
        up = ", ".join(item["name"] for item in overview["turning_up"][:3]) or "없음"
        down = ", ".join(item["name"] for item in overview["turning_down"][:3]) or "없음"
        points.append(f"흐름이 바뀌는 분야: 반등 시도 {up} / 상승 뒤 약세 {down}.")
    for sector in briefing["focus_sectors"]:
        if sector["article_count"]:
            points.append(
                f"{sector['name']}({sector['flow']}, 1일 {_rate_text(sector['rates'].get('1d'))}): 기사 {sector['article_count']}건 중 "
                f"호재 {sector['positive']}·악재 {sector['negative']}건으로 논조는 '{sector['tone']}'."
                + (f" {sector['signal']}" if sector["signal"] else "")
            )
    if interpretation:
        points.append(f"랭킹 표본 기준: {interpretation['headline']}. {interpretation['signals'][1]['assessment']}")

    return {"headline": headline, "summary": overview["summary"], "points": points, "method": "rule"}


async def ai_narrative(briefing: dict[str, Any]) -> dict[str, Any]:
    context = {
        "market": MARKET_LABELS[briefing["market"]],
        "지수": briefing["gauges"],
        "분야_개요": briefing["sector_overview"],
        "주목_분야_기사논조": [
            {key: sector[key] for key in ("name", "rates", "flow", "tone", "positive", "negative", "article_count", "signal")}
            | {"대표_기사제목": [article.get("title_ko") or article["title"] for article in sector["articles"]]}
            for sector in briefing["focus_sectors"]
        ],
        "랭킹표본_해석": briefing.get("interpretation"),
        "랭킹표본_상위종목": briefing.get("movers"),
    }
    system = (
        "너는 증권 시황 분석가다. 사용자가 주는 JSON 자료만 근거로 한국어 시황 브리핑을 쓴다. "
        "등락률(rates)은 % 단위다. 자료에 없는 사실·수치·전망은 쓰지 않는다. 기사 제목은 참고 맥락으로만 "
        "언급하고 주가 변동의 원인이라고 단정하지 않는다. 매수·매도 권유를 하지 않는다. "
        "'랭킹표본'은 상승·하락·거래대금 상위 종목만 모은 표본이라 시장 전체를 대표하지 않으니 그렇게 구분해서 쓴다. "
        "단순 나열 대신 지수 흐름, 분야 간 차별화, 기사 논조와 가격의 일치·괴리를 서로 연결해 해석한다. "
        'JSON으로만 답한다: {"headline": "한 줄 제목(40자 이내)", "summary": "4~5문장 종합 해석", '
        '"points": ["근거 수치를 포함한 핵심 포인트 3~5개"]}'
    )
    answer = await chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        temperature=0.3,
        json_mode=True,
        timeout=AI_TIMEOUT_SECONDS,
    )
    parsed = json.loads(answer)
    headline, summary, points = parsed.get("headline"), parsed.get("summary"), parsed.get("points")
    if not (isinstance(headline, str) and isinstance(summary, str) and isinstance(points, list) and summary.strip()):
        raise ValueError("unexpected AI briefing shape")
    return {
        "headline": headline.strip(),
        "summary": summary.strip(),
        "points": [str(point).strip() for point in points if str(point).strip()][:6],
        "method": "ai",
    }


async def build_market_briefing(api: TossApiClient, market: str) -> dict[str, Any]:
    """지수 흐름, 공식 분야 흐름, 분야 기사 논조, 랭킹 표본 해석을 한 화면용으로 종합합니다."""
    market_country = market.upper()

    async def safe_gauge(symbol: str, label: str) -> dict[str, Any] | None:
        try:
            return await gauge_snapshot(api, symbol, label)
        except Exception:
            logger.exception("gauge snapshot failed: %s", symbol)
            return None

    gauges, flow = await asyncio.gather(
        asyncio.gather(*(safe_gauge(symbol, label) for symbol, label in MARKET_GAUGES[market_country])),
        get_sector_flow(api, market),
    )
    overview = flow["overview"]

    focus: list[dict[str, Any]] = []
    for brief in overview["strongest"][:FOCUS_PER_SIDE] + overview["weakest"][:FOCUS_PER_SIDE]:
        if all(brief["sector_id"] != item["sector_id"] for item in focus):
            focus.append(brief)
    focus_sectors = list(await asyncio.gather(*(sector_news_tone(api, sector, market_country) for sector in focus)))

    briefing: dict[str, Any] = {
        "market": market_country,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "gauges": [gauge for gauge in gauges if gauge],
        "sector_overview": overview,
        "sector_flow_generated_at": flow.get("generated_at"),
        "focus_sectors": focus_sectors,
        "news_days": NEWS_DAYS,
    }

    # 랭킹 표본(상승·하락·거래대금 상위)은 수집기가 채운 캐시가 있을 때만 붙입니다.
    rankings = await load_rankings(market.lower())
    analysis = analyze_rankings(rankings, market.lower()) if any(rankings.values()) else None
    if analysis and analysis["stock_count"]:
        insights = build_detailed_insights(rankings, market.lower(), analysis)
        briefing["interpretation"] = interpret_market(insights, [])
        briefing["sample_overview"] = insights["overview"]
        briefing["movers"] = {
            "top_gainers": insights["top_gainers"],
            "top_decliners": insights["top_decliners"],
            "most_traded": insights["most_traded"],
        }

    narrative = rule_narrative(briefing)
    if ai_enabled():
        try:
            narrative = await ai_narrative(briefing)
        except Exception:
            logger.exception("AI market briefing failed; using rule-based narrative")
    briefing["narrative"] = narrative
    briefing["caveat"] = (
        "지수·분야 등락률은 토스증권 데이터, 흐름 단계와 기사 논조는 자체 규칙으로 분류한 값입니다. "
        "기사는 참고 자료이며 주가 변동의 원인을 확정하지 않습니다. 투자 판단을 대신하지 않습니다."
    )
    return briefing


CROSS_GROUPS = (
    ("both_up", "동반 상승"),
    ("both_down", "동반 하락"),
    ("kr_only", "국장만 상승"),
    ("us_only", "미장만 상승"),
)
CROSS_GROUP_SIZE = 5


def compare_sector_flows(kr_flow: dict[str, Any], us_flow: dict[str, Any]) -> dict[str, Any]:
    """같은 섹터 분류를 쓰는 두 시장의 분야를 짝지어, 같이 움직인 분야와 엇갈린 분야를 나눕니다(1일 등락률 기준)."""
    us_by_id = {sector["sector_id"]: sector for sector in us_flow["sectors"]}
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in CROSS_GROUPS}
    compared = 0
    for kr_sector in kr_flow["sectors"]:
        us_sector = us_by_id.get(kr_sector["sector_id"])
        kr_day = kr_sector["rates"].get("1d")
        us_day = us_sector["rates"].get("1d") if us_sector else None
        if kr_day is None or us_day is None:
            continue
        compared += 1
        if kr_day > 0 and us_day > 0:
            key = "both_up"
        elif kr_day < 0 and us_day < 0:
            key = "both_down"
        elif kr_day > 0 and us_day < 0:
            key = "kr_only"
        elif kr_day < 0 and us_day > 0:
            key = "us_only"
        else:
            continue  # 한쪽이 보합
        groups[key].append({
            "sector_id": kr_sector["sector_id"],
            "name": kr_sector["name"],
            "kr": {"rates": kr_sector["rates"], "flow": kr_sector["flow"]},
            "us": {"rates": us_sector["rates"], "flow": us_sector["flow"]},
        })

    counts = {key: len(items) for key, items in groups.items()}
    for key, items in groups.items():
        # 두 시장의 움직임이 함께 큰 분야부터 보여 줍니다.
        items.sort(key=lambda item: abs(item["kr"]["rates"]["1d"]) + abs(item["us"]["rates"]["1d"]), reverse=True)
        groups[key] = items[:CROSS_GROUP_SIZE]
    same = counts["both_up"] + counts["both_down"]
    return {
        "compared": compared,
        "counts": counts,
        "same_direction_ratio": round(same / compared, 3) if compared else 0.0,
        "groups": groups,
        "labels": dict(CROSS_GROUPS),
    }


def combined_rule_narrative(kr: dict[str, Any], us: dict[str, Any], cross: dict[str, Any]) -> dict[str, Any]:
    def lead(briefing: dict[str, Any]) -> str:
        gauge = briefing["gauges"][0] if briefing["gauges"] else None
        return f"{gauge['name']} {_rate_text(gauge['rates']['1d'])}" if gauge else "지수 자료 없음"

    counts = cross["counts"]
    ratio = cross["same_direction_ratio"]
    mood = "두 시장이 대체로 같은 방향" if ratio >= 0.65 else "두 시장이 엇갈림" if ratio <= 0.4 else "분야별로 방향이 갈림"

    def names(key: str) -> str:
        return ", ".join(item["name"] for item in cross["groups"][key][:3]) or "없음"

    return {
        "headline": f"국장 {lead(kr)} · 미장 {lead(us)}, {mood}",
        "summary": (
            f"비교한 {cross['compared']}개 분야 중 동반 상승 {counts['both_up']}개, 동반 하락 {counts['both_down']}개, "
            f"국장만 상승 {counts['kr_only']}개, 미장만 상승 {counts['us_only']}개입니다. "
            f"국장은 {kr['narrative']['headline']}, 미장은 {us['narrative']['headline']}로 요약됩니다."
        ),
        "points": [
            f"동반 상승 분야: {names('both_up')}",
            f"동반 하락 분야: {names('both_down')}",
            f"국장만 오른 분야: {names('kr_only')} / 미장만 오른 분야: {names('us_only')}",
        ],
        "method": "rule",
    }


async def combined_ai_narrative(kr: dict[str, Any], us: dict[str, Any], cross: dict[str, Any]) -> dict[str, Any]:
    def digest(briefing: dict[str, Any]) -> dict[str, Any]:
        return {
            "지수": briefing["gauges"],
            "요약제목": briefing["narrative"]["headline"],
            "요약": briefing["narrative"]["summary"],
            "분야_개요": briefing["sector_overview"]["summary"],
            "주목_분야": [
                {key: sector[key] for key in ("name", "rates", "flow", "tone", "signal")}
                for sector in briefing["focus_sectors"]
            ],
        }

    context = {"국장": digest(kr), "미장": digest(us), "분야_비교(1일 등락률 기준)": cross}
    system = (
        "너는 증권 시황 분석가다. 사용자가 주는 JSON 자료만 근거로 국장과 미장을 비교하는 한국어 브리핑을 쓴다. "
        "두 시장을 따로 나열하지 말고, 같이 움직인 분야와 엇갈린 분야, 지수 흐름의 차이를 연결해 해석한다. "
        "등락률(rates)은 % 단위다. 미장 수치는 한국 시간으로 직전 밤 거래분이라 국장과 시차가 있음을 감안한다. "
        "자료에 없는 사실·수치·전망은 쓰지 않고, 한 시장이 다른 시장의 원인이라고 단정하지 않는다. "
        "매수·매도 권유를 하지 않는다. "
        'JSON으로만 답한다: {"headline": "한 줄 제목(45자 이내)", "summary": "4~5문장 비교 해석", '
        '"points": ["근거 수치를 포함한 핵심 포인트 3~5개"]}'
    )
    answer = await chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        temperature=0.3,
        json_mode=True,
        timeout=AI_TIMEOUT_SECONDS,
    )
    parsed = json.loads(answer)
    headline, summary, points = parsed.get("headline"), parsed.get("summary"), parsed.get("points")
    if not (isinstance(headline, str) and isinstance(summary, str) and isinstance(points, list) and summary.strip()):
        raise ValueError("unexpected AI briefing shape")
    return {
        "headline": headline.strip(),
        "summary": summary.strip(),
        "points": [str(point).strip() for point in points if str(point).strip()][:6],
        "method": "ai",
    }


async def build_combined_briefing(api: TossApiClient, kr: dict[str, Any], us: dict[str, Any]) -> dict[str, Any]:
    """국장·미장 분석을 섞지 않고 나란히 놓은 뒤, 같은 분야끼리 비교한 결과와 비교 해석을 붙입니다."""
    kr_flow, us_flow = await get_sector_flow(api, "kr"), await get_sector_flow(api, "us")
    cross = compare_sector_flows(kr_flow, us_flow)

    narrative = combined_rule_narrative(kr, us, cross)
    if ai_enabled():
        try:
            narrative = await combined_ai_narrative(kr, us, cross)
        except Exception:
            logger.exception("AI combined briefing failed; using rule-based narrative")

    def market_card(briefing: dict[str, Any]) -> dict[str, Any]:
        overview = briefing["sector_overview"]
        return {
            "headline": briefing["narrative"]["headline"],
            "summary": briefing["narrative"]["summary"],
            "method": briefing["narrative"]["method"],
            "advancing": overview["advancing"],
            "sector_count": overview["sector_count"],
            "strongest": overview["strongest"],
            "weakest": overview["weakest"],
        }

    return {
        "market": "ALL",
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "narrative": narrative,
        "gauges": [{**gauge, "market": "KR"} for gauge in kr["gauges"]] + [{**gauge, "market": "US"} for gauge in us["gauges"]],
        "markets": {"kr": market_card(kr), "us": market_card(us)},
        "cross": cross,
        "caveat": (
            "통합은 두 시장의 종목이나 금액을 합치지 않고, 같은 토스 섹터 분류의 분야끼리 1일 등락률을 비교합니다. "
            "미장은 한국 시간으로 직전 밤 거래분이라 국장과 시차가 있습니다. 한 시장의 움직임이 다른 시장의 원인이라는 뜻이 아니며 "
            "투자 판단을 대신하지 않습니다."
        ),
    }

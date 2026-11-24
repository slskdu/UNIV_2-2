"""시장 분석 결과(분야 흐름·기사 논조·종목 주가 흐름)로 테마와 종목을 추천하고, 위험 테마와 종목을 표시합니다.

공개된 수치로 계산한 순위와 AI 분석 결과를 그 근거·주의점과 함께 보여 줍니다(참고용, 수익 보장 없음).
- 테마: 대분류 분야를 1주·20일·60일 등락률 순위와 흐름 단계로 점수화해 '추세 강세'와 '반등 시도'로 나눕니다.
  고른 분야는 최근 기사 논조(호재·악재)로 점수를 보정합니다.
- 종목: 고른 분야의 거래대금 상위 종목을 분야 대비 상대 강도, 20일 평균선 위치, 거래대금으로 점수화하고
  변동성이 크거나 매수 유의사항이 있으면 감점합니다(투자경고·위험·정리매매는 제외).
"""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from .ai_client import ai_enabled, chat_completion, seconds_until_available
from .briefing import KST, MARKET_LABELS, sector_news_tone
from .history import describe_warnings, fetch_daily_candles, summarize_trend
from .sectors import SECTOR_SPACING_SECONDS, _sector_call_lock, get_sector_flow
from .signals import CANDLE_DAYS, cached_warnings, evidence_for
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

TREND_THEMES = 3
REBOUND_THEMES = 2
STOCKS_PER_THEME = 3
STOCK_CANDIDATES = 8  # 분야마다 점수를 매겨 보는 종목 수(시가총액 상위 20개 중 거래대금 순)
RISK_THEMES = 3
RISK_STOCK_CANDIDATES = 6  # 위험 테마는 조회 한도를 아끼려 조금 적게 봅니다
STOCK_CONCURRENCY = 2  # 종목 일봉·유의사항 조회 한도(429)를 넘지 않게 천천히
HIGH_VOLATILITY = 5.0  # 하루 평균 변동폭(%)이 이보다 크면 감점
# 하루에 이만큼 넘게 움직이면 분할·분사·병합 같은 권리 변동일 수 있어(예: 코르테바 분사일 -83.8%), 실제 손익으로 단정하지 않습니다.
PRICE_EVENT_RATE = 40.0
AI_TIMEOUT_SECONDS = 40.0

TREND_FLOWS = {"상승 지속", "상승 중 단기 조정"}
REBOUND_FLOWS = {"반등 전환", "하락 중 반등 시도"}
RISK_FLOWS = {"하락 지속", "상승 후 꺾임"}
FLOW_BONUS = {
    "상승 지속": 0.15, "상승 중 단기 조정": 0.05, "반등 전환": 0.05, "하락 중 반등 시도": 0.0,
    "횡보": 0.0, "상승 후 꺾임": -0.1, "하락 지속": -0.15,
}

CAVEAT = (
    "참고용 분석입니다. 토스증권의 분야·종목 등락률과 최근 기사 논조를 정해진 규칙과 AI로 분석한 결과이며, "
    "지난 흐름이 이어진다거나 수익이 난다는 보장은 없습니다. 위험 표시는 매도 권유가 아니라 약한 흐름·변동성·유의사항을 "
    "알려 주는 것입니다. 근거와 주의점을 함께 보고 매매 판단은 직접 하세요."
)


def _rank(values: list[float | None]) -> list[float]:
    """값의 상대 순위(0~1, 클수록 높음). 값이 없으면 0.5(중간)로 둡니다."""
    known = sorted(value for value in values if value is not None)
    if len(known) < 2:
        return [0.5] * len(values)
    return [0.5 if value is None else sum(1 for other in known if other < value) / (len(known) - 1) for value in values]


def score_sectors(sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """분야마다 모멘텀 점수(0~1 안팎)를 붙입니다. 1주·20일·60일 순위의 가중 합 + 흐름 단계 가산 - 거래 부족 감점."""
    ranks = {key: _rank([sector["rates"].get(key) for sector in sectors]) for key in ("1w", "1mo", "3mo")}
    scored = []
    for index, sector in enumerate(sectors):
        momentum = 0.3 * ranks["1w"][index] + 0.45 * ranks["1mo"][index] + 0.25 * ranks["3mo"][index]
        thin = sector.get("trading_amount") is None  # 거래대금 상위 100위 밖: 몇 종목에 좌우되기 쉬움
        scored.append({
            **sector,
            "score": round(momentum + FLOW_BONUS.get(sector["flow"], 0.0) - (0.1 if thin else 0.0), 4),
            "thin": thin,
        })
    return scored


def pick_themes(scored: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(추세 강세, 반등 시도, 위험) 테마."""
    trend = sorted((sector for sector in scored if sector["flow"] in TREND_FLOWS), key=lambda sector: -sector["score"])
    # 반등 시도는 최근 1주 반등 폭이 큰 순서. 중장기 하락 분야라 점수 대신 반등 강도로 고릅니다.
    rebound = sorted(
        (sector for sector in scored if sector["flow"] in REBOUND_FLOWS),
        key=lambda sector: -(sector["rates"].get("1w") or 0),
    )
    trend, rebound = trend[:TREND_THEMES], rebound[:REBOUND_THEMES]
    # 위험은 1주·20일 모두 내리거나(하락 지속) 오르다 꺾인 분야 중 점수가 가장 낮은 순서.
    # 그런 분야가 모자라면, 추천에 쓰지 않은 분야 중 20일 등락률이 마이너스인 약한 분야로 채웁니다.
    risk = sorted((sector for sector in scored if sector["flow"] in RISK_FLOWS), key=lambda sector: sector["score"])[:RISK_THEMES]
    taken = {sector["sector_id"] for sector in trend + rebound + risk}
    weak = sorted(
        (sector for sector in scored if sector["sector_id"] not in taken and (sector["rates"].get("1mo") or 0) < 0),
        key=lambda sector: sector["score"],
    )
    return trend, rebound, risk + weak[:RISK_THEMES - len(risk)]


def _rate_text(value: float | None) -> str:
    return "자료 없음" if value is None else f"{value:+.2f}%"


def risk_theme_reasons(sector: dict[str, Any], news: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """위험 테마의 (위험 근거, 반대로 볼 점)."""
    rates = sector["rates"]
    reasons = [
        f"흐름 '{sector['flow']}': 1주 {_rate_text(rates.get('1w'))}, 20일 {_rate_text(rates.get('1mo'))}, "
        f"60일 {_rate_text(rates.get('3mo'))}"
    ]
    if sector["flow"] == "상승 후 꺾임":
        reasons.append("20일로는 올랐지만 최근 1주에 내려 상승 흐름이 꺾였습니다.")
    if (rates.get("3mo") or 0) <= -15:
        reasons.append(f"60일 {_rate_text(rates.get('3mo'))}로 중기 하락 폭이 큽니다.")
    if sector.get("thin"):
        reasons.append("분야 거래대금이 적어 몇몇 종목 움직임에 크게 좌우됩니다.")
    counterpoints = []
    if news and news.get("article_count"):
        relevant = news["article_count"] - news.get("unrelated", 0)
        line = f"최근 {relevant}건 기사 중 호재 {news['positive']}·악재 {news['negative']}건 (논조 '{news['tone']}')"
        if news["negative"] >= news["positive"]:
            reasons.append(line)
        else:
            counterpoints.append(line + ": 기사는 호재 쪽이 많아 가격 흐름과 엇갈립니다.")
    if (rates.get("1d") or 0) > 0:
        counterpoints.append(f"오늘은 {_rate_text(rates.get('1d'))}로 올라 반등이 나올 수도 있습니다.")
    if (rates.get("1mo") or 0) <= -20:
        counterpoints.append("이미 많이 내린 만큼 되돌림 반등이 나올 수 있습니다.")
    return reasons, counterpoints


def theme_reasons(sector: dict[str, Any], news: dict[str, Any] | None, kind: str) -> tuple[list[str], list[str]]:
    if kind == "risk":
        return risk_theme_reasons(sector, news)
    rates = sector["rates"]
    reasons = [
        f"흐름 '{sector['flow']}': 1주 {_rate_text(rates.get('1w'))}, 20일 {_rate_text(rates.get('1mo'))}, "
        f"60일 {_rate_text(rates.get('3mo'))}"
    ]
    risks = []
    if kind == "rebound":
        risks.append("중장기로는 아직 하락 흐름이라 반등이 짧게 끝날 수 있습니다.")
    if sector["flow"] == "상승 중 단기 조정":
        risks.append("오늘은 내리고 있어 단기 조정이 이어질 수 있습니다.")
    if (rates.get("1mo") or 0) >= 25:
        risks.append(f"20일 {_rate_text(rates.get('1mo'))}로 이미 많이 올라 되돌림 위험이 큽니다.")
    if sector.get("thin"):
        risks.append("분야 거래대금이 적어 몇몇 종목 움직임에 크게 좌우됩니다.")
    if news and news.get("article_count"):
        relevant = news["article_count"] - news.get("unrelated", 0)
        reasons.append(f"최근 {relevant}건 기사 중 호재 {news['positive']}·악재 {news['negative']}건 (논조 '{news['tone']}')")
        if news["negative"] > news["positive"]:
            risks.append("기사 논조는 악재 쪽이 많아 가격 흐름과 엇갈립니다.")
        if news.get("signal"):
            reasons.append(news["signal"])
    return reasons, risks


def _news_adjustment(news: dict[str, Any] | None) -> float:
    if not news:
        return 0.0
    relevant = news["positive"] + news["negative"] + news["neutral"]
    return 0.1 * (news["positive"] - news["negative"]) / relevant if relevant else 0.0


async def stock_snapshot(api: TossApiClient, stock: dict[str, Any]) -> dict[str, Any] | None:
    """종목 하나의 5·20·60일 수익률, 20일 평균선 위치, 60일 낙폭·변동성, 매수 유의사항."""
    today = date.today()
    candles, warnings = await asyncio.gather(
        # 근거 모듈(52주 위치·60일선)까지 같은 일봉으로 계산하도록 1년치를 한 번에 받습니다.
        fetch_daily_candles(api, stock["symbol"], today - timedelta(days=CANDLE_DAYS), today),
        cached_warnings(api, stock["symbol"]),
    )
    closes = [candle["close"] for candle in candles if candle.get("close")]
    if len(closes) < 25:
        return None

    def change(days: int) -> float | None:
        return round((closes[-1] / closes[-1 - days] - 1) * 100, 2) if len(closes) > days and closes[-1 - days] else None

    trend = summarize_trend(candles[-61:])
    average20 = sum(closes[-20:]) / 20
    event = None
    if trend:
        for day in (trend["worst_day"], trend["best_day"]):
            if day and abs(day["change_rate"]) >= PRICE_EVENT_RATE:
                event = {"date": day["date"], "change_rate": round(day["change_rate"], 1)}
                break
    return {
        "symbol": stock["symbol"],
        "name": stock.get("name") or stock["symbol"],
        "currency": stock.get("currency") or candles[-1].get("currency"),
        "close": closes[-1],
        "day_change": round((closes[-1] / closes[-2] - 1) * 100, 2) if closes[-2] else None,
        "trading_amount": stock.get("trading_amount"),
        "rates": {"5d": change(5), "20d": change(20), "60d": change(60)},
        "above_ma20": closes[-1] > average20,
        "max_drawdown": trend["max_drawdown"]["rate"] if trend else None,
        "volatility": trend["daily_volatility"] if trend else None,
        "warnings": describe_warnings(warnings),
        "price_event": event,
        "_candles": candles,  # 근거 계산용. 응답에 싣기 전에 뺍니다.
    }


def price_event_note(snapshot: dict[str, Any]) -> str | None:
    event = snapshot.get("price_event")
    if not event:
        return None
    return (
        f"{event['date']} 하루 {event['change_rate']:+.1f}% 급변: 분할·분사·병합 같은 권리 변동이면 "
        "수익률·낙폭이 실제 손익과 다를 수 있습니다."
    )


def score_risk_stocks(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """위험 점수(클수록 위험): 같은 분야 안에서 20·60일이 약하고, 20일 평균선 아래, 변동성·낙폭이 크고, 유의사항이 있을수록."""
    r20 = _rank([snapshot["rates"]["20d"] for snapshot in snapshots])
    r60 = _rank([snapshot["rates"]["60d"] for snapshot in snapshots])
    scored = []
    for index, snapshot in enumerate(snapshots):
        score = 0.35 * (1 - r20[index]) + 0.25 * (1 - r60[index]) + (0.15 if not snapshot["above_ma20"] else 0.0)
        score += 0.15 if (snapshot["volatility"] or 0) > HIGH_VOLATILITY else 0.0
        score += 0.1 if (snapshot["max_drawdown"] or 0) <= -20 else 0.0
        if any(warning["severe"] for warning in snapshot["warnings"]):
            score += 0.3
        elif snapshot["warnings"]:
            score += 0.15
        if snapshot.get("price_event"):
            score -= 0.3  # 권리 변동 때문일 수 있는 수치는 위험 근거로 덜 씁니다
        scored.append({**snapshot, "score": round(score, 4)})
    return sorted(scored, key=lambda snapshot: -snapshot["score"])


def score_stocks(snapshots: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """같은 분야 후보끼리 비교한 점수. 추세는 20·60일 강도, 반등은 최근 5일 반등을 더 봅니다. 위험은 score_risk_stocks."""
    if kind == "risk":
        return score_risk_stocks(snapshots)
    eligible = [snapshot for snapshot in snapshots if not any(warning["severe"] for warning in snapshot["warnings"])]
    r5 = _rank([snapshot["rates"]["5d"] for snapshot in eligible])
    r20 = _rank([snapshot["rates"]["20d"] for snapshot in eligible])
    r60 = _rank([snapshot["rates"]["60d"] for snapshot in eligible])
    liquidity = _rank([snapshot["trading_amount"] for snapshot in eligible])
    scored = []
    for index, snapshot in enumerate(eligible):
        if kind == "trend":
            score = 0.4 * r20[index] + 0.3 * r60[index] + 0.1 * liquidity[index]
        else:
            score = 0.4 * r5[index] + 0.2 * r20[index] + 0.2 * liquidity[index]
        score += 0.2 if snapshot["above_ma20"] else 0.0
        if (snapshot["volatility"] or 0) > HIGH_VOLATILITY:
            score -= 0.15
        if snapshot["warnings"]:
            score -= 0.2
        scored.append({**snapshot, "score": round(score, 4)})
    return sorted(scored, key=lambda snapshot: -snapshot["score"])


def risk_stock_reasons(snapshot: dict[str, Any], sector: dict[str, Any]) -> tuple[list[str], list[str]]:
    """위험 종목의 (위험 근거, 반대로 볼 점)."""
    rates, sector_month = snapshot["rates"], sector["rates"].get("1mo")
    reasons = [f"20일 {_rate_text(rates['20d'])}, 60일 {_rate_text(rates['60d'])}"]
    if rates["20d"] is not None and sector_month is not None and rates["20d"] < sector_month:
        reasons.append(f"분야 20일 {_rate_text(sector_month)}보다도 약함 ({rates['20d'] - sector_month:+.1f}%p)")
    if not snapshot["above_ma20"]:
        reasons.append("주가가 20일 평균선 아래")
    if snapshot["max_drawdown"] is not None and snapshot["max_drawdown"] <= -20:
        reasons.append(f"최근 60일 고점 대비 최대 {snapshot['max_drawdown']:.1f}% 하락")
    if (snapshot["volatility"] or 0) > HIGH_VOLATILITY:
        reasons.append(f"하루 평균 변동폭 {snapshot['volatility']:.1f}%로 큼")
    for warning in snapshot["warnings"]:
        reasons.append(f"매수 유의사항: {warning['label']}")
    counterpoints = []
    if (rates["5d"] or 0) > 3:
        counterpoints.append(f"최근 5일은 {_rate_text(rates['5d'])}로 반등 중입니다.")
    if price_event_note(snapshot):
        counterpoints.append(price_event_note(snapshot))
    return reasons, counterpoints


def stock_reasons(snapshot: dict[str, Any], sector: dict[str, Any], kind: str) -> tuple[list[str], list[str]]:
    if kind == "risk":
        return risk_stock_reasons(snapshot, sector)
    rates, sector_month = snapshot["rates"], sector["rates"].get("1mo")
    reasons = []
    if kind == "rebound":
        reasons.append(f"최근 5일 {_rate_text(rates['5d'])} (20일 {_rate_text(rates['20d'])}, 60일 {_rate_text(rates['60d'])})")
    else:
        reasons.append(f"20일 {_rate_text(rates['20d'])}, 60일 {_rate_text(rates['60d'])}")
    if rates["20d"] is not None and sector_month is not None:
        gap = rates["20d"] - sector_month
        reasons.append(f"분야 20일 {_rate_text(sector_month)}보다 {'강함' if gap >= 0 else '약함'} ({gap:+.1f}%p)")
    reasons.append("주가가 20일 평균선 위" if snapshot["above_ma20"] else "주가가 아직 20일 평균선 아래")
    risks = []
    if snapshot["max_drawdown"] is not None and snapshot["max_drawdown"] <= -20:
        risks.append(f"최근 60일 고점 대비 최대 {snapshot['max_drawdown']:.1f}% 하락한 적이 있습니다.")
    if (snapshot["volatility"] or 0) > HIGH_VOLATILITY:
        risks.append(f"하루 평균 변동폭이 {snapshot['volatility']:.1f}%로 큽니다.")
    if (rates["20d"] or 0) >= 40:
        risks.append(f"20일 {_rate_text(rates['20d'])}로 단기 급등해 되돌림 위험이 큽니다.")
    for warning in snapshot["warnings"]:
        risks.append(f"매수 유의사항: {warning['label']}")
    if price_event_note(snapshot):
        risks.append(price_event_note(snapshot))
    return reasons, risks


async def fetch_theme_rows(api: TossApiClient, sector_ids: list[str], market_country: str) -> dict[str, list[dict[str, Any]]]:
    """테마 분야들의 구성 종목을 섹터 API 한도(초당 5회) 안쪽 간격으로 차례로 받습니다."""
    rows: dict[str, list[dict[str, Any]]] = {}
    async with _sector_call_lock:
        for sector_id in sector_ids:
            try:
                rows[sector_id] = await api.fetch_sector_stocks(sector_id, market_country)
            except Exception:
                logger.exception("sector stocks failed: %s", sector_id)
                rows[sector_id] = []
            await asyncio.sleep(SECTOR_SPACING_SECONDS)
    return rows


EVIDENCE_PER_THEME = 5  # 테마마다 근거(수급 포함)를 자세히 보는 상위 종목 수
EVIDENCE_WEIGHT = 0.3  # 규칙 점수에 근거 점수를 섞는 비율


def apply_evidence(ranked: list[dict[str, Any]], evidence: dict[str, dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """근거 점수(0~100, 50이 중립)를 규칙 점수에 섞어 다시 줄 세웁니다. 위험 테마는 근거가 약할수록 위로."""
    for item in ranked:
        detail = evidence.get(item["symbol"])
        if not detail or detail["score"] is None:
            continue
        item["evidence"] = detail
        shift = (detail["score"] - 50) / 50
        item["score"] = round(item["score"] + EVIDENCE_WEIGHT * (-shift if kind == "risk" else shift), 4)
    return sorted(ranked, key=lambda item: -item["score"])


def _without_candles(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "_candles"}


async def theme_stocks(api: TossApiClient, sector: dict[str, Any], rows: list[dict[str, Any]], kind: str,
                       semaphore: asyncio.Semaphore, market_country: str = "KR") -> dict[str, Any]:
    candidates = [
        {**row, "trading_amount": float(row["tradingAmount"]) if row.get("tradingAmount") else None}
        for row in rows[:20]
    ]
    candidates.sort(key=lambda row: -(row["trading_amount"] or 0))

    async def snapshot(row: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            try:
                return await stock_snapshot(api, row)
            except Exception:
                logger.exception("stock snapshot failed: %s", row.get("symbol"))
                return None

    limit = RISK_STOCK_CANDIDATES if kind == "risk" else STOCK_CANDIDATES
    snapshots = [item for item in await asyncio.gather(*(snapshot(row) for row in candidates[:limit])) if item]
    scored = score_stocks(snapshots, kind)
    ranked = []
    for item in scored:
        reasons, risks = stock_reasons(item, sector, kind)
        ranked.append({**item, "reasons": reasons, "risks": risks})
    # 상위 후보는 근거 모듈(이동평균·52주 위치·RSI·거래량·수급·기사 등)로 다시 따져 순위를 보정합니다.
    top = ranked[:EVIDENCE_PER_THEME]
    evidence = await evidence_for(
        api, [{"symbol": item["symbol"], "candles": item["_candles"], "warnings": item["warnings"]} for item in top],
        market_country, semaphore,
    )
    ranked = apply_evidence(top, evidence, kind) + ranked[EVIDENCE_PER_THEME:]
    kept = {item["symbol"] for item in scored}
    # 추천 테마에서 투자경고·위험·정리매매로 뺀 종목은 위험 쪽에 따로 보여 줍니다.
    flagged = [
        {**item, "theme": sector["name"], "theme_sector_id": sector["sector_id"],
         "reasons": [f"매수 유의사항: {warning['label']}" for warning in item["warnings"]], "risks": []}
        for item in snapshots if item["symbol"] not in kept
    ]
    return {"ranked": [_without_candles(item) for item in ranked], "excluded": len(flagged),
            "flagged": [_without_candles(item) for item in flagged]}


COMBINED_PICKS = 5  # 통합 추천 최소 개수(AI 추천이 이보다 많으면 AI 추천을 모두 담음)


def combined_picks(themes: list[dict[str, Any]], ai_picks: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """통합 추천: ① AI·테마 양쪽에서 모두 추천된 종목(근거 점수 순) → ② 나머지 AI 추천 → ③ 남는 자리를 테마 종목 근거 점수 순.

    개수는 AI 추천 수와 COMBINED_PICKS 중 큰 쪽(AI가 고른 종목은 빠짐없이 들어감).
    기존 "AI 추천 종목"·"추천 테마"는 그대로 두고 덧붙이는 목록입니다. 종목마다 via(ai·theme)로 출처를 남깁니다.
    """
    limit = limit or max(COMBINED_PICKS, len(ai_picks))
    def evidence(stock: dict[str, Any]) -> float:
        return (stock.get("evidence") or {}).get("score") or 0

    theme_stocks = sorted(
        ({**stock, "theme": group["name"], "theme_sector_id": group["sector_id"], "theme_kind": group["kind"]}
         for group in themes if group["kind"] != "risk" for stock in group.get("stocks", [])),
        key=lambda stock: -evidence(stock),
    )
    ai_symbols = {stock["symbol"] for stock in ai_picks}
    by_theme = {stock["symbol"]: stock for stock in theme_stocks}
    both = sorted((stock for stock in ai_picks if stock["symbol"] in by_theme), key=lambda stock: -evidence(stock))
    picked, seen = [], set()
    for stock in both + list(ai_picks) + theme_stocks:
        if stock["symbol"] in seen:
            continue
        seen.add(stock["symbol"])
        via = [name for name, present in (("ai", stock["symbol"] in ai_symbols), ("theme", stock["symbol"] in by_theme)) if present]
        picked.append({**by_theme.get(stock["symbol"], {}), **stock, "via": via})
        if len(picked) >= limit:
            break
    return picked


def with_combined(result: dict[str, Any]) -> dict[str, Any]:
    """예전에 저장된 결과에도 통합 추천을 채워 줍니다."""
    if "combined_picks" not in result:
        result = {**result, "combined_picks": combined_picks(result.get("themes", []), result.get("ai_picks", []))}
    return result


def assign_stocks(themes: list[dict[str, Any]]) -> None:
    """여러 분야에 함께 속한 종목은 먼저 나오는(점수가 높은) 테마에만 넣고, 다음 순위 종목으로 채웁니다."""
    used: set[str] = set()
    for theme in themes:
        theme["stocks"] = []
        for stock in theme.pop("ranked"):
            if stock["symbol"] in used or len(theme["stocks"]) >= STOCKS_PER_THEME:
                continue
            used.add(stock["symbol"])
            theme["stocks"].append(stock)


def rule_summary(market: str, trend: list[dict[str, Any]], rebound: list[dict[str, Any]], risk: list[dict[str, Any]] | None = None) -> str:
    label = MARKET_LABELS[market]
    parts = []
    if trend:
        parts.append(f"{label}에서 흐름이 가장 꾸준한 분야는 " + ", ".join(
            f"{theme['name']}(20일 {_rate_text(theme['rates'].get('1mo'))})" for theme in trend) + "입니다.")
    if rebound:
        parts.append("하락하다 최근 반등을 시도하는 분야는 " + ", ".join(
            f"{theme['name']}(1주 {_rate_text(theme['rates'].get('1w'))})" for theme in rebound) + "입니다.")
    if not parts:
        parts.append(f"{label}에 지금 뚜렷하게 오르거나 반등하는 분야가 없습니다.")
    if risk:
        parts.append("흐름이 약해 주의할 분야는 " + ", ".join(
            f"{theme['name']}(20일 {_rate_text(theme['rates'].get('1mo'))})" for theme in risk) + "입니다.")
    return " ".join(parts)


# 추천 종목 수는 AI가 정합니다(근거가 충분한 만큼, 최소 1개 · 후보 목록 안에서). 예전에는 3~5개로 고정했습니다.
AI_PICKS_MIN = 1
AI_RISK_PICKS_MAX = 4
AI_POOL_PER_THEME = 4  # AI에 보내는 테마별 후보 수. 많으면 무료 AI의 분당 토큰 한도를 넘기 쉽습니다.


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _pool_row(stock: dict[str, Any], theme: dict[str, Any]) -> dict[str, Any]:
    """AI에 보내는 후보 종목 한 줄. 무료 AI의 분당 토큰 한도(예: 8천)에 맞게 짧은 키와 반올림 값을 씁니다."""
    row = {
        "symbol": stock["symbol"], "name": stock["name"], "theme": theme["name"],
        "r5": _round(stock["rates"]["5d"]), "r20": _round(stock["rates"]["20d"]), "r60": _round(stock["rates"]["60d"]),
        "ma20": "위" if stock["above_ma20"] else "아래", "mdd": _round(stock["max_drawdown"]), "vol": _round(stock["volatility"]),
    }
    if stock["warnings"]:
        row["warn"] = [warning["label"] for warning in stock["warnings"]]
    if stock.get("price_event"):
        row["event"] = f"{stock['price_event']['date']} 하루 {stock['price_event']['change_rate']:+.1f}%"
    evidence = stock.get("evidence")
    if evidence and evidence.get("score") is not None:
        row["ev"] = evidence["score"]
        row["plus"] = [factor["label"] for factor in evidence["factors"] if factor["signal"] > 0][:4]
        row["minus"] = [factor["label"] for factor in evidence["factors"] if factor["signal"] < 0][:4]
    return row


def _theme_context(theme: dict[str, Any]) -> dict[str, Any]:
    labels = {"trend": "추세 강세", "rebound": "반등 시도", "risk": "위험"}
    news = theme["news"] or {}
    return {
        "kind": labels[theme["kind"]], "name": theme["name"], "flow": theme["flow"],
        "rates": {key: _round(value) for key, value in theme["rates"].items() if key in ("1d", "1w", "1mo", "3mo")},
        "news": f"호재 {news.get('positive', 0)}·악재 {news.get('negative', 0)}" if news.get("article_count") else "기사 없음",
        "titles": [article.get("title_ko") or article["title"] for article in news.get("articles", [])][:2],
    }


def _validated_picks(items: Any, pool: list[tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]], limit: int, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """AI가 고른 종목 중 후보 목록 안에 있는 것만, 중복 없이 limit개까지."""
    by_symbol = {stock["symbol"]: (stock, theme) for (stock, theme), _ in pool}
    picks: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if symbol not in by_symbol or any(pick["symbol"] == symbol for pick in picks):
            continue
        stock, theme = by_symbol[symbol]
        picks.append({
            "symbol": symbol, "name": stock["name"], "currency": stock["currency"], "close": stock["close"],
            "rates": stock["rates"], "warnings": stock["warnings"], "evidence": stock.get("evidence"),
            "volatility": stock.get("volatility"),
            "theme": theme["name"], "theme_sector_id": theme["sector_id"], "theme_kind": theme["kind"],
            **{key: str(item.get(key) or "").strip() for key in keys},
        })
        if len(picks) >= limit:
            break
    return picks


async def ai_recommend(
    market: str,
    themes: list[dict[str, Any]],
    pool: list[tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]],
    risk_pool: list[tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """AI가 테마 근거와 후보 종목 수치를 비교해 추천 종목과 위험 종목을 고르고 해설을 씁니다.

    고를 수 있는 종목은 각 후보 목록(pool, risk_pool) 안으로 제한하고, 목록 밖 종목이나 형식이 틀린 답은 버립니다.
    """
    risk_pool = risk_pool or []
    context = {
        "시장": MARKET_LABELS[market],
        "추천테마": [_theme_context(theme) for theme in themes if theme["kind"] != "risk"],
        "후보종목": [row for _, row in pool],
        "위험테마": [_theme_context(theme) for theme in themes if theme["kind"] == "risk"],
        "위험후보종목": [row for _, row in risk_pool],
    }
    system = (
        "너는 증권 애널리스트다. 사용자가 주는 JSON(규칙으로 고른 추천·위험 테마와, 각 테마의 후보 종목 수치)만 근거로 판단한다. "
        "수치는 모두 % 단위다. 테마 rates의 기간 키는 장이 열린 날 기준 1d=1일, 1w=5일, 1mo=20일, 3mo=60일이다. "
        "종목의 r5·r20·r60은 5일·20일·60일 수익률, ma20은 주가가 20일 평균선 위/아래인지, mdd는 60일 고점 대비 최대 낙폭, "
        "vol은 하루 평균 변동폭, warn은 매수 유의사항이다. ev는 근거 점수(0~100, 50 중립: 이동평균 배열·52주 위치·모멘텀·RSI·"
        "거래량·변동성·낙폭·외국인·기관 수급·유의사항·기사 논조를 합친 값), plus·minus는 긍정·부정 근거 이름이다. "
        "추천 이유와 위험 근거에는 가능하면 plus·minus의 구체적 근거를 인용한다. event가 있는 종목은 그날 분할·분사·병합 같은 권리 변동이 있었을 수 있어 "
        "수익률·낙폭이 실제 손익과 다를 수 있으니, 그 수치만으로 위험하다고 단정하지 말고 쓸 때는 그 점을 밝힌다. "
        "기간은 '1개월·3개월'이나 '거래일' 대신 '20일·60일'처럼 일수로 쓴다. "
        "① 후보종목 가운데 테마 흐름·기사 논조·종목의 상대 강도·20일 평균선 위치를 종합해 주목할 종목을 골라 추천한다(picks). "
        f"개수는 정해져 있지 않다: 근거가 충분한 종목은 모두 고르고 근거가 약하면 적게 고른다(최소 {AI_PICKS_MIN}개, 후보종목 수 이내). "
        "단기 급등, 큰 변동폭, 큰 낙폭, 유의사항은 감점 요소로 따지고, 한 테마에 몰리지 않게 고려한다. "
        f"② 위험후보종목 가운데 약한 흐름·20일 평균선 아래·큰 변동폭·큰 낙폭·유의사항이 겹쳐 특히 주의할 종목을 최대 {AI_RISK_PICKS_MAX}개 고른다(risk_picks). "
        "위험 종목은 매도 권유가 아니라 주의할 점을 알리는 것이다. "
        "반드시 각 목록의 symbol만 고른다. 자료에 없는 사실·수치·실적·정책·목표가는 쓰지 않고(기사 제목에 나온 내용만 인용), "
        "수익을 보장하는 표현과 투자 비중·분산 투자 같은 자산 운용 조언은 쓰지 않는다. "
        "문장에는 r20·mdd 같은 키 이름 대신 '20일 수익률', '최대 낙폭'처럼 풀어 쓴다. "
        "picks의 reason은 근거 수치를 포함한 1~2문장, risk는 주의할 점 1문장. risk_picks의 reason은 위험 근거 수치를 포함한 1~2문장. "
        "summary는 추천 흐름과 위험 흐름을 함께 다룬 3~5문장. 모두 한국어로 쓴다. "
        'JSON으로만 답한다: {"summary": "...", "picks": [{"symbol": "...", "reason": "...", "risk": "..."}], '
        '"risk_picks": [{"symbol": "...", "reason": "..."}]}'
    )
    answer = await chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))}],
        temperature=0.3, json_mode=True, timeout=AI_TIMEOUT_SECONDS,
    )
    parsed = json.loads(answer)
    summary = str(parsed.get("summary") or "").strip()
    picks = _validated_picks(parsed.get("picks"), pool, len(pool), ("reason", "risk"))
    risk_picks = _validated_picks(parsed.get("risk_picks"), risk_pool, AI_RISK_PICKS_MAX, ("reason",))
    if not summary or not picks:
        raise ValueError("unexpected AI recommendation shape")
    return summary, picks, risk_picks


AI_RETRY_WAIT_SECONDS = 60.0  # 무료 AI 분당 한도는 1분 안에 풀립니다


async def recommend_with_retry(market: str, themes: list[dict[str, Any]], pool: list, risk_pool: list) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """테마 기사 분류에 분당 한도를 써 버려 막혔다면, 한도가 곧 풀릴 때(1분 안) 한 번 기다렸다 다시 묻습니다."""
    try:
        return await ai_recommend(market, themes, pool, risk_pool)
    except Exception:
        wait = seconds_until_available()
        if wait is None or wait > AI_RETRY_WAIT_SECONDS:
            raise
        logger.info("AI recommendation retry after %.1fs", wait)
        await asyncio.sleep(wait + 0.5)
        return await ai_recommend(market, themes, pool, risk_pool)


async def build_recommendations(api: TossApiClient, market: str) -> dict[str, Any]:
    market_country = market.upper()
    flow = await get_sector_flow(api, market.lower())
    scored = score_sectors(flow["sectors"])
    trend, rebound, risk = pick_themes(scored)
    chosen = [(sector, "trend") for sector in trend] + [(sector, "rebound") for sector in rebound] + [(sector, "risk") for sector in risk]

    rows = await fetch_theme_rows(api, [sector["sector_id"] for sector, _ in chosen], market_country)

    async def safe_news(sector: dict[str, Any]) -> dict[str, Any] | None:
        try:
            # 테마 기사는 규칙으로만 분류합니다. 무료 AI의 분당 한도를 뒤의 추천·위험 종목 분석에 남겨 두기 위해서입니다.
            return await sector_news_tone(api, sector, market_country, rows[sector["sector_id"]], use_ai=False)
        except Exception:
            logger.exception("theme news failed: %s", sector["sector_id"])
            return None

    semaphore = asyncio.Semaphore(STOCK_CONCURRENCY)
    news_list, stock_list = await asyncio.gather(
        asyncio.gather(*(safe_news(sector) for sector, _ in chosen)),
        asyncio.gather(*(theme_stocks(api, sector, rows[sector["sector_id"]], kind, semaphore, market_country) for sector, kind in chosen)),
    )

    themes, flagged = [], []
    for (sector, kind), news, stocks in zip(chosen, news_list, stock_list):
        reasons, risks = theme_reasons(sector, news, kind)
        themes.append({
            "kind": kind,
            "sector_id": sector["sector_id"],
            "name": sector["name"],
            "flow": sector["flow"],
            "rates": sector["rates"],
            "score": round(sector["score"] + _news_adjustment(news), 4),  # 위험 테마는 낮을수록(악재 많을수록) 위험
            "news": {key: news[key] for key in ("tone", "positive", "negative", "neutral", "article_count", "articles")} if news else None,
            "reasons": reasons,
            "risks": risks,
            "ranked": stocks["ranked"],
            "excluded_stocks": stocks["excluded"],
        })
        if kind != "risk":
            flagged.extend(stocks["flagged"])
    # 같은 구분 안에서는 기사 논조로 보정한 점수 순서로 보여 줍니다(반등은 고른 순서 유지).
    themes = (
        sorted((theme for theme in themes if theme["kind"] == "trend"), key=lambda theme: -theme["score"])
        + [theme for theme in themes if theme["kind"] == "rebound"]
        + sorted((theme for theme in themes if theme["kind"] == "risk"), key=lambda theme: theme["score"])
    )
    # AI는 테마마다 점수 상위 AI_POOL_PER_THEME개(유의사항 제외 후)에서 고릅니다. 여러 테마에 속한 종목은 앞 테마로 한 번만 넣습니다.
    pool: list[tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]] = []
    seen: set[str] = set()
    risk_pool: list[tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]] = []
    for theme in themes:
        target = risk_pool if theme["kind"] == "risk" else pool
        for stock in theme["ranked"][:AI_POOL_PER_THEME]:
            if stock["symbol"] not in seen:
                seen.add(stock["symbol"])
                target.append(((stock, theme), _pool_row(stock, theme)))
    theme_by_id = {theme["sector_id"]: theme for theme in themes}
    for stock in flagged:
        if stock["symbol"] not in seen:
            seen.add(stock["symbol"])
            risk_pool.append(((stock, theme_by_id[stock["theme_sector_id"]]), _pool_row(stock, theme_by_id[stock["theme_sector_id"]])))
    assign_stocks(themes)

    summary, method, ai_picks, ai_risk_picks = rule_summary(market_country, trend, rebound, risk), "rule", [], []
    if ai_enabled() and pool:
        try:
            summary, ai_picks, ai_risk_picks = await recommend_with_retry(market_country, themes, pool, risk_pool)
            method = "ai"
        except Exception:
            logger.exception("AI recommendation failed; using rule-based recommendations only")

    return {
        "market": market_country,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "sector_flow_generated_at": flow.get("generated_at"),
        "summary": summary,
        "method": method,
        "ai_picks": ai_picks,
        "combined_picks": combined_picks(themes, ai_picks),
        "ai_pool_size": len(pool),
        "ai_risk_picks": ai_risk_picks,
        "ai_risk_pool_size": len(risk_pool),
        "flagged_stocks": flagged,
        "themes": themes,
        "method_note": (
            "테마: 대분류 분야의 1주·20일·60일 등락률 순위(가중 0.3·0.45·0.25)와 흐름 단계로 점수를 매기고, "
            "최근 7일 기사 논조(제목 기준 규칙 분류)로 보정했습니다. 종목: 분야의 시가총액 상위 20개 중 거래대금 상위 8개를 같은 분야끼리 비교해 "
            "상대 강도·20일 평균선 위치·거래대금으로 점수를 매기고, 변동성이 크거나 매수 유의사항이 있으면 감점했습니다 "
            "(투자경고·위험·정리매매 종목은 제외해 위험 쪽에 표시). 위험 테마: 1주·20일 모두 내리거나(하락 지속) 오르다 꺾인 분야(모자라면 20일이 마이너스인 분야) 중 "
            "점수가 낮은 순서(악재 기사가 많으면 더 위험), 위험 종목: 분야 안에서 20·60일이 약하고 20일 평균선 아래, "
            "변동성·낙폭이 크거나 유의사항이 있을수록 위험 점수가 높습니다. AI: 테마마다 점수 상위 4개 종목의 수치와 테마 근거·기사 제목을 "
            "비교해 추천 종목(개수는 AI가 근거에 따라 정함)과 위험 최대 4개를 골랐습니다(후보 목록 밖 종목은 고를 수 없음). "
            "통합 추천은 AI 추천을 모두 담고, 5개보다 적으면 테마 종목으로 채웁니다."
        ),
        "caveat": CAVEAT,
    }

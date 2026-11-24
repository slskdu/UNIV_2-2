"""종목별 판단 기록과 성적표(stock memory).

AI분석·AI 점검·자동 운용이 그 종목을 어떻게 판단했는지 시간순으로 쌓고, 판단 뒤 1·5·20거래일 수익률(지수 대비 포함)을
나중에 채워 넣습니다. 지금은 기록·채점만 하고, 판단에 쓰는 것(summarize → 추천·AI 프롬프트)은 데이터가 쌓인 뒤 켭니다(USE_IN_DECISIONS).

저장: Redis stock:memory:v1:{market}:{symbol} = 사건 목록(최근 MEMORY_MAX개), stock:memory:index:v1:{market} = 기록이 있는 종목 집합.
같은 종목·같은 종류·같은 날의 판단은 한 건으로 합칩니다(장중 30분 추천 갱신마다 쌓이지 않게: 처음 가격은 두고 이유·근거만 최신으로).

사건 종류(type)
- ai_pick: AI 추천, ai_risk: AI 위험 지정, pick: 규칙 테마 추천 종목, risk: 규칙 위험 종목
- review_sell / review_hold: 하루 한 번 AI 점검 의견
- buy / sell: 자동 운용 매매(이유 포함)
미래 정보가 섞이지 않게, summarize는 as_of 시점에 이미 끝난 기간의 결과만 셉니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from .briefing import MARKET_GAUGES
from .redis_client import get_json, redis

logger = logging.getLogger(__name__)

MEMORY_MAX = 120
HORIZONS = (1, 5, 20)
REASON_MAX = 200
USE_IN_DECISIONS = False  # 2~4주 쌓인 뒤 켬
GRADE_LOOKBACK_DAYS = 45  # 채점: 이보다 오래된 사건 중 결과가 비어 있는 것은 포기(상장 폐지·거래 정지 등)


def memory_key(market: str, symbol: str) -> str:
    return f"stock:memory:v1:{market.lower()}:{symbol}"


def index_key(market: str) -> str:
    return f"stock:memory:index:v1:{market.lower()}"


def _event(kind: str, symbol: str, name: str, at: str, day: str, price: float | None, **extra: Any) -> dict[str, Any]:
    event = {"type": kind, "symbol": symbol, "name": name, "at": at, "day": day, "price": price}
    for key, value in extra.items():
        if value is not None and value != "":
            event[key] = value[:REASON_MAX] if isinstance(value, str) else value
    return event


def events_from_recommendation(result: dict[str, Any], at: str, day: str) -> list[dict[str, Any]]:
    """AI분석 결과 → 사건들(AI 추천·위험은 이유 문장까지)."""
    events = []
    for theme in result.get("themes", []):
        kind = "risk" if theme["kind"] == "risk" else "pick"
        for stock in theme.get("stocks", []):
            events.append(_event(kind, stock["symbol"], stock["name"], at, day, stock.get("close"),
                                 evidence=(stock.get("evidence") or {}).get("score"), theme=theme.get("name"),
                                 theme_kind=theme.get("kind"), flow=theme.get("flow")))
    for stock in result.get("ai_picks", []):
        events.append(_event("ai_pick", stock["symbol"], stock["name"], at, day, stock.get("close"),
                             evidence=(stock.get("evidence") or {}).get("score"), theme=stock.get("theme"),
                             reason=stock.get("reason"), risk_note=stock.get("risk"), method=result.get("method")))
    for stock in result.get("ai_risk_picks", []):
        events.append(_event("ai_risk", stock["symbol"], stock["name"], at, day, stock.get("close"),
                             evidence=(stock.get("evidence") or {}).get("score"), theme=stock.get("theme"), reason=stock.get("reason")))
    return events


def events_from_review(review: dict[str, dict[str, str]], positions: list[dict[str, Any]], at: str, day: str,
                       weak: bool | None) -> list[dict[str, Any]]:
    """AI 점검 의견 → 사건들. positions(review_positions)의 가격·수치를 함께 남깁니다."""
    by_symbol = {position["symbol"]: position for position in positions}
    events = []
    for symbol, opinion in review.items():
        position = by_symbol.get(symbol, {})
        events.append(_event(f"review_{opinion.get('action')}", symbol, position.get("name", symbol), at, day, position.get("price"),
                             reason=opinion.get("reason"), evidence=position.get("evidence"), theme=position.get("theme"),
                             flow=position.get("theme_flow"), weak=weak, gain=position.get("gain")))
    return events


def events_from_trades(trades: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [_event(trade["action"], trade["symbol"], trade["name"], trade.get("time") or trade["date"], day, trade["price"],
                   reason=trade.get("reason"), result=trade.get("return")) for trade in trades]


def merge(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 종류·같은 날은 한 건으로(처음 가격·시각은 두고 이유·근거만 최신으로), 최근 MEMORY_MAX개.
    자동 운용 매매도 같은 날 여러 포트폴리오에서 같은 종목을 사고팔면 한 건(같은 판단)으로 봅니다."""
    out = list(existing)
    index = {(event["type"], event["day"]): position for position, event in enumerate(out)}
    for event in new:
        key = (event["type"], event["day"])
        if key in index:
            kept = out[index[key]]
            out[index[key]] = {**event, "at": kept["at"], "price": kept.get("price") or event.get("price"),
                               "outcome": kept.get("outcome"), "updated_at": event["at"]}
            if out[index[key]]["outcome"] is None:
                out[index[key]].pop("outcome")
        else:
            index[key] = len(out)
            out.append(event)
    out.sort(key=lambda event: (event["day"], event["at"]))
    return out[-MEMORY_MAX:]


async def remember(market: str, events: list[dict[str, Any]]) -> int:
    """사건들을 종목별로 저장합니다. 저장한 종목 수."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("symbol"):
            by_symbol.setdefault(event["symbol"], []).append(event)
    for symbol, items in by_symbol.items():
        key = memory_key(market, symbol)
        existing = await get_json(key) or []
        await redis.set(key, json.dumps(merge(existing, items), ensure_ascii=False))
        await redis.sadd(index_key(market), symbol)
    return len(by_symbol)


async def safe_remember(market: str, events: list[dict[str, Any]]) -> None:
    """기록 실패가 추천·매매를 막지 않게."""
    try:
        await remember(market, events)
    except Exception:
        logger.exception("stock memory record failed: %s", market)


HISTORY_KINDS = {"ai_pick", "pick", "risk", "ai_risk"}


def events_from_history(market: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """이미 쌓인 하루 추천 기록(rec:history)으로 처음 한 번 채우기. 이유 문장은 그때 남기지 않아 없습니다.
    판단한 날은 그 시장 기준 날짜(미장은 한국 새벽에 만든 기록이 전날 미국 장)."""
    from .briefing import KST
    from .managed_live import price_day

    events = []
    for snapshot in history:
        generated = snapshot.get("generated_at") or f"{snapshot['date']} 23:59"
        day = price_day(market, datetime.fromisoformat(generated).replace(tzinfo=KST))
        for item in snapshot.get("items", []):
            if item.get("kind") in HISTORY_KINDS:
                events.append(_event(item["kind"], item["symbol"], item["name"], generated, day, item.get("price"),
                                     evidence=item.get("evidence"), theme=item.get("theme"), source="history"))
    return events


# --- 채점 -----------------------------------------------------------------------------------

def outcome_for(event: dict[str, Any], closes: list[tuple[str, float]], index: list[tuple[str, float]]) -> dict[str, Any]:
    """판단한 날(day) 뒤 h거래일 종가 수익률. closes·index = [(날짜, 종가)] 과거→최신. 아직 h일이 안 지났으면 그 칸은 비움."""
    base = event.get("price")
    after = [close for day, close in closes if day > event["day"]]
    index_base = next((close for day, close in reversed(index) if day <= event["day"]), None)
    index_after = [close for day, close in index if day > event["day"]]
    outcome = dict(event.get("outcome") or {})
    for horizon in HORIZONS:
        name = f"d{horizon}"
        if name in outcome or not base or len(after) < horizon:
            continue
        outcome[name] = round(after[horizon - 1] / base - 1, 4)
        if index_base and len(index_after) >= horizon:
            outcome[f"x{horizon}"] = round(outcome[name] - (index_after[horizon - 1] / index_base - 1), 4)  # 지수 대비
    return outcome


async def grade_market(api: Any, market: str, today: str, fetch=None) -> int:
    """결과가 비어 있는 사건을 채웁니다(장이 닫혀 있을 때 하루 한 번). 채운 종목 수."""
    from .virtual import _recent_candles

    fetch = fetch or (lambda symbol, start, end: _recent_candles(api, symbol, start, end))
    oldest = (datetime.fromisoformat(today) - timedelta(days=GRADE_LOOKBACK_DAYS)).date().isoformat()
    start = (datetime.fromisoformat(oldest) - timedelta(days=7)).date().isoformat()
    index_symbol = MARKET_GAUGES[market.upper()][0][0]
    index_rows = await fetch(index_symbol, start, today)
    index = [(row["date"], row["close"]) for row in index_rows]
    semaphore = asyncio.Semaphore(2)
    graded = 0

    async def one(symbol: str) -> None:
        nonlocal graded
        key = memory_key(market, symbol)
        events = await get_json(key) or []
        todo = [event for event in events if event["day"] >= oldest and event.get("price")
                and any(f"d{horizon}" not in (event.get("outcome") or {}) for horizon in HORIZONS)]
        if not todo:
            return
        async with semaphore:
            try:
                rows = await fetch(symbol, start, today)
            except Exception:
                logger.exception("stock memory grade failed: %s", symbol)
                return
        closes = [(row["date"], row["close"]) for row in rows if row["date"] < today]  # 오늘 봉은 장중일 수 있어 빼고
        changed = False
        for event in todo:
            outcome = outcome_for(event, closes, [item for item in index if item[0] < today])
            if outcome != (event.get("outcome") or {}):
                event["outcome"] = outcome
                changed = True
        if changed:
            await redis.set(key, json.dumps(events, ensure_ascii=False))
            graded += 1

    symbols = sorted(await redis.smembers(index_key(market)))
    await asyncio.gather(*(one(symbol) for symbol in symbols))
    return graded


# --- 요약(판단에 쓸 때) ---------------------------------------------------------------------------

def summarize(events: list[dict[str, Any]], as_of: str, days: int = 30) -> dict[str, Any]:
    """as_of 기준 최근 days일의 판단 횟수와, 이미 결과가 나온 판단의 평균 5일 수익률(지수 대비)."""
    since = (datetime.fromisoformat(as_of[:10]) - timedelta(days=days)).date().isoformat()
    recent = [event for event in events if since <= event["day"] < as_of[:10]]
    counts: dict[str, int] = {}
    for event in recent:
        counts[event["type"]] = counts.get(event["type"], 0) + 1

    def average(kind: str, field: str = "x5") -> tuple[float | None, int]:
        values = [event["outcome"][field] for event in recent if event["type"] == kind and field in (event.get("outcome") or {})]
        return (round(sum(values) / len(values), 4), len(values)) if values else (None, 0)

    pick_avg, pick_n = average("ai_pick")
    sell_avg, sell_n = average("review_sell")
    return {"counts": counts, "ai_pick_x5": pick_avg, "ai_pick_samples": pick_n,
            "review_sell_x5": sell_avg, "review_sell_samples": sell_n,
            "last": [{key: event.get(key) for key in ("type", "day", "reason")} for event in recent[-3:]]}

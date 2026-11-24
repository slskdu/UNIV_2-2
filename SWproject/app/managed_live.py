"""자동 운용 장중 확인 루프: 장이 열려 있는 동안 5분마다 자동 운용 포트폴리오를 확인해 사고팝니다(가상, 실제 주문 없음).

- 처음 볼 때(또는 자동 운용을 다시 켰을 때) 마지막으로 끝난 장까지를 일봉으로 다시 계산해 장부(ledger)를 만들고, 그 뒤로 장중에 이어 갑니다.
- 사고팔 때는 판단 모듈(app/decision.py)을 씁니다. 확인할 때마다 시장마다 한 번 판단 자료를 만듭니다.
  시장 약세(대표 지수 60일 평균선 아래), 분야 흐름(insight:sector-flow), 종목의 최근 가격 흐름,
  AI 점검(하루 한 번, 그날 첫 확인 때 보유 종목 전체를 AI가 hold/sell로 점검)이 들어갑니다.
- 현재가는 일봉의 오늘 봉(장중에는 지금 가격)으로 봅니다. 오늘 봉이 없으면(휴장일) 거래하지 않습니다.
- 서버 부담: 자동 운용 포트폴리오가 가진 종목, 최근 추천 후보, 대표 지수만 종목마다 일봉 한 번(5분 캐시 공유)씩 받습니다.
  AI는 시장마다 하루 한 번만 부릅니다.
- 자동 운용을 끄거나 다시 켜면(run_id가 바뀜) 예전 장부는 버리고 새로 시작합니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .briefing import KST, MARKET_GAUGES
from .decision import RISK_KINDS, Context, ai_review, metrics, snapshot_info
from .exit_signals import TRAIL_ARM
from .managed import execute_plan, live_tick, needed_symbols, next_session_day, plan_orders, snapshot_id, usable_snapshots
from .redis_client import get_json, redis, set_json
from .simulation import COST, market_is_weak
from .stock_memory import events_from_history, events_from_review, events_from_trades, grade_market, safe_remember
from .toss_api import TossApiClient
from .virtual import (
    MANAGED_LOOKBACK_DAYS,
    _recent_candles,
    ledger_key,
    list_portfolios,
    managed_params,
    replay_managed,
    session_close,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 5 * 60
NEW_YORK = ZoneInfo("America/New_York")
SESSIONS = {"KR": (KST, time(9, 0), time(15, 30)), "US": (NEW_YORK, time(9, 30), time(16, 0))}
LATE_FROM = {"KR": time(14, 30), "US": time(15, 0)}  # 하루 한 번 보는 매도 기준은 이 시각 뒤(마감 무렵)에만
REVIEW_TTL = 3 * 24 * 3600


def market_session(market: str, now: datetime) -> tuple[bool, str]:
    """(지금 정규장이 열려 있는지, 그 시장 기준 오늘 날짜). 주말은 닫힘, 공휴일은 오늘 봉이 없는 것으로 걸러 냅니다."""
    zone, opens, closes = SESSIONS[market.upper()]
    local = now.astimezone(zone)
    is_open = local.weekday() < 5 and opens <= local.time() < closes
    return is_open, local.date().isoformat()


def price_day(market: str, when: datetime) -> str:
    """그 시각의 '현재가'가 속한 장 날짜(그 시장 기준): 장중·장 마감 뒤면 그날, 장 시작 전·주말이면 직전 평일.
    판단 기록의 기준일로 씁니다(장 시작 전에 만든 추천의 가격은 전날 종가)."""
    zone, opens, _ = SESSIONS[market.upper()]
    local = when.astimezone(zone)
    day = local.date()
    if not (day.weekday() < 5 and local.time() >= opens):
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day.isoformat()


def is_late(market: str, now: datetime) -> bool:
    zone = SESSIONS[market.upper()][0]
    return now.astimezone(zone).time() >= LATE_FROM[market.upper()]


def review_key(market: str, day: str) -> str:
    return f"vp:ai-review:v1:{market.lower()}:{day}"


async def _candles(api: TossApiClient, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    try:
        return await _recent_candles(api, symbol, start, end)
    except Exception:
        logger.exception("managed live price failed: %s", symbol)
        return []


async def recent_candles(api: TossApiClient, symbols: list[str], today: str) -> dict[str, list[dict[str, Any]]]:
    """판단(20일선·RSI·60일선)에 쓸 만큼의 일봉. 오늘 봉은 장중 지금 가격."""
    start = (datetime.fromisoformat(today) - timedelta(days=MANAGED_LOOKBACK_DAYS)).date().isoformat()
    semaphore = asyncio.Semaphore(2)

    async def one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            return symbol, await _candles(api, symbol, start, today)

    return dict(await asyncio.gather(*(one(symbol) for symbol in symbols)))


async def boot_ledger(api: TossApiClient, portfolio: dict[str, Any], history: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """마지막으로 끝난 장까지 일봉으로 다시 계산한 장부."""
    today = now.date().isoformat()
    start = (datetime.fromisoformat(portfolio["created_date"]) - timedelta(days=MANAGED_LOOKBACK_DAYS)).date().isoformat()
    index_symbol = MARKET_GAUGES[portfolio["market"]][0][0]

    async def fetch(symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {symbol: await _candles(api, symbol, start, today) for symbol in symbols}

    prices = await fetch([pick["symbol"] for pick in portfolio["picks"]] + [index_symbol])
    closed_days = [row["date"] for row in prices.get(index_symbol, []) if session_close(portfolio["market"], row["date"]) <= now]
    last_day = max(closed_days, default=portfolio["created_date"])
    run = await replay_managed(fetch, portfolio, prices, index_symbol, history, last_day)
    if run is None:
        return None
    return {**run["state"], "booted_through": last_day}


def review_positions(entries: list[dict[str, Any]], candles: dict[str, list[dict[str, Any]]], snapshot: dict[str, Any] | None,
                     flows: dict[str, str]) -> list[dict[str, Any]]:
    """AI 점검에 넘길 보유 종목 수치(종목마다 한 번)."""
    info = snapshot_info(snapshot)
    positions, seen = [], set()
    for entry in entries:
        for symbol, item in (entry["ledger"].get("held") or {}).items():
            rows = candles.get(symbol) or []
            if symbol in seen or not rows:
                continue
            seen.add(symbol)
            price = rows[-1]["close"]
            tech = metrics([row["close"] for row in rows]) or {}
            row_info = info.get(symbol, {})
            theme = item.get("theme") or row_info.get("theme") or ""
            positions.append({
                "symbol": symbol, "name": item["name"], "price": price, "gain": round((price / item["buy_price"] - 1) * 100, 1),
                "from_peak": round((price / max(item["peak"], price) - 1) * 100, 1),
                "r5": round(tech["r5"] * 100, 1) if tech.get("r5") is not None else None,
                "r20": round(tech["r20"] * 100, 1) if tech.get("r20") is not None else None,
                "ma20": None if tech.get("above_ma20") is None else ("위" if tech["above_ma20"] else "아래"),
                "rsi": round(tech["rsi"]) if tech.get("rsi") is not None else None,
                "evidence": row_info.get("evidence"), "recommended": bool(row_info) and not (row_info.get("kinds", set()) & RISK_KINDS),
                "risk": bool(row_info.get("kinds", set()) & RISK_KINDS), "theme": theme, "theme_flow": flows.get(theme),
            })
    return positions


async def load_entries(api: TossApiClient, now: datetime, histories: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """운용 중인 자동 운용 포트폴리오와 장부(없으면 만들기)."""
    entries = []
    async for key in redis.scan_iter("vp:v1:*", count=200):
        username = key.split(":", 2)[2]
        for portfolio in await list_portfolios(username):
            managed = portfolio.get("managed")
            if not managed:
                continue
            if not portfolio.get("open_ended") and now.date().isoformat() > portfolio["end_date"]:
                continue  # 운용을 끝낸 포트폴리오
            market = portfolio["market"].lower()
            if market not in histories:
                histories[market] = await get_json(f"rec:history:v1:{market}") or []
            run_id = managed.get("run_id")
            ledger = await get_json(ledger_key(username, portfolio["id"]))
            changed = False
            if not ledger or ledger.get("run_id") != run_id:
                ledger = await boot_ledger(api, portfolio, histories[market], now)
                if ledger is None:
                    continue
                ledger["run_id"], changed = run_id, True
            entries.append({"username": username, "portfolio": portfolio, "ledger": ledger, "run_id": run_id, "changed": changed})
    return entries


async def tick_market(api: TossApiClient, market: str, entries: list[dict[str, Any]], history: list[dict[str, Any]], now: datetime) -> int:
    """한 시장의 운용 중 포트폴리오를 장중 한 번 확인."""
    is_open, day = market_session(market, now)
    if not is_open:
        return 0
    now_text = now.strftime("%Y-%m-%d %H:%M")
    index_symbol = MARKET_GAUGES[market.upper()][0][0]
    symbols = list(dict.fromkeys([symbol for entry in entries for symbol in needed_symbols(entry["ledger"], entry["portfolio"], history, now_text)]
                                 + [order["symbol"] for entry in entries for order in (entry["ledger"].get("plan") or {}).get("orders", [])]
                                 + [index_symbol]))
    candles = await recent_candles(api, symbols, now.date().isoformat())
    quotes = {symbol: rows[-1]["close"] for symbol, rows in candles.items() if rows and rows[-1]["date"] == day and symbol != index_symbol}
    if not quotes:
        return 0  # 오늘 봉이 하나도 없으면 휴장일
    flow_data = await get_json(f"insight:sector-flow:{market.lower()}") or {}
    flows = {sector["name"]: sector.get("flow") for sector in flow_data.get("sectors", []) if sector.get("name")}
    weak = bool(market_is_weak([row["close"] for row in candles.get(index_symbol, [])]))
    ready = [snapshot for snapshot in usable_snapshots(history, "all") if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
    latest = ready[-1] if ready else None
    review = await get_json(review_key(market, day))
    if review is None:
        # 그날 첫 확인: 보유 종목 전체를 AI가 한 번 점검(결과가 비어도 남겨 그날 다시 부르지 않음)
        positions = review_positions(entries, candles, latest, flows)
        review = await ai_review(market.upper(), positions, weak)
        await set_json(review_key(market, day), review, REVIEW_TTL)
        await safe_remember(market, events_from_review(review, positions, now_text, day, weak))
    ctx = Context(weak_market=weak, flows=flows, review=review, late=is_late(market, now),
                  tech=lambda symbol: metrics([row["close"] for row in candles.get(symbol, [])]))
    # 예약 주문 체결용: 오늘 시가·전날 종가, 판단은 전날 종가까지의 흐름으로(예약을 만든 기준과 같게)
    opens = {symbol: {"open": rows[-1].get("open") or rows[-1]["close"], "prev_close": rows[-2]["close"] if len(rows) > 1 else None}
             for symbol, rows in candles.items() if rows and rows[-1]["date"] == day}
    plan_ctx = Context(weak_market=weak, flows=flows, review=review, late=True,
                       tech=lambda symbol: metrics([row["close"] for row in candles.get(symbol, []) if row["date"] < day]))
    new_trades: list[dict[str, Any]] = []
    for entry in entries:
        portfolio = entry["portfolio"]
        trail, rotate = managed_params(portfolio)
        before = len(entry["ledger"].get("trades") or [])
        plan = entry["ledger"].get("plan")
        if plan and not plan.get("executed") and plan.get("for_day", day) <= day:
            entry["ledger"] = {**entry["ledger"], **execute_plan(entry["ledger"], portfolio, opens, history, day, now_text, COST, trail,
                                                                 TRAIL_ARM, rotate, plan_ctx)}
        state = live_tick(entry["ledger"], portfolio, quotes, history, day, now_text, COST, trail, TRAIL_ARM, rotate, ctx)
        entry["ledger"] = {**entry["ledger"], **state, "updated_at": now_text,
                           "context": {"weak_market": weak, "late": ctx.late, "review_count": len(review)}}
        entry["changed"] = True
        new_trades += entry["ledger"]["trades"][before:]
    if new_trades:
        await safe_remember(market, events_from_trades(new_trades, day))
    return len(entries)


async def plan_market(api: TossApiClient, market: str, entries: list[dict[str, Any]], history: list[dict[str, Any]], now: datetime) -> int:
    """장이 닫혀 있을 때: 다음 장 시가 예약 주문을 만듭니다(새 추천이 나오면 다시 만듦). 만든 포트폴리오 수를 돌려줍니다."""
    is_open, _ = market_session(market, now)
    if is_open:
        return 0
    zone, opens_at, _ = SESSIONS[market.upper()]
    for_day = next_session_day(market, now.astimezone(zone), opens_at)
    now_text = now.strftime("%Y-%m-%d %H:%M")

    def latest_id(portfolio: dict[str, Any]) -> str | None:
        ready = [snapshot for snapshot in usable_snapshots(history, portfolio.get("source", "all"))
                 if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
        return snapshot_id(ready[-1]) if ready else None

    todo = []
    for entry in entries:
        if not entry["ledger"].get("started"):
            continue  # 처음 매수는 장이 열리면 지금 가격으로
        plan = entry["ledger"].get("plan") or {}
        if plan and not plan.get("executed") and plan.get("for_day") == for_day and plan.get("based_on") == latest_id(entry["portfolio"]):
            continue  # 이미 최신 추천으로 만든 예약이 있음
        todo.append(entry)
    if not todo:
        return 0
    index_symbol = MARKET_GAUGES[market.upper()][0][0]
    symbols = list(dict.fromkeys([symbol for entry in todo for symbol in needed_symbols(entry["ledger"], entry["portfolio"], history, now_text)]
                                 + [index_symbol]))
    candles = await recent_candles(api, symbols, now.date().isoformat())
    closes = {symbol: rows[-1]["close"] for symbol, rows in candles.items() if rows and symbol != index_symbol}
    flow_data = await get_json(f"insight:sector-flow:{market.lower()}") or {}
    flows = {sector["name"]: sector.get("flow") for sector in flow_data.get("sectors", []) if sector.get("name")}
    weak = bool(market_is_weak([row["close"] for row in candles.get(index_symbol, [])]))
    review = await get_json(review_key(market, for_day))
    if review is None:
        # 다음 장 AI 점검을 장 마감 뒤에 미리(그 결과를 예약 매도에 넣고, 다음 장 첫 확인 때는 다시 부르지 않음)
        ready = [snapshot for snapshot in usable_snapshots(history, "all") if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
        positions = review_positions(entries, candles, ready[-1] if ready else None, flows)
        review = await ai_review(market.upper(), positions, weak)
        await set_json(review_key(market, for_day), review, REVIEW_TTL)
        # 장 마감 뒤 점검이라 가격은 그날 종가: 판단한 날은 마지막 장 날짜로 남깁니다
        last_day = max((rows[-1]["date"] for rows in candles.values() if rows), default=for_day)
        await safe_remember(market, events_from_review(review, positions, now_text, last_day, weak))
    ctx = Context(weak_market=weak, flows=flows, review=review, late=True,
                  tech=lambda symbol: metrics([row["close"] for row in candles.get(symbol, [])]))
    for entry in todo:
        trail, rotate = managed_params(entry["portfolio"])
        entry["ledger"]["plan"] = plan_orders(entry["ledger"], entry["portfolio"], closes, history, for_day, now_text, COST, trail,
                                              TRAIL_ARM, rotate, ctx)
        entry["changed"] = True
    return len(todo)


async def run_once(api: TossApiClient, now: datetime | None = None) -> int:
    """모든 사용자의 자동 운용 포트폴리오를 한 번 확인합니다. 장중 확인한 포트폴리오 수를 돌려줍니다."""
    now = now or datetime.now(KST)
    histories: dict[str, list[dict[str, Any]]] = {}
    entries = await load_entries(api, now, histories)
    checked = 0
    for market in ("kr", "us"):
        group = [entry for entry in entries if entry["portfolio"]["market"].lower() == market]
        if group:
            checked += await tick_market(api, market, group, histories.get(market, []), now)
            planned = await plan_market(api, market, group, histories.get(market, []), now)
            if planned:
                logger.info("managed plan: %s %d portfolios", market, planned)
    for market in ("kr", "us"):
        # 처음 한 번: 이미 쌓인 하루 추천 기록으로 종목별 판단 기록을 채움
        if await redis.set(f"stock:memory:backfilled:v1:{market}", now.strftime("%Y-%m-%d %H:%M"), nx=True):
            history = histories.get(market)
            if history is None:
                history = await get_json(f"rec:history:v1:{market}") or []
            await safe_remember(market, events_from_history(market, history))
        # 종목별 판단 기록 채점: 장이 닫혀 있을 때 하루 한 번(판단 뒤 1·5·20거래일 수익률)
        if market_session(market, now)[0]:
            continue
        if await redis.set(f"stock:memory:graded:{market}:{now.date().isoformat()}", now.strftime("%H:%M"), nx=True, ex=2 * 24 * 3600):
            try:
                graded = await grade_market(api, market, now.date().isoformat())
                logger.info("stock memory graded: %s %d symbols", market, graded)
            except Exception:
                logger.exception("stock memory grade failed: %s", market)
    for entry in entries:
        if not entry["changed"]:
            continue
        # 저장 직전에 다시 확인: 그사이 자동 운용을 끄거나 다시 켰거나 지웠으면 버립니다.
        current = next((item for item in await list_portfolios(entry["username"]) if item["id"] == entry["portfolio"]["id"]), None)
        if current and (current.get("managed") or {}).get("run_id") == entry["run_id"]:
            await redis.set(ledger_key(entry["username"], entry["portfolio"]["id"]), json.dumps(entry["ledger"], ensure_ascii=False))
    return checked


async def managed_live_loop(api: TossApiClient) -> None:
    while True:
        try:
            checked = await run_once(api)
            if checked:
                logger.info("managed live tick: %d portfolios", checked)
        except Exception:
            logger.exception("managed live tick failed")
        await asyncio.sleep(TICK_SECONDS)

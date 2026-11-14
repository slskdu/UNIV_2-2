"""가상 투자.

1) 과거로 가상 투자해 보기(run_portfolio): 시뮬레이션과 같은 규칙(근거 점수 상위 N개)으로 시작 금액을 실제 주수(정수)로 나눠 사고,
   보유 기간이 지나면 팔아 다시 삽니다. 거래 비용은 살 때·팔 때 절반씩 뺍니다. 같은 돈을 지수·표본 평균에 넣었을 때와 비교합니다.
2) 오늘부터 추천대로 가상 투자(포트폴리오): AI분석 탭의 추천 종목을 같은 금액씩, **다음 거래일 시가**에 산 것으로 치고
   종료일 종가까지 매일 평가합니다. 같은 기간 지수와 비교합니다. 로그인한 사용자별로 저장합니다.
"""
import asyncio
import json
import logging
import math
import statistics
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from .briefing import KST, MARKET_GAUGES
from .history import fetch_daily_candles
from .redis_client import get_json, redis, set_json
from .signals import compute_signals
from .simulation import COST, MIN_HISTORY, _candles, build_universe
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

MAX_PORTFOLIOS = 50
NAME_MAX = 40
MAX_PICKS = 5
PRICE_TTL = 5 * 60  # 장중에는 일봉의 오늘 봉이 현재가로 바뀌므로 5분마다 새로 받습니다


def session_close(market: str, day: str) -> datetime:
    """그날 장이 확실히 끝나는 한국 시간. 국장 15:40, 미장은 다음 날 06:30(서머타임이면 05:00, 아니면 06:00 마감)."""
    base = datetime.fromisoformat(day).replace(tzinfo=KST)
    if market.upper() == "KR":
        return base.replace(hour=15, minute=40)
    return (base + timedelta(days=1)).replace(hour=6, minute=30)


# --- 1) 과거로 가상 투자 -------------------------------------------------------------------

def run_portfolio(series: dict[str, list[dict[str, Any]]], names: dict[str, str], capital: float, horizon: int = 20,
                  top: int = 5, start_after: str | None = None, cost: float = COST,
                  benchmark: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """시작 금액으로 리밸런스마다 근거 점수 상위 top개를 정수 주수로 같은 금액씩 사고, horizon거래일 뒤 판 결과."""
    calendar = sorted({candle["date"] for candles in series.values() for candle in candles})
    closes = {symbol: {candle["date"]: candle["close"] for candle in candles if candle.get("close")} for symbol, candles in series.items()}
    ordered = {symbol: [candle for candle in candles if candle.get("close")] for symbol, candles in series.items()}
    bench = {candle["date"]: candle["close"] for candle in (benchmark or []) if candle.get("close")}
    first = MIN_HISTORY
    if start_after:
        first = max(first, next((index for index, day in enumerate(calendar) if day >= start_after), len(calendar)))

    cash, universe_value, index_value = float(capital), float(capital), float(capital)
    periods: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    for start in range(first, len(calendar) - horizon, horizon):
        day, end = calendar[start], calendar[start + horizon]
        rows = []
        for symbol, candles in ordered.items():
            history = [candle for candle in candles if candle["date"] <= day]
            if len(history) < MIN_HISTORY or history[-1]["date"] != day or end not in closes[symbol]:
                continue
            score = compute_signals(history[-260:])["score"]
            if score is not None:
                rows.append({"symbol": symbol, "score": score, "buy": history[-1]["close"], "sell": closes[symbol][end]})
        if len(rows) < top * 3:
            continue
        rows.sort(key=lambda row: (-row["score"], row["symbol"]))
        if not equity:
            equity.append({"date": day, "value": round(cash), "index_value": round(index_value), "universe_value": round(universe_value)})
        budget, start_cash, trades, spent, proceeds = cash / top, cash, [], 0.0, 0.0
        for row in rows[:top]:
            shares = math.floor(budget / (row["buy"] * (1 + cost / 2)))
            if shares <= 0:
                continue  # 1주 값이 배정 금액보다 비싼 종목은 건너뜁니다(현금으로 남김)
            paid = shares * row["buy"] * (1 + cost / 2)
            received = shares * row["sell"] * (1 - cost / 2)
            spent += paid
            proceeds += received
            trades.append({"symbol": row["symbol"], "name": names.get(row["symbol"], row["symbol"]), "score": row["score"],
                           "shares": shares, "buy_price": row["buy"], "sell_price": row["sell"],
                           "profit": round(received - paid), "return": round(received / paid - 1, 4)})
        cash = start_cash - spent + proceeds
        universe_value *= 1 + statistics.fmean(row["sell"] / row["buy"] - 1 for row in rows)
        if day in bench and end in bench and bench[day]:
            index_value *= bench[end] / bench[day]
        periods.append({"date": day, "end": end, "start_value": round(start_cash), "end_value": round(cash),
                        "profit": round(cash - start_cash), "return": round(cash / start_cash - 1, 4),
                        "cash_left": round(start_cash - spent), "trades": trades})
        equity.append({"date": end, "value": round(cash), "index_value": round(index_value), "universe_value": round(universe_value)})

    wins = sum(1 for period in periods if period["profit"] > 0)
    return {
        "capital": round(capital), "final_value": round(cash), "profit": round(cash - capital),
        "return": round(cash / capital - 1, 4) if capital else None,
        "index_final": round(index_value), "index_return": round(index_value / capital - 1, 4) if capital else None,
        "universe_final": round(universe_value), "universe_return": round(universe_value / capital - 1, 4) if capital else None,
        "period_count": len(periods), "winning_periods": wins,
        "start_date": periods[0]["date"] if periods else None, "end_date": periods[-1]["end"] if periods else None,
        "periods": periods, "equity": equity, "cost": cost,
    }


async def virtual_backtest(api: TossApiClient, market: str, capital: float, horizon: int, top: int, months: int) -> dict[str, Any]:
    universe = await build_universe(api, market)
    names = {row["symbol"]: row["name"] for row in universe}
    semaphore = asyncio.Semaphore(2)

    async def load(symbol: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            try:
                return symbol, await _candles(api, symbol)
            except Exception:
                logger.exception("virtual candles failed: %s", symbol)
                return symbol, []

    pairs = await asyncio.gather(*(load(row["symbol"]) for row in universe))
    series = {symbol: candles for symbol, candles in pairs if len(candles) > MIN_HISTORY}
    index_symbol, index_name = MARKET_GAUGES[market.upper()][0]
    benchmark = await _candles(api, index_symbol)
    start_after = (date.today() - timedelta(days=months * 30)).isoformat()
    result = await asyncio.to_thread(run_portfolio, series, names, capital, horizon, top, start_after, COST, benchmark)
    return {**result, "market": market.upper(), "currency": "KRW" if market.lower() == "kr" else "USD",
            "index_name": index_name, "universe_size": len(series), "horizon": horizon, "top": top, "months": months}


# --- 2) 오늘부터 추천대로 가상 투자 ------------------------------------------------------------

def next_tuesday(today: date) -> date:
    """오늘 다음에 오는 화요일(오늘이 화요일이면 다음 주 화요일)."""
    days = (1 - today.weekday()) % 7
    return today + timedelta(days=days or 7)


def _key(username: str) -> str:
    return f"vp:v1:{username}"


async def list_portfolios(username: str) -> list[dict[str, Any]]:
    return await get_json(_key(username)) or []


async def latest_recommendations(market: str) -> dict[str, Any] | None:
    """AI분석 추천(30분 캐시). 캐시가 지났으면 오늘 남긴 추천 기록(app/simulation.record_recommendations)으로 대신합니다."""
    cached = await get_json(f"insight:recommendations:v4:{market.lower()}")
    if cached:
        return cached
    history = await get_json(f"rec:history:v1:{market.lower()}") or []
    today = datetime.now(KST).strftime("%Y-%m-%d")
    snapshot = next((item for item in reversed(history) if item["date"] == today), None)
    if not snapshot:
        return None
    def stock(item: dict[str, Any]) -> dict[str, Any]:
        return {"symbol": item["symbol"], "name": item["name"], "theme": item.get("theme", ""), "evidence": {"score": item.get("evidence")}}
    return {
        "generated_at": snapshot.get("generated_at"),
        "ai_picks": [stock(item) for item in snapshot["items"] if item["kind"] == "ai_pick"],
        "themes": [{"kind": "trend", "stocks": [stock(item) for item in snapshot["items"] if item["kind"] == "pick"]}],
    }


SOURCE_LABELS = {"all": "통합 추천", "ai": "AI 추천 종목", "theme": "추천 테마 종목"}


def pick_recommendations(result: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """AI분석 결과에서 따라 살 종목(최대 MAX_PICKS개).

    ai = AI 추천 종목, theme = 추천 테마 종목 중 근거 점수 높은 순,
    all(통합) = ① AI와 테마 양쪽에서 모두 추천된 종목 → ② 나머지 AI 추천 → ③ 남는 자리를 테마 종목 근거 점수 순으로.
    종목마다 어느 쪽 추천인지(via: ai·theme)를 남깁니다.
    """
    def evidence(stock: dict[str, Any]) -> float:
        return (stock.get("evidence") or {}).get("score") or 0

    ai = list(result.get("ai_picks") or [])
    theme = sorted(({**stock, "theme": stock.get("theme") or group.get("name", "")}
                    for group in result.get("themes", []) if group["kind"] != "risk" for stock in group.get("stocks", [])),
                   key=lambda stock: -evidence(stock))
    ai_symbols, theme_symbols = {stock["symbol"] for stock in ai}, {stock["symbol"] for stock in theme}
    if source == "ai":
        stocks = ai
    elif source == "theme":
        stocks = theme
    elif result.get("combined_picks"):
        stocks = result["combined_picks"]  # AI분석 탭 "통합 추천 종목"과 같은 목록
    else:
        both = sorted((stock for stock in ai if stock["symbol"] in theme_symbols), key=lambda stock: -evidence(stock))
        stocks = both + ai + theme
    picked, seen = [], set()
    for stock in stocks:
        if stock["symbol"] in seen:
            continue
        seen.add(stock["symbol"])
        via = [name for name, symbols in (("ai", ai_symbols), ("theme", theme_symbols)) if stock["symbol"] in symbols]
        picked.append({"symbol": stock["symbol"], "name": stock["name"], "theme": stock.get("theme", ""),
                       "evidence": (stock.get("evidence") or {}).get("score"), "via": via})
        if len(picked) >= MAX_PICKS:
            break
    return picked


def default_name(market: str, source: str, created: date) -> str:
    return f"{'국장' if market.lower() == 'kr' else '미장'} {SOURCE_LABELS.get(source, source)} {created.strftime('%m-%d')}"


def unique_name(name: str, existing: list[dict[str, Any]]) -> str:
    """같은 이름이 있으면 뒤에 (2), (3)…을 붙입니다."""
    taken = {item.get("name") for item in existing}
    if name not in taken:
        return name
    number = 2
    while f"{name} ({number})" in taken:
        number += 1
    return f"{name} ({number})"


def clean_name(name: str | None) -> str:
    return " ".join((name or "").split())[:NAME_MAX]


async def create_portfolio(username: str, market: str, capital_krw: float, source: str, end_date: date,
                           recommendations: dict[str, Any], fx: float | None, group: str | None = None,
                           name: str | None = None) -> dict[str, Any]:
    picks = pick_recommendations(recommendations, source)
    if not picks:
        raise ValueError("따라 살 추천 종목이 없습니다. AI분석 탭에서 추천을 먼저 계산하세요.")
    currency = "KRW" if market.lower() == "kr" else "USD"
    if currency == "USD" and not fx:
        raise ValueError("환율을 불러오지 못해 원화 금액을 달러로 바꿀 수 없습니다.")
    created = datetime.now(KST)
    items = await list_portfolios(username)
    if len(items) >= MAX_PORTFOLIOS:
        raise ValueError(f"가상 포트폴리오는 {MAX_PORTFOLIOS}개까지 만들 수 있습니다. 지난 것을 지워 주세요.")
    portfolio = {
        "id": uuid.uuid4().hex[:12], "market": market.upper(), "currency": currency, "source": source,
        "name": unique_name(clean_name(name) or default_name(market, source, created.date()), items),
        "created_at": created.strftime("%Y-%m-%d %H:%M"), "created_date": created.date().isoformat(), "end_date": end_date.isoformat(),
        "capital_krw": round(capital_krw), "capital": round(capital_krw / fx, 2) if currency == "USD" else round(capital_krw),
        "fx_at_create": fx, "recommended_at": recommendations.get("generated_at"), "picks": picks,
        "group": group,  # "세 가지 모두 비교"로 함께 만든 포트폴리오 묶음
    }
    await redis.set(_key(username), json.dumps(items + [portfolio], ensure_ascii=False))
    return portfolio


async def rename_portfolio(username: str, portfolio_id: str, name: str) -> dict[str, Any]:
    items = await list_portfolios(username)
    target = next((item for item in items if item["id"] == portfolio_id), None)
    if not target:
        raise ValueError("가상 포트폴리오를 찾을 수 없습니다.")
    cleaned = clean_name(name)
    if not cleaned:
        raise ValueError("이름을 입력하세요.")
    target["name"] = unique_name(cleaned, [item for item in items if item["id"] != portfolio_id])
    await redis.set(_key(username), json.dumps(items, ensure_ascii=False))
    return target


async def delete_portfolio(username: str, portfolio_id: str) -> None:
    items = [item for item in await list_portfolios(username) if item["id"] != portfolio_id]
    await redis.set(_key(username), json.dumps(items, ensure_ascii=False))


async def _recent_candles(api: TossApiClient, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    key = f"vp:candles:v1:{symbol}:{start}:{end}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    candles = await fetch_daily_candles(api, symbol, date.fromisoformat(start), date.fromisoformat(end))
    slim = [{"date": candle["date"], "open": candle.get("open"), "close": candle["close"]} for candle in candles if candle.get("close")]
    await set_json(key, slim, PRICE_TTL)
    return slim


def evaluate(portfolio: dict[str, Any], prices: dict[str, list[dict[str, Any]]], index_candles: list[dict[str, Any]],
             today: str, cost: float = COST, now: datetime | None = None) -> dict[str, Any]:
    """가상 포트폴리오 평가. 매수는 만든 날 다음 거래일 시가, 평가는 종료일까지의 가격. 지금 팔면 받을 금액(매도 비용 차감)으로 셉니다.

    장중에는 일봉의 오늘 봉이 현재가라 그 시점 가격으로 평가하고(live), 종료일 장이 끝난 뒤에만 "종료"로 봅니다.
    """
    now = now or datetime.fromisoformat(f"{today}T23:59").replace(tzinfo=KST)
    created, end = portfolio["created_date"], portfolio["end_date"]
    capital = portfolio["capital"]
    budget = capital / len(portfolio["picks"])

    def window(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [candle for candle in candles if created < candle["date"] <= end]

    holdings, cash = [], float(capital)
    for pick in portfolio["picks"]:
        rows = window(prices.get(pick["symbol"], []))
        if not rows:
            holdings.append({**pick, "status": "pending"})
            continue
        buy = rows[0].get("open") or rows[0]["close"]
        shares = math.floor(budget / (buy * (1 + cost / 2)))
        paid = shares * buy * (1 + cost / 2)
        cash -= paid
        last = rows[-1]["close"]
        value = shares * last * (1 - cost / 2)
        holdings.append({**pick, "status": "bought" if shares else "too_expensive", "buy_date": rows[0]["date"], "buy_price": buy,
                         "shares": shares, "last_price": last, "last_date": rows[-1]["date"], "cost": round(paid, 2),
                         "value": round(value, 2), "profit": round(value - paid, 2),
                         "return": round(value / paid - 1, 4) if paid else None})
    bought = [holding for holding in holdings if holding.get("status") == "bought"]
    if not bought:
        return {**portfolio, "status": "pending", "holdings": holdings, "series": [],
                "message": f"{created} 다음 거래일 시가에 사는 것으로 칩니다. 장이 열리면 반영됩니다."}

    # 날짜별 평가액(종가, 매도 비용 차감)
    days = sorted({candle["date"] for holding in bought for candle in window(prices[holding["symbol"]])})
    last_close: dict[str, float] = {}
    by_symbol = {holding["symbol"]: {candle["date"]: candle["close"] for candle in window(prices[holding["symbol"]])} for holding in bought}
    index_rows = window(index_candles)
    index_entry = (index_rows[0].get("open") or index_rows[0]["close"]) if index_rows else None
    index_by_date = {candle["date"]: candle["close"] for candle in index_rows}
    series, index_last = [], None
    for day in days:
        for holding in bought:
            if day in by_symbol[holding["symbol"]]:
                last_close[holding["symbol"]] = by_symbol[holding["symbol"]][day]
        value = cash + sum(holding["shares"] * last_close.get(holding["symbol"], holding["buy_price"]) * (1 - cost / 2) for holding in bought)
        index_last = index_by_date.get(day, index_last)
        series.append({"date": day, "value": round(value, 2),
                       "index_value": round(capital * index_last / index_entry, 2) if index_entry and index_last else None})
    value = series[-1]["value"]
    last_day = max(holding["last_date"] for holding in bought)
    finished = now >= session_close(portfolio["market"], end)
    live = not finished and now < session_close(portfolio["market"], last_day)  # 마지막 봉의 장이 아직 진행 중
    return {
        **portfolio, "status": "finished" if finished else "active", "live": live, "holdings": holdings, "series": series,
        "cash_left": round(cash, 2), "value": value, "profit": round(value - capital, 2), "return": round(value / capital - 1, 4),
        "index_value": series[-1]["index_value"],
        "index_return": round(series[-1]["index_value"] / capital - 1, 4) if series[-1]["index_value"] else None,
        "as_of": last_day,
    }


async def evaluate_portfolios(api: TossApiClient, username: str) -> list[dict[str, Any]]:
    items = await list_portfolios(username)
    today = datetime.now(KST).date()
    results = []
    semaphore = asyncio.Semaphore(2)

    async def candles(symbol: str, start: str, end: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            try:
                return symbol, await _recent_candles(api, symbol, start, end)
            except Exception:
                logger.exception("virtual price failed: %s", symbol)
                return symbol, []

    for portfolio in reversed(items):
        if not portfolio.get("name"):
            portfolio = {**portfolio, "name": default_name(portfolio["market"], portfolio["source"], date.fromisoformat(portfolio["created_date"]))}
        end = min(date.fromisoformat(portfolio["end_date"]) + timedelta(days=1), today)
        start = portfolio["created_date"]
        index_symbol, index_name = MARKET_GAUGES[portfolio["market"]][0]
        pairs = await asyncio.gather(*(candles(pick["symbol"], start, end.isoformat()) for pick in portfolio["picks"]),
                                     candles(index_symbol, start, end.isoformat()))
        prices = dict(pairs)
        result = evaluate(portfolio, prices, prices.get(index_symbol, []), today.isoformat(), now=datetime.now(KST))
        results.append({**result, "index_name": index_name})
    return results

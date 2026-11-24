"""가상 투자.

추천 규칙(11-17 시뮬레이션 비교로 정함): 종목별 비중은 변동성 반비례(한 종목 최대 25%), 보유가 5거래일 이하이면
단기 과열·근거 약한 종목을 빼고, 매도 신호(고점 대비 −10%)를 따르면 그날 종가에 판 것으로 칩니다(선택).

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
from .exit_signals import TRAIL_ARM, TRAIL_DROP, exit_signals
from .managed import book_view, simulate_managed
from .signals import compute_signals
from .simulation import (
    COST,
    MIN_HISTORY,
    _candles,
    build_universe,
    exit_path_return,
    history_metrics,
    market_is_weak,
    passes_rules,
    position_weights,
    WEAK_MARKET_FRACTION,
)
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

MAX_PORTFOLIOS = 50
NAME_MAX = 40
MAX_PICKS = 5  # 통합 추천이 없던 예전 결과에서 쓰는 기본 수
MANAGED_LOOKBACK_DAYS = 120  # 자동 운용: 지수 60거래일 평균선(시장 약세)을 보려면 만든 날보다 이만큼 앞부터 일봉이 필요
MAX_COUNT = 40  # 종목 수를 직접 정할 때(API)의 안전 상한. 화면은 추천이 고른 수를 그대로 씁니다.
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
                  benchmark: list[dict[str, Any]] | None = None, filtered: bool = False, sizing: str = "equal",
                  trail: float | None = None, regime: bool = False) -> dict[str, Any]:
    """시작 금액으로 리밸런스마다 근거 점수 상위 top개를 정수 주수로 사고, horizon거래일 뒤(또는 매도 신호가 난 날) 판 결과.

    filtered: 단기 과열·근거 약한 종목 제외. sizing: equal(같은 금액)·inverse_vol(변동성 반비례). trail: 고점 대비 매도 기준(소수).
    """
    calendar = sorted({candle["date"] for candles in series.values() for candle in candles})
    closes = {symbol: {candle["date"]: candle["close"] for candle in candles if candle.get("close")} for symbol, candles in series.items()}
    ordered = {symbol: [candle for candle in candles if candle.get("close")] for symbol, candles in series.items()}
    bench = {candle["date"]: candle["close"] for candle in (benchmark or []) if candle.get("close")}
    bench_days = sorted(bench)
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
            if score is None:
                continue
            row = {"symbol": symbol, "score": score, "buy": history[-1]["close"], "sell": closes[symbol][end], "sell_date": end,
                   "exit": None, **history_metrics([candle["close"] for candle in history[-61:]])}
            if trail:
                path = [candle for candle in candles if day < candle["date"] <= end]
                _, exit_index, reason = exit_path_return(row["buy"], [candle["close"] for candle in path], None, trail, TRAIL_ARM)
                if exit_index is not None:
                    row.update(sell=path[exit_index]["close"], sell_date=path[exit_index]["date"], exit=reason)
            rows.append(row)
        if len(rows) < top * 3:
            continue
        rows.sort(key=lambda row: (-row["score"], row["symbol"]))
        if not equity:
            equity.append({"date": day, "value": round(cash), "index_value": round(index_value), "universe_value": round(universe_value)})
        eligible = [row for row in rows if passes_rules(row["score"], row["r5"], row["r20"])] if filtered else rows
        picks = eligible[:top]
        weights = [weight * len(picks) / top for weight in position_weights([row["volatility"] for row in picks], sizing)]
        weak = bool(regime and market_is_weak([bench[known] for known in bench_days if known <= day][-60:]))
        if weak:
            weights = [weight * WEAK_MARKET_FRACTION for weight in weights]  # 약세장: 절반만 투자
        start_cash, trades, spent, proceeds = cash, [], 0.0, 0.0
        for weight, row in zip(weights, picks):
            shares = math.floor(start_cash * weight / (row["buy"] * (1 + cost / 2)))
            if shares <= 0:
                continue  # 1주 값이 배정 금액보다 비싼 종목은 건너뜁니다(현금으로 남김)
            paid = shares * row["buy"] * (1 + cost / 2)
            received = shares * row["sell"] * (1 - cost / 2)
            spent += paid
            proceeds += received
            trades.append({"symbol": row["symbol"], "name": names.get(row["symbol"], row["symbol"]), "score": row["score"],
                           "shares": shares, "weight": round(weight, 3), "buy_price": row["buy"], "sell_price": row["sell"],
                           "sell_date": row["sell_date"], "exit": row["exit"],
                           "profit": round(received - paid), "return": round(received / paid - 1, 4)})
        cash = start_cash - spent + proceeds
        universe_value *= 1 + statistics.fmean(row["sell"] / row["buy"] - 1 for row in rows)
        if day in bench and end in bench and bench[day]:
            index_value *= bench[end] / bench[day]
        periods.append({"date": day, "end": end, "start_value": round(start_cash), "end_value": round(cash),
                        "profit": round(cash - start_cash), "return": round(cash / start_cash - 1, 4),
                        "cash_left": round(start_cash - spent), "weak_market": weak, "trades": trades})
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
        "rules": {"filtered": filtered, "sizing": sizing, "trail": trail, "regime": regime},
        "weak_periods": sum(1 for period in periods if period["weak_market"]),
        "early_exits": sum(1 for period in periods for trade in period["trades"] if trade["exit"]),
    }


async def virtual_backtest(api: TossApiClient, market: str, capital: float, horizon: int, top: int, months: int,
                          follow_signals: bool = True) -> dict[str, Any]:
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
    # (가): 보유 5거래일 이하만 급등 종목 거르기, 비중 조절은 항상, 매도 신호는 선택
    result = await asyncio.to_thread(run_portfolio, series, names, capital, horizon, top, start_after, COST, benchmark,
                                     horizon <= 5, "inverse_vol", TRAIL_DROP if follow_signals else None, horizon <= 5)
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


def natural_count(result: dict[str, Any], source: str) -> int:
    """추천이 정한 그대로의 종목 수: ai = AI가 고른 수, theme = 추천 테마 종목 수, all = 통합 추천 수(AI 추천을 모두 담음)."""
    ai = result.get("ai_picks") or []
    theme = [stock for group in result.get("themes", []) if group["kind"] != "risk" for stock in group.get("stocks", [])]
    if source == "ai":
        return len(ai)
    if source == "theme":
        return len({stock["symbol"] for stock in theme})
    return len(result.get("combined_picks") or []) or max(MAX_PICKS, len(ai))


def pick_recommendations(result: dict[str, Any], source: str, limit: int | None = None) -> list[dict[str, Any]]:
    """AI분석 결과에서 따라 살 종목(limit이 없으면 추천이 정한 수만큼, natural_count).

    ai = AI 추천 종목, theme = 추천 테마 종목 중 근거 점수 높은 순,
    all(통합) = ① AI와 테마 양쪽에서 모두 추천된 종목 → ② 나머지 AI 추천 → ③ 남는 자리를 테마 종목 근거 점수 순으로.
    종목마다 어느 쪽 추천인지(via: ai·theme)를 남깁니다.
    """
    limit = limit or natural_count(result, source)
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
    else:
        both = sorted((stock for stock in ai if stock["symbol"] in theme_symbols), key=lambda stock: -evidence(stock))
        # AI분석 탭 "통합 추천 종목"(상위 5개)을 먼저, 종목 수를 더 늘리면 같은 규칙(양쪽 추천 → AI → 테마 근거 순)으로 이어 붙입니다.
        stocks = list(result.get("combined_picks") or []) + both + ai + theme
    picked, seen = [], set()
    for stock in stocks:
        if stock["symbol"] in seen:
            continue
        seen.add(stock["symbol"])
        via = [name for name, symbols in (("ai", ai_symbols), ("theme", theme_symbols)) if stock["symbol"] in symbols]
        rates = stock.get("rates") or {}
        picked.append({"symbol": stock["symbol"], "name": stock["name"], "theme": stock.get("theme", ""),
                       "evidence": (stock.get("evidence") or {}).get("score"), "via": via,
                       "r5": rates["5d"] / 100 if rates.get("5d") is not None else None,
                       "r20": rates["20d"] / 100 if rates.get("20d") is not None else None,
                       "volatility": stock.get("volatility")})
        if len(picked) >= limit:
            break
    return picked


def trading_days(after: date, until: date) -> int:
    """after 다음 날부터 until까지의 평일 수(휴장일은 따지지 않음)."""
    return sum(1 for offset in range(1, (until - after).days + 1) if (after + timedelta(days=offset)).weekday() < 5)


SHORT_HOLD_DAYS = 5


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
                           name: str | None = None, auto_sell: bool = False, weak_market: bool | None = None,
                           count: int | None = None, managed: bool = False, rotate: bool = False) -> dict[str, Any]:
    # 종목 수는 정하지 않으면(None) 추천이 고른 그대로(AI가 고른 수 등)입니다.
    requested = count
    count = max(1, min(MAX_COUNT, count)) if count else natural_count(recommendations, source)
    candidates = pick_recommendations(recommendations, source, limit=max(count, 1) * 2)
    if not candidates:
        raise ValueError("따라 살 추천 종목이 없습니다. AI분석 탭에서 추천을 먼저 계산하세요.")
    today = datetime.now(KST).date()
    # 자동 운용은 종료일 없이 계속 들고 가므로 단기 보유 규칙(과열 제외·약세장 절반)을 쓰지 않습니다.
    short = not managed and trading_days(today, end_date) <= SHORT_HOLD_DAYS
    excluded: list[dict[str, Any]] = []
    if short:
        # (가) 5거래일 이하로 짧게 들고 있을 때만, 단기 과열·근거 약한 종목을 빼고 다음 후보로 채웁니다.
        kept = []
        for pick in candidates:
            if pick["evidence"] is None or passes_rules(pick["evidence"], pick["r5"], pick["r20"]):
                kept.append(pick)
            else:
                reason = "근거 점수 55점 미만" if pick["evidence"] < 55 else "단기 과열(20일 +30% 또는 5일 +20% 초과)"
                excluded.append({"symbol": pick["symbol"], "name": pick["name"], "reason": reason})
        candidates = kept
    picks = candidates[:count]
    if not picks:
        raise ValueError("규칙(단기 과열·근거 약한 종목 제외)을 통과한 추천 종목이 없습니다. 종료일을 더 뒤로 잡아 보세요.")
    # 단기 보유인데 지수가 60일 평균선 아래(약세장)면 절반만 투자하고 나머지는 현금으로 둡니다.
    scale = WEAK_MARKET_FRACTION if short and weak_market else 1.0
    for pick, weight in zip(picks, position_weights([pick.get("volatility") for pick in picks], "inverse_vol")):
        pick["weight"] = round(weight * scale, 4)
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
        "rules": {"sizing": "inverse_vol", "filtered": short, "auto_sell": auto_sell or managed, "trail": TRAIL_DROP,
                  "weak_market": bool(short and weak_market), "invest_fraction": scale},
        "excluded": excluded,
        "requested_count": requested,  # None = 추천이 고른 수를 따름(자동 운용도 새 추천의 종목 수를 따름)
    }
    if managed:
        portfolio.update(managed={"rotate": rotate, "since": created.date().isoformat(), "run_id": uuid.uuid4().hex[:12]}, open_ended=True)
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


MAX_END_DAYS = 180


def check_end_date(created_date: str, end_date: date, today: date) -> None:
    """종료일 바꾸기 검사: 만든 날 뒤여야 하고, 오늘부터 MAX_END_DAYS일 안이어야 합니다(지난 날짜로 앞당기는 것도 허용)."""
    if end_date <= date.fromisoformat(created_date):
        raise ValueError("종료일은 만든 날 뒤로 정하세요.")
    if (end_date - today).days > MAX_END_DAYS:
        raise ValueError(f"종료일은 오늘부터 {MAX_END_DAYS}일 안으로 정하세요.")


async def change_end_date(username: str, portfolio_id: str, end_date: date) -> list[dict[str, Any]]:
    """만든 뒤에 종료일을 바꿉니다(더 오래 들고 있었다면 / 일찍 팔았다면). '세 가지 비교'로 함께 만든 묶음은 모두 같이 바꿉니다.
    처음 정한 종료일은 original_end_date로 남깁니다. 종목·비중·규칙(만들 때 정한 단기 보유 규칙 포함)은 그대로입니다."""
    items = await list_portfolios(username)
    target = next((item for item in items if item["id"] == portfolio_id), None)
    if not target:
        raise LookupError("가상 포트폴리오를 찾을 수 없습니다.")
    check_end_date(target["created_date"], end_date, datetime.now(KST).date())
    group = target.get("group")
    changed = [item for item in items if item["id"] == portfolio_id or (group and item.get("group") == group)]
    for item in changed:
        item.setdefault("original_end_date", item["end_date"])
        item["end_date"] = end_date.isoformat()
        item["open_ended"] = False  # 종료일을 정하면 자동 운용도 그날로 끝냄
    await redis.set(_key(username), json.dumps(items, ensure_ascii=False))
    return changed


async def set_managed(username: str, portfolio_id: str, enabled: bool, rotate: bool = False) -> dict[str, Any]:
    """자동 운용 켜기/끄기. 켜면 만든 날부터 이 규칙이었던 것으로 다시 계산하고 종료일 없이 계속 이어 갑니다.
    끄면 원래 방식(처음 산 종목 그대로, 정한 종료일까지)으로 돌아갑니다."""
    items = await list_portfolios(username)
    target = next((item for item in items if item["id"] == portfolio_id), None)
    if not target:
        raise LookupError("가상 포트폴리오를 찾을 수 없습니다.")
    group = target.get("group")
    for item in items:
        if item["id"] != portfolio_id and not (group and item.get("group") == group):
            continue  # '세 가지 비교' 묶음은 함께 켜고 끕니다
        # 켜거나 끌 때마다 장중 매매 장부를 새로 시작합니다(run_id가 바뀌면 예전 장부는 쓰지 않음).
        await redis.delete(ledger_key(username, item["id"]))
        if enabled:
            item["managed"] = {"rotate": rotate, "since": datetime.now(KST).date().isoformat(), "run_id": uuid.uuid4().hex[:12]}
            item["open_ended"] = True
            item.setdefault("original_end_date", item["end_date"])
        else:
            item["managed"] = None
            item["open_ended"] = False
    await redis.set(_key(username), json.dumps(items, ensure_ascii=False))
    return target


async def delete_portfolio(username: str, portfolio_id: str) -> None:
    items = [item for item in await list_portfolios(username) if item["id"] != portfolio_id]
    await redis.set(_key(username), json.dumps(items, ensure_ascii=False))
    await redis.delete(ledger_key(username, portfolio_id))


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

    - 종목별 비중(weight)이 있으면 그 비율로, 없으면(예전 포트폴리오) 같은 금액으로 삽니다.
    - auto_sell이면 매도 신호(고점 대비 −10%)가 난 날 종가에 판 것으로 치고, 그 뒤에는 현금으로 둡니다.
    - 아직 들고 있는 종목마다 매도 신호(app/exit_signals.py)를 붙입니다.
    - 장중에는 일봉의 오늘 봉이 현재가라 그 시점 가격으로 평가하고(live), 종료일 장이 끝난 뒤에만 "종료"로 봅니다.
    """
    now = now or datetime.fromisoformat(f"{today}T23:59").replace(tzinfo=KST)
    created, end = portfolio["created_date"], portfolio["end_date"]
    capital = portfolio["capital"]
    rules = portfolio.get("rules") or {}
    auto_sell = bool(rules.get("auto_sell"))
    trail = rules.get("trail") or TRAIL_DROP
    picks = portfolio["picks"]

    def window(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [candle for candle in candles if created < candle["date"] <= end]

    holdings, cash = [], float(capital)
    for pick in picks:
        rows = window(prices.get(pick["symbol"], []))
        if not rows:
            holdings.append({**pick, "status": "pending"})
            continue
        budget = capital * pick.get("weight", 1 / len(picks))
        buy = rows[0].get("open") or rows[0]["close"]
        shares = math.floor(budget / (buy * (1 + cost / 2)))
        paid = shares * buy * (1 + cost / 2)
        cash -= paid
        sold_index = None
        if auto_sell and shares:
            _, sold_index, _ = exit_path_return(buy, [candle["close"] for candle in rows], None, trail, TRAIL_ARM)
        last_row = rows[sold_index] if sold_index is not None else rows[-1]
        last = last_row["close"]
        value = shares * last * (1 - cost / 2)
        holding = {**pick, "status": ("sold" if sold_index is not None else "bought") if shares else "too_expensive",
                   "buy_date": rows[0]["date"], "buy_price": buy, "shares": shares, "last_price": last,
                   "last_date": last_row["date"], "cost": round(paid, 2), "value": round(value, 2),
                   "profit": round(value - paid, 2), "return": round(value / paid - 1, 4) if paid else None}
        if sold_index is not None:
            holding.update(sold_date=last_row["date"], sold_price=last, exit_reason="고점 대비 하락")
        elif shares:
            history = [candle for candle in prices.get(pick["symbol"], []) if candle["date"] <= end]
            holding["signal"] = exit_signals(history, buy, rows[0]["date"])
        holdings.append(holding)
    owned = [holding for holding in holdings if holding.get("status") in {"bought", "sold"}]
    if not owned:
        return {**portfolio, "status": "pending", "holdings": holdings, "series": [],
                "message": f"{created} 다음 거래일 시가에 사는 것으로 칩니다. 장이 열리면 반영됩니다."}

    # 날짜별 평가액(종가, 매도 비용 차감). 판 종목은 판 날부터 그 금액으로 고정합니다.
    days = sorted({candle["date"] for holding in owned for candle in window(prices[holding["symbol"]])})
    by_symbol = {holding["symbol"]: {candle["date"]: candle["close"] for candle in window(prices[holding["symbol"]])} for holding in owned}
    index_rows = window(index_candles)
    index_entry = (index_rows[0].get("open") or index_rows[0]["close"]) if index_rows else None
    index_by_date = {candle["date"]: candle["close"] for candle in index_rows}
    series, index_last, last_close = [], None, {}
    for day in days:
        total = cash
        for holding in owned:
            symbol = holding["symbol"]
            if holding["status"] == "sold" and day >= holding["sold_date"]:
                total += holding["value"]
                continue
            if day in by_symbol[symbol]:
                last_close[symbol] = by_symbol[symbol][day]
            total += holding["shares"] * last_close.get(symbol, holding["buy_price"]) * (1 - cost / 2)
        index_last = index_by_date.get(day, index_last)
        series.append({"date": day, "value": round(total, 2),
                       "index_value": round(capital * index_last / index_entry, 2) if index_entry and index_last else None})
    value = series[-1]["value"]
    finished = now >= session_close(portfolio["market"], end)
    live = not finished and now < session_close(portfolio["market"], days[-1])  # 마지막 봉의 장이 아직 진행 중
    signal_counts = {level: sum(1 for holding in owned if (holding.get("signal") or {}).get("level") == level) for level in ("sell", "watch")}
    return {
        **portfolio, "status": "finished" if finished else "active", "live": live, "holdings": holdings, "series": series,
        "cash_left": round(cash, 2), "value": value, "profit": round(value - capital, 2), "return": round(value / capital - 1, 4),
        "index_value": series[-1]["index_value"],
        "index_return": round(series[-1]["index_value"] / capital - 1, 4) if series[-1]["index_value"] else None,
        "as_of": days[-1], "signal_counts": signal_counts,
        "sold_count": sum(1 for holding in owned if holding["status"] == "sold"),
    }


def ledger_key(username: str, portfolio_id: str) -> str:
    """자동 운용 장중 매매 장부(app/managed_live.py가 장중 5분마다 갱신)."""
    return f"vp:ledger:v1:{username}:{portfolio_id}"


def managed_params(portfolio: dict[str, Any]) -> tuple[float, bool]:
    return (portfolio.get("rules") or {}).get("trail") or TRAIL_DROP, bool((portfolio.get("managed") or {}).get("rotate"))


async def replay_managed(fetch, portfolio: dict[str, Any], prices: dict[str, list[dict[str, Any]]], index_symbol: str,
                         history: list[dict[str, Any]], last_day: str) -> dict[str, Any] | None:
    """일봉으로 다시 계산(새로 살 종목의 가격이 필요하면 fetch(symbols)로 받아 prices에 넣고 다시). 실패하면 None."""
    trail, rotate = managed_params(portfolio)
    for _ in range(30):
        run = simulate_managed(portfolio, prices, prices.get(index_symbol, []), history, last_day, COST, trail, TRAIL_ARM, rotate, decide=True)
        if not run.get("missing"):
            return run
        prices.update(await fetch(run["missing"]))
    logger.warning("managed portfolio %s: too many price rounds", portfolio["id"])
    return None


def evaluate_managed(portfolio: dict[str, Any], prices: dict[str, list[dict[str, Any]]], index_candles: list[dict[str, Any]],
                     snapshots: list[dict[str, Any]], end: str, cost: float = COST, now: datetime | None = None,
                     ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """자동 운용 포트폴리오 평가(app/managed.py). 결과 모양은 evaluate와 같고 매매 내역(trades)이 더 붙습니다.
    장중 매매 장부(ledger)가 있으면 그것으로, 없으면 일봉으로 다시 계산합니다. prices에 없는 종목이 필요하면 {"missing": [...]}."""
    if ledger is not None:
        run = book_view(ledger, cost, prices, end)
    else:
        trail, rotate = managed_params(portfolio)
        run = simulate_managed(portfolio, prices, index_candles, snapshots, end, cost, trail, TRAIL_ARM, rotate, decide=True)
        if run.get("missing"):
            return run
    capital = portfolio["capital"]
    if not run["series"]:
        message = ("장이 열려 있으면 서버가 5분 안에 지금 가격으로 삽니다. 장이 닫혀 있으면 다음 장이 열릴 때 삽니다."
                   if portfolio.get("open_ended") else f"{portfolio['created_date']} 다음 거래일 시가에 사는 것으로 칩니다. 장이 열리면 반영됩니다.")
        return {**portfolio, "status": "pending", "holdings": [{**pick, "status": "pending"} for pick in portfolio["picks"]], "series": [],
                "trades": [], "message": message}
    holdings = run["holdings"]
    for holding in holdings:
        history = [candle for candle in prices.get(holding["symbol"], []) if candle["date"] <= end]
        holding["signal"] = exit_signals(history, holding["buy_price"], holding["buy_date"])
    rows = [candle for candle in index_candles if portfolio["created_date"] < candle["date"] <= end]
    index_entry = (rows[0].get("open") or rows[0]["close"]) if rows else None
    index_by_date, index_last, series = {candle["date"]: candle["close"] for candle in rows}, None, []
    for point in run["series"]:
        index_last = index_by_date.get(point["date"], index_last)
        series.append({**point, "index_value": round(capital * index_last / index_entry, 2) if index_entry and index_last else None})
    value, days = series[-1]["value"], [point["date"] for point in series]
    now = now or datetime.now(KST)
    finished = not portfolio.get("open_ended") and now >= session_close(portfolio["market"], portfolio["end_date"])
    live = not finished and now < session_close(portfolio["market"], days[-1])
    signal_counts = {level: sum(1 for holding in holdings if holding["signal"].get("level") == level) for level in ("sell", "watch")}
    return {
        **portfolio, "status": "finished" if finished else "active", "live": live, "holdings": holdings + run["closed"],
        "series": series, "trades": run["trades"], "cash_left": run["cash"], "waiting_cash": run["pending_cash"],
        "reserve_cash": run.get("reserve_cash", 0), "plan": run.get("plan"),
        "value": value, "profit": round(value - capital, 2), "return": round(value / capital - 1, 4),
        "index_value": series[-1]["index_value"],
        "index_return": round(series[-1]["index_value"] / capital - 1, 4) if series[-1]["index_value"] else None,
        "as_of": days[-1], "signal_counts": signal_counts, "sold_count": len(run["closed"]), "used_snapshot": run["used_snapshot"],
        "intraday": ledger is not None, "ledger_updated_at": (ledger or {}).get("updated_at"),
    }


async def evaluate_portfolios(api: TossApiClient, username: str) -> list[dict[str, Any]]:
    items = await list_portfolios(username)
    today = datetime.now(KST).date()
    results = []
    semaphore = asyncio.Semaphore(2)
    history_cache: dict[str, list[dict[str, Any]]] = {}

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
        end = today if portfolio.get("open_ended") else min(date.fromisoformat(portfolio["end_date"]) + timedelta(days=1), today)
        # 매도 신호(20일 평균선·RSI)를 계산하려고 산 날보다 60일 앞부터 받습니다.
        # 자동 운용은 시장 약세(지수 60거래일 평균선)도 보므로 120일 앞부터.
        lookback = MANAGED_LOOKBACK_DAYS if portfolio.get("managed") else 60
        start = (date.fromisoformat(portfolio["created_date"]) - timedelta(days=lookback)).isoformat()
        index_symbol, index_name = MARKET_GAUGES[portfolio["market"]][0]
        pairs = await asyncio.gather(*(candles(pick["symbol"], start, end.isoformat()) for pick in portfolio["picks"]),
                                     candles(index_symbol, start, end.isoformat()))
        prices = dict(pairs)
        if portfolio.get("managed"):
            market = portfolio["market"].lower()
            if market not in history_cache:
                history_cache[market] = await get_json(f"rec:history:v1:{market}") or []
            last_day = end.isoformat() if portfolio.get("open_ended") else min(portfolio["end_date"], today.isoformat())

            async def fetch(symbols: list[str], start: str = start, end: date = end) -> dict[str, list[dict[str, Any]]]:
                return dict(await asyncio.gather(*(candles(symbol, start, end.isoformat()) for symbol in symbols)))

            ledger = await get_json(ledger_key(username, portfolio["id"]))
            if ledger and ledger.get("run_id") == portfolio["managed"].get("run_id"):
                # 장중 매매 장부: 지금 가진 종목의 가격만 더 받으면 됩니다.
                prices.update(await fetch([symbol for symbol in (ledger.get("held") or {}) if symbol not in prices]))
                result = evaluate_managed(portfolio, prices, prices.get(index_symbol, []), [], last_day, ledger=ledger)
            else:
                run_ok = await replay_managed(fetch, portfolio, prices, index_symbol, history_cache[market], last_day)
                result = (evaluate_managed(portfolio, prices, prices.get(index_symbol, []), history_cache[market], last_day)
                          if run_ok else {**portfolio, "status": "pending", "holdings": [], "series": [], "trades": [],
                                          "message": "가격을 불러오지 못했습니다. 잠시 뒤 다시 보세요."})
        else:
            result = evaluate(portfolio, prices, prices.get(index_symbol, []), today.isoformat(), now=datetime.now(KST))
        results.append({**result, "index_name": index_name})
    return results

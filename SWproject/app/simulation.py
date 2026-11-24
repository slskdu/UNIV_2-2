"""추천 성과 검증.

1) 시뮬레이션(과거 데이터 백테스트): 시장마다 분야별 시가총액 상위 종목을 모은 표본(약 60개)으로, 과거 리밸런스 날짜마다
   **그날까지의 자료만으로** 근거 점수(app/signals.py의 기술 지표 근거)를 매겨 상위 N개(추천)·하위 N개(위험)를 고르고,
   그 뒤 H거래일 수익률을 표본 전체 평균·시장 지수와 비교합니다. 점수 상·중·하위 1/3의 평균 수익률도 비교해
   점수가 실제로 앞날 수익과 관계가 있었는지 봅니다.
   한계: 과거의 기사·수급 자료가 없어 기술 지표 근거만 씁니다. 표본은 지금 상장된 종목이라 생존 편향이 있습니다.
   거래 비용은 리밸런스마다 왕복 0.3%를 추천 쪽에만 뺍니다.
2) 실제 추천 기록 추적: AI분석 탭이 만든 추천·위험 종목과 그때 가격을 날마다 남기고, 지금 가격과 비교합니다.
"""
import asyncio
import json
import logging
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from .briefing import KST, MARKET_GAUGES
from .history import fetch_daily_candles
from .redis_client import get_json, redis, set_json
from .sectors import SECTOR_SPACING_SECONDS, _sector_call_lock, get_sector_flow
from .signals import compute_signals
from .toss_api import TossApiClient, TossApiError

logger = logging.getLogger(__name__)

UNIVERSE_SIZE = 60
PER_SECTOR = 3
HISTORY_DAYS = 560  # 달력 기준. 약 380거래일: 근거 계산용 130일 + 검증 기간 약 1년
MIN_HISTORY = 130  # 근거 점수를 매기려면 필요한 과거 거래일 수
COST = 0.003  # 리밸런스마다 왕복 거래 비용(수수료·세금·호가 차이 추정)
RESULT_TTL = 12 * 60 * 60
CANDLE_CONCURRENCY = 2


# --- 추천 규칙(실제 추천·가상 투자·시뮬레이션이 함께 씀) ------------------------------------------

OVERHEAT_20D = 0.30  # 20일 +30% 넘게 오른 종목은 단기 과열로 보고 추천에서 뺍니다
OVERHEAT_5D = 0.20  # 5일 +20% 넘게 오른 종목도 같습니다
MIN_SCORE = 55  # 근거 점수가 이보다 낮으면 추천하지 않습니다(50이 중립)
WEIGHT_CAP = 0.25  # 한 종목에 넣는 최대 비중(종목이 4개 미만이면 1/종목 수)
WEAK_MARKET_FRACTION = 0.5  # 약세장(지수가 60일 평균선 아래)이면 단기(5거래일 이하) 보유는 이만큼만 투자
SHORT_HOLD = 5


def market_is_weak(index_closes: list[float]) -> bool | None:
    """지수 종가(과거→최신)가 60일 평균선 아래면 약세장. 자료가 모자라면 None."""
    closes = [value for value in index_closes if value]
    if len(closes) < 60:
        return None
    return closes[-1] < sum(closes[-60:]) / 60


def history_metrics(closes: list[float]) -> dict[str, float | None]:
    """5·20일 수익률(소수)과 최근 20일 하루 변동의 표준편차(%)."""
    def change(days: int) -> float | None:
        return closes[-1] / closes[-1 - days] - 1 if len(closes) > days and closes[-1 - days] else None

    daily = [(current / previous - 1) * 100 for previous, current in zip(closes[-21:-1], closes[-20:]) if previous]
    volatility = statistics.pstdev(daily) if len(daily) >= 10 else None
    return {"r5": change(5), "r20": change(20), "volatility": volatility}


def is_overheated(r5: float | None, r20: float | None) -> bool:
    return (r20 is not None and r20 > OVERHEAT_20D) or (r5 is not None and r5 > OVERHEAT_5D)


def passes_rules(score: float | None, r5: float | None, r20: float | None, min_score: float = MIN_SCORE) -> bool:
    return score is not None and score >= min_score and not is_overheated(r5, r20)


def position_weights(volatilities: list[float | None], sizing: str = "equal", cap: float = WEIGHT_CAP) -> list[float]:
    """종목별 투자 비중(합 1). equal = 같은 금액, inverse_vol = 변동성이 작을수록 많이(한 종목 최대 cap)."""
    count = len(volatilities)
    if not count:
        return []
    if sizing != "inverse_vol":
        return [1 / count] * count
    limit = max(cap, 1 / count)
    known = [value for value in volatilities if value]
    fallback = statistics.fmean(known) if known else 1.0
    raw = [1 / (value or fallback) for value in volatilities]
    weights = [value / sum(raw) for value in raw]
    for _ in range(count):  # 상한을 넘는 비중은 잘라 나머지 종목에 나눠 줍니다
        over = [index for index, weight in enumerate(weights) if weight > limit + 1e-9]
        if not over:
            break
        spare = sum(weights[index] - limit for index in over)
        for index in over:
            weights[index] = limit
        room = [index for index, weight in enumerate(weights) if weight < limit - 1e-9]
        room_total = sum(weights[index] for index in room)
        for index in room:
            weights[index] += spare * weights[index] / room_total if room_total else spare / len(room)
    return weights


def exit_path_return(entry: float, closes: list[float], stop: float | None = None, trail: float | None = None,
                     arm: float = 0.05) -> tuple[float, int | None, str | None]:
    """보유 기간 동안의 종가(closes)를 하루씩 보며 매도 신호가 나오면 그날 종가에 팝니다.

    stop: 산 가격 대비 이만큼(소수) 떨어지면 손절. trail: 산 뒤 최고 종가가 산 가격보다 arm 이상 오른 다음,
    최고 종가 대비 이만큼 떨어지면 판매(이익 지키기). 돌려주는 값: (수익률, 판 날 위치 또는 None, 이유).
    """
    peak = entry
    for index, close in enumerate(closes):
        peak = max(peak, close)
        if stop is not None and close / entry - 1 <= -stop:
            return close / entry - 1, index, "stop"
        if trail is not None and peak >= entry * (1 + arm) and close / peak - 1 <= -trail:
            return close / entry - 1, index, "trail"
    return (closes[-1] / entry - 1 if closes else 0.0), None, None


# --- 순수 계산(테스트 가능) -----------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def run_backtest(series: dict[str, list[dict[str, Any]]], names: dict[str, str], horizon: int = 20, top: int = 5,
                 step: int | None = None, benchmark: list[dict[str, Any]] | None = None,
                 filtered: bool = False, sizing: str = "equal", exits: dict[str, float] | None = None,
                 regime: bool = False) -> dict[str, Any]:
    """series: {symbol: 일봉(과거→최신, date·close·volume)}. 리밸런스마다 근거 점수 상위 top개를 추천, 하위 top개를 위험으로.

    filtered=True면 단기 과열(20일 +30%·5일 +20% 초과)·근거 점수 MIN_SCORE 미만 종목을 추천에서 빼고(모자라면 그만큼 현금),
    sizing="inverse_vol"이면 변동성이 작은 종목에 더 많이(한 종목 최대 WEIGHT_CAP) 넣습니다.
    exits={"stop": 0.08, "trail": 0.10}이면 보유 중 매도 신호(손절·고점 대비 하락)가 나온 날 종가에 팔고 나머지 기간은 현금.
    regime=True면 그날 지수가 60일 평균선 아래(약세장)일 때 WEAK_MARKET_FRACTION만 투자하고 나머지는 현금.
    표본 평균·위험 종목 수익률은 비교 기준이라 매도 신호를 적용하지 않습니다. 수익률은 소수(0.05 = 5%)입니다.
    """
    step = step or horizon
    calendar = sorted({candle["date"] for candles in series.values() for candle in candles})
    index_of = {day: position for position, day in enumerate(calendar)}
    by_symbol = {
        symbol: {candle["date"]: candle for candle in candles if candle.get("close")}
        for symbol, candles in series.items()
    }
    ordered = {symbol: [candle for candle in candles if candle.get("close")] for symbol, candles in series.items()}
    bench = {candle["date"]: candle["close"] for candle in (benchmark or []) if candle.get("close")}
    bench_days = sorted(bench)

    periods = []
    buckets: dict[str, list[float]] = {"high": [], "middle": [], "low": []}
    for start in range(MIN_HISTORY, len(calendar) - horizon, step):
        day, end = calendar[start], calendar[start + horizon]
        rows = []
        for symbol, candles in ordered.items():
            history = [candle for candle in candles if candle["date"] <= day]
            if len(history) < MIN_HISTORY or history[-1]["date"] != day or end not in by_symbol[symbol]:
                continue
            score = compute_signals(history[-260:])["score"]
            if score is None:
                continue
            forward = by_symbol[symbol][end]["close"] / history[-1]["close"] - 1
            metrics = history_metrics([candle["close"] for candle in history[-61:]])
            row = {"symbol": symbol, "name": names.get(symbol, symbol), "score": score, "return": forward, "exit": None, **metrics}
            if exits:
                path_closes = [candle["close"] for candle in candles if day < candle["date"] <= end]
                row["held_return"], _, row["exit"] = exit_path_return(history[-1]["close"], path_closes, exits.get("stop"), exits.get("trail"))
            rows.append(row)
        if len(rows) < top * 3:
            continue
        rows.sort(key=lambda row: (-row["score"], row["symbol"]))  # 점수만으로 줄 세움(앞날 수익률은 보지 않음)
        eligible = [row for row in rows if passes_rules(row["score"], row["r5"], row["r20"])] if filtered else rows
        picks, avoid = eligible[:top], rows[-top:]
        third = len(rows) // 3
        buckets["high"].extend(row["return"] for row in rows[:third])
        buckets["middle"].extend(row["return"] for row in rows[third:-third])
        buckets["low"].extend(row["return"] for row in rows[-third:])
        # 거르고 남은 종목이 top개보다 적으면 빈자리는 현금(수익 0)으로 둡니다.
        weights = [weight * len(picks) / top for weight in position_weights([row["volatility"] for row in picks], sizing)]
        weak = bool(regime and market_is_weak([bench[known] for known in bench_days if known <= day][-60:]))
        if weak:
            weights = [weight * WEAK_MARKET_FRACTION for weight in weights]
        invested = sum(weights)
        pick_return = sum(weight * row.get("held_return", row["return"]) for weight, row in zip(weights, picks)) - COST * invested
        universe_return = statistics.fmean(row["return"] for row in rows)
        avoid_return = statistics.fmean(row["return"] for row in avoid)
        index_return = bench[end] / bench[day] - 1 if day in bench and end in bench and bench[day] else None
        periods.append({
            "date": day, "end": end, "count": len(rows),
            "pick_return": round(pick_return, 4), "universe_return": round(universe_return, 4),
            "avoid_return": round(avoid_return, 4), "index_return": round(index_return, 4) if index_return is not None else None,
            "invested": round(invested, 3), "weak_market": weak,
            "early_exits": sum(1 for row in picks if row.get("exit")),
            "picks": [{key: row[key] for key in ("symbol", "name", "score")} | {"return": round(row["return"], 4), "weight": round(weight, 3)}
                      for weight, row in zip(weights, picks)],
            "avoid": [{key: row[key] for key in ("symbol", "name", "score")} | {"return": round(row["return"], 4)} for row in avoid],
        })

    def compound(key: str) -> float | None:
        values = [period[key] for period in periods if period[key] is not None]
        if not values:
            return None
        total = 1.0
        for value in values:
            total *= 1 + value
        return round(total - 1, 4)

    excess = [period["pick_return"] - period["universe_return"] for period in periods]

    def drawdown(key: str) -> float | None:
        """누적 곡선의 최고점 대비 최대 하락(소수)."""
        values = [period[key] for period in periods if period[key] is not None]
        if not values:
            return None
        level, peak, worst = 1.0, 1.0, 0.0
        for value in values:
            level *= 1 + value
            peak = max(peak, level)
            worst = min(worst, level / peak - 1)
        return round(worst, 4)

    pick_returns = [period["pick_return"] for period in periods]
    return {
        "periods": periods,
        "summary": {
            "period_count": len(periods),
            "horizon": horizon,
            "top": top,
            "pick_mean": _mean([period["pick_return"] for period in periods]),
            "universe_mean": _mean([period["universe_return"] for period in periods]),
            "avoid_mean": _mean([period["avoid_return"] for period in periods]),
            "index_mean": _mean([period["index_return"] for period in periods if period["index_return"] is not None]),
            "excess_mean": _mean(excess),
            "hit_rate": round(sum(1 for value in excess if value > 0) / len(excess), 3) if excess else None,
            "avoid_hit_rate": round(sum(1 for period in periods if period["avoid_return"] < period["universe_return"]) / len(periods), 3) if periods else None,
            "pick_compound": compound("pick_return"),
            "universe_compound": compound("universe_return"),
            "index_compound": compound("index_return"),
            "score_buckets": {name: _mean(values) for name, values in buckets.items()},
            "cost_per_period": COST,
            "pick_max_drawdown": drawdown("pick_return"),
            "universe_max_drawdown": drawdown("universe_return"),
            "index_max_drawdown": drawdown("index_return"),
            "pick_volatility": round(statistics.pstdev(pick_returns), 4) if len(pick_returns) > 1 else None,
            "worst_period": round(min(pick_returns), 4) if pick_returns else None,
            "average_invested": _mean([period["invested"] for period in periods]),
            "filtered": filtered,
            "sizing": sizing,
            "exits": exits,
            "early_exit_count": sum(period.get("early_exits", 0) for period in periods),
            "regime": regime,
            "weak_periods": sum(1 for period in periods if period["weak_market"]),
        },
    }


# --- 자료 모으기 -----------------------------------------------------------------------------

async def build_universe(api: TossApiClient, market: str) -> list[dict[str, str]]:
    """분야(대분류)마다 시가총액 상위 PER_SECTOR개를 모아 거래대금 순으로 UNIVERSE_SIZE개. 하루 동안 캐시합니다."""
    key = f"sim:universe:v1:{market}"
    cached = await get_json(key)
    if cached:
        return cached
    flow = await get_sector_flow(api, market)
    picked: dict[str, dict[str, Any]] = {}
    async with _sector_call_lock:
        for sector in flow["sectors"]:
            try:
                rows = await api.fetch_sector_stocks(sector["sector_id"], market.upper())
            except TossApiError:
                logger.exception("universe sector failed: %s", sector["sector_id"])
                rows = []
            for row in rows[:PER_SECTOR]:
                if row.get("symbol") and row["symbol"] not in picked:
                    picked[row["symbol"]] = {"symbol": row["symbol"], "name": row.get("name") or row["symbol"],
                                             "amount": float(row.get("tradingAmount") or 0)}
            await asyncio.sleep(SECTOR_SPACING_SECONDS)
    universe = sorted(picked.values(), key=lambda row: -row["amount"])[:UNIVERSE_SIZE]
    universe = [{"symbol": row["symbol"], "name": row["name"]} for row in universe]
    await set_json(key, universe, 24 * 60 * 60)
    return universe


async def _candles(api: TossApiClient, symbol: str) -> list[dict[str, Any]]:
    key = f"sim:candles:v1:{symbol}:{date.today().isoformat()}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    today = date.today()
    candles = await fetch_daily_candles(api, symbol, today - timedelta(days=HISTORY_DAYS), today)
    slim = [{"date": candle["date"], "close": candle["close"], "volume": candle.get("volume")} for candle in candles]
    await set_json(key, slim, 24 * 60 * 60)
    return slim


# 화면의 "규칙 비교" 표: (이름, 급등 거르기, 비중, 매도 신호, 약세장 절반 투자)
VARIANTS = [
    ("예전 방식(같은 금액)", False, "equal", None, False),
    ("급등 종목 거르기만", True, "equal", None, False),
    ("비중 조절만", False, "inverse_vol", None, False),
    ("거르기 + 비중 조절", True, "inverse_vol", None, False),
    ("비중 조절 + 약세장 절반 투자", False, "inverse_vol", None, True),
    ("거르기 + 비중 조절 + 약세장 절반 투자", True, "inverse_vol", None, True),
    ("비중 조절 + 매도 신호(고점 대비 −10%)", False, "inverse_vol", {"trail": 0.10}, False),
    ("거르기 + 비중 조절 + 매도 신호", True, "inverse_vol", {"trail": 0.10}, False),
]


def _job_key(market: str, horizon: int, top: int) -> str:
    return f"sim:result:v3:{market}:{horizon}:{top}:{date.today().isoformat()}"


async def get_simulation(market: str, horizon: int, top: int) -> dict[str, Any] | None:
    return await get_json(_job_key(market, horizon, top))


async def run_simulation(api: TossApiClient, market: str, horizon: int, top: int) -> None:
    """백그라운드 작업. 진행 상황과 결과를 Redis에 남깁니다(화면이 몇 초마다 확인)."""
    key = _job_key(market, horizon, top)
    state: dict[str, Any] = {"status": "running", "progress": "종목 표본을 고르는 중", "market": market.upper(),
                             "horizon": horizon, "top": top, "started_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")}
    await set_json(key, state, RESULT_TTL)
    try:
        universe = await build_universe(api, market)
        names = {row["symbol"]: row["name"] for row in universe}
        semaphore = asyncio.Semaphore(CANDLE_CONCURRENCY)
        done = 0

        async def load(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            nonlocal done
            async with semaphore:
                try:
                    candles = await _candles(api, symbol)
                except Exception:
                    logger.exception("simulation candles failed: %s", symbol)
                    candles = []
            done += 1
            if done % 10 == 0:
                await set_json(key, {**state, "progress": f"과거 주가를 모으는 중 ({done}/{len(universe)})"}, RESULT_TTL)
            return symbol, candles

        pairs = await asyncio.gather(*(load(row["symbol"]) for row in universe))
        series = {symbol: candles for symbol, candles in pairs if len(candles) > MIN_HISTORY}
        index_symbol, index_name = MARKET_GAUGES[market.upper()][0]
        try:
            benchmark = await _candles(api, index_symbol)
        except Exception:
            benchmark = []
        await set_json(key, {**state, "progress": "시뮬레이션 계산 중"}, RESULT_TTL)
        # 지금 추천 규칙(11-17): 비중 조절은 항상, 급등 종목 거르기·약세장 절반 투자는 5거래일 이하 보유일 때만.
        filtered = horizon <= SHORT_HOLD
        result = await asyncio.to_thread(run_backtest, series, names, horizon, top, None, benchmark, filtered, "inverse_vol", None, filtered)
        variants = []
        for label, variant_filtered, sizing, exits, regime in VARIANTS:
            summary = (await asyncio.to_thread(run_backtest, series, names, horizon, top, None, benchmark,
                                               variant_filtered, sizing, exits, regime))["summary"]
            variants.append({"label": label,
                             "current": variant_filtered == filtered and sizing == "inverse_vol" and not exits and regime == filtered,
                             **{key: summary[key] for key in ("pick_mean", "pick_compound", "hit_rate", "excess_mean",
                                                             "pick_max_drawdown", "worst_period", "early_exit_count")}})
        await set_json(key, {
            **state, "status": "done", "progress": "완료", "finished_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "universe_size": len(series), "index_name": index_name, **result, "variants": variants,
            "method_note": (
                f"표본: 분야(대분류)마다 시가총액 상위 {PER_SECTOR}개를 모은 {len(series)}개 종목. 리밸런스 날짜마다 그날까지의 일봉만으로 "
                f"근거 점수(이동평균 배열·52주 위치·20일 모멘텀·RSI·거래량·변동성·낙폭)를 매겨 상위 {top}개를 추천, 하위 {top}개를 위험으로 고르고 "
                f"(지금 규칙: 변동성이 작은 종목에 더 많이·한 종목 최대 25%"
                f"{', 단기 과열·근거 약한 종목 제외, 지수가 60일 평균선 아래인 약세장에는 절반만 투자' if horizon <= SHORT_HOLD else ''}) "
                f"{horizon}거래일 뒤 수익률을 표본 평균·{index_name}와 비교했습니다. 추천 쪽에는 리밸런스마다 거래 비용 {COST * 100:.1f}%를 뺐습니다."
            ),
            "limits": [
                "과거 시점의 기사·외국인·기관 수급 자료가 없어 기술 지표 근거만 썼습니다(실제 추천은 이 둘과 AI 판단이 더해집니다).",
                "표본이 지금 상장된 종목이라, 그사이 상장폐지된 종목은 빠져 있습니다(생존 편향).",
                "검증 기간이 약 1년이라 시장 국면이 바뀌면 결과가 달라질 수 있습니다. 과거 성과가 앞날을 보장하지 않습니다.",
            ],
        }, RESULT_TTL)
    except Exception as error:
        logger.exception("simulation failed")
        await set_json(key, {**state, "status": "error", "progress": f"실패: {error}"}, 10 * 60)


# --- 실제 추천 기록 추적 ---------------------------------------------------------------------

def _track_key(market: str) -> str:
    return f"rec:history:v1:{market.lower()}"


async def record_recommendations(market: str, result: dict[str, Any]) -> None:
    """AI분석 결과의 추천·위험 종목과 그때 가격을 하루 한 번 남깁니다(같은 날 다시 계산하면 덮어씀)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")

    def entry(stock: dict[str, Any], kind: str, theme: str) -> dict[str, Any]:
        return {"symbol": stock["symbol"], "name": stock["name"], "kind": kind, "theme": theme,
                "price": stock.get("close"), "currency": stock.get("currency"),
                "evidence": (stock.get("evidence") or {}).get("score")}

    picks: dict[str, dict[str, Any]] = {}
    for theme in result.get("themes", []):
        kind = "risk" if theme["kind"] == "risk" else "pick"
        for stock in theme.get("stocks", []):
            picks.setdefault(f"{kind}:{stock['symbol']}", entry(stock, kind, theme["name"]))
    for stock in result.get("ai_picks", []):
        picks[f"ai:{stock['symbol']}"] = entry(stock, "ai_pick", stock.get("theme", ""))
    for stock in result.get("ai_risk_picks", []):
        picks[f"ai_risk:{stock['symbol']}"] = entry(stock, "ai_risk", stock.get("theme", ""))
    for stock in result.get("combined_picks", []):
        picks[f"combined:{stock['symbol']}"] = entry(stock, "combined", stock.get("theme", ""))
    if not any(item["kind"] in {"pick", "ai_pick", "combined"} for item in picks.values()):
        # 추천 종목이 하나도 없는 결과(자료 부족·계산 실패)는 남기지 않습니다. 같은 날 앞서 남긴 기록이 있으면 그대로 둡니다.
        logger.warning("recommendation record skipped (no picks): %s %s", market, today)
        return
    snapshot = {"date": today, "generated_at": result.get("generated_at"), "method": result.get("method"), "items": list(picks.values())}
    history = await get_json(_track_key(market)) or []
    earlier = next((item for item in history if item["date"] == today), None)
    if earlier and result.get("method") != "ai" and earlier.get("method", "ai") == "ai":
        # AI가 잠깐 실패해 규칙 추천만 나온 장중 갱신이 그날 AI 추천을 덮어쓰지 않게(자동 운용이 AI 추천 종목을 잘못 팔지 않게)
        logger.warning("recommendation record kept earlier AI result: %s %s", market, today)
        return
    history = [item for item in history if item["date"] != today] + [snapshot]
    await redis.set(_track_key(market), json.dumps(history[-120:], ensure_ascii=False))


async def track_recommendations(api: TossApiClient, market: str, max_snapshots: int = 30) -> dict[str, Any]:
    """기록된 추천마다 그때 가격 대비 지금 가격 수익률. 구분(규칙 추천·AI 추천·위험·AI 위험)별 평균과 지수를 비교합니다."""
    history = (await get_json(_track_key(market)) or [])[-max_snapshots:]
    today = datetime.now(KST).strftime("%Y-%m-%d")
    symbols = sorted({item["symbol"] for snapshot in history for item in snapshot["items"] if item.get("price")})
    index_symbol, index_name = MARKET_GAUGES[market.upper()][0]
    semaphore = asyncio.Semaphore(CANDLE_CONCURRENCY)

    async def latest(symbol: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            try:
                return symbol, await _candles(api, symbol)
            except Exception:
                return symbol, []

    prices = dict(await asyncio.gather(*(latest(symbol) for symbol in [*symbols, index_symbol])))
    index_by_date = {candle["date"]: candle["close"] for candle in prices.get(index_symbol, [])}

    def close_on_or_before(candles: list[dict[str, Any]], day: str) -> float | None:
        values = [candle["close"] for candle in candles if candle["date"] <= day]
        return values[-1] if values else None

    rows, by_kind = [], {}
    for snapshot in history:
        if snapshot["date"] >= today:
            continue  # 오늘 만든 추천은 아직 성과가 없음
        index_start = close_on_or_before(prices.get(index_symbol, []), snapshot["date"])
        index_now = prices.get(index_symbol, [])[-1]["close"] if prices.get(index_symbol) else None
        index_return = index_now / index_start - 1 if index_start and index_now else None
        for item in snapshot["items"]:
            candles = prices.get(item["symbol"]) or []
            if not item.get("price") or not candles:
                continue
            change = candles[-1]["close"] / item["price"] - 1
            rows.append({**item, "date": snapshot["date"], "return": round(change, 4),
                         "index_return": round(index_return, 4) if index_return is not None else None})
            by_kind.setdefault(item["kind"], []).append(change)
    labels = {"combined": "통합 추천 종목", "pick": "테마별 추천 종목", "ai_pick": "AI 추천 종목", "risk": "위험 테마 종목", "ai_risk": "AI 위험 종목"}
    summary = [
        {"kind": kind, "label": labels.get(kind, kind), "count": len(values), "mean": _mean(values),
         "win_rate": round(sum(1 for value in values if value > 0) / len(values), 3)}
        for kind, values in by_kind.items()
    ]
    index_values = [row["index_return"] for row in rows if row["index_return"] is not None]
    return {
        "market": market.upper(), "snapshots": len(history), "index_name": index_name,
        "index_mean": _mean(index_values), "summary": summary,
        "rows": sorted(rows, key=lambda row: (row["date"], row["kind"]), reverse=True)[:200],
        "note": "서버가 평일 장 마감 뒤 하루 한 번(국장 15:55, 미장 다음 날 06:45 이후) 추천을 새로 만들어 추천·위험 종목과 종가를 남기고(AI분석 탭에서 새로 계산한 날은 그 결과로 덮어씀), 지금 가격과 비교합니다. 기록이 쌓일수록 믿을 만해집니다.",
    }

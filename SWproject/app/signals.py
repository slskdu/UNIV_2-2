"""추천 근거 모듈: 종목 한 개에 대해 근거(지표) 10가지를 계산해 +(긍정)·0(중립)·−(부정)으로 판정하고 0~100점으로 묶습니다.

근거마다 이름·값·판정·설명을 남겨, 화면과 AI가 "왜 추천/위험인지"를 수치로 보여 줄 수 있게 합니다.
과거 시점에도 같은 계산을 할 수 있도록 순수 함수(compute_signals)로 두어 시뮬레이션(app/simulation.py)에서도 씁니다.

근거(가중치): 이동평균 배열(2) · 52주 위치(1) · 20일 모멘텀(1.5) · RSI 과열/과매도(1) · 거래량 동반 여부(1)
· 변동성(1) · 60일 낙폭(1) · 외국인·기관 수급(국내, 1.5) · 매수 유의사항(2) · 기사 논조(1)

11-17 근거별 검증(국장·미장 약 1년, 앞·뒤 절반): "거래량 동반 상승"은 이후 수익이 표본 평균보다 오히려 낮아(국장 두 절반 모두)
점수에서는 중립(0)으로 두고 화면에는 참고로만 보여 줍니다. "거래량 동반 하락"은 그대로 부정 근거입니다.
"""
import asyncio
import logging
import math
from datetime import date, timedelta
from typing import Any

from .history import describe_warnings, fetch_daily_candles
from .redis_client import get_json, set_json
from .toss_api import TossApiClient, TossApiError

logger = logging.getLogger(__name__)

CANDLE_DAYS = 380  # 52주(약 250거래일)와 60일선을 계산할 만큼
FLOW_DAYS = 5
WEIGHTS = {
    "ma_alignment": 2.0, "year_position": 1.0, "momentum": 1.5, "rsi": 1.0, "volume": 1.0,
    "volatility": 1.0, "drawdown": 1.0, "flows": 1.5, "warnings": 2.0, "news": 1.0,
}
TECHNICAL_KEYS = ("ma_alignment", "year_position", "momentum", "rsi", "volume", "volatility", "drawdown")


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for previous, current in zip(closes[-period - 1:-1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain, average_loss = sum(gains) / period, sum(losses) / period
    if average_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + average_gain / average_loss), 1)


def _factor(key: str, label: str, value: str, signal: int, text: str) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "signal": signal, "weight": WEIGHTS[key], "text": text}


def technical_factors(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """일봉(과거→최신)만으로 계산하는 근거 7가지. 자료가 모자라면 그 근거는 빠집니다."""
    rows = [candle for candle in candles if candle.get("close")]
    closes = [candle["close"] for candle in rows]
    volumes = [candle.get("volume") or 0.0 for candle in rows]
    factors: list[dict[str, Any]] = []
    if len(closes) < 25:
        return factors
    close = closes[-1]

    ma20 = _average(closes[-20:])
    ma60 = _average(closes[-60:]) if len(closes) >= 60 else None
    if ma60:
        if close > ma20 > ma60:
            factors.append(_factor("ma_alignment", "이동평균 정배열", "주가>20일선>60일선", 1, "단기·중기 흐름이 모두 위를 향합니다."))
        elif close < ma20 < ma60:
            factors.append(_factor("ma_alignment", "이동평균 역배열", "주가<20일선<60일선", -1, "단기·중기 흐름이 모두 아래를 향합니다."))
        else:
            factors.append(_factor("ma_alignment", "이동평균 혼조", "20·60일선 사이", 0, "흐름 방향이 아직 정해지지 않았습니다."))

    year = closes[-250:]
    if len(year) >= 120:
        high, low = max(year), min(year)
        position = (close - low) / (high - low) if high > low else 0.5
        days = len(year)
        label_period = "52주" if days >= 240 else f"{days}일"
        value = f"{label_period} 범위의 {position * 100:.0f}% 위치 (고점 대비 {(close / high - 1) * 100:+.1f}%)"
        if position >= 0.8:
            factors.append(_factor("year_position", f"{label_period} 고점 근처", value, 1, "강한 종목이 신고가 부근에 있습니다."))
        elif position <= 0.2:
            factors.append(_factor("year_position", f"{label_period} 저점 근처", value, -1, "약한 흐름이 바닥권에 머물러 있습니다."))
        else:
            factors.append(_factor("year_position", f"{label_period} 중간 위치", value, 0, "고점·저점 어느 쪽에도 가깝지 않습니다."))

    momentum = (close / closes[-21] - 1) * 100 if len(closes) > 20 and closes[-21] else None
    if momentum is not None:
        signal = 1 if momentum > 5 else -1 if momentum < -5 else 0
        label = "20일 상승세" if signal > 0 else "20일 하락세" if signal < 0 else "20일 보합"
        factors.append(_factor("momentum", label, f"20일 {momentum:+.1f}%", signal, "최근 한 달(20거래일) 수익률입니다."))

    strength = rsi(closes)
    if strength is not None:
        if strength >= 70:
            factors.append(_factor("rsi", "RSI 과열", f"RSI {strength:.0f}", -1, "단기에 많이 올라 쉬어 갈 수 있습니다."))
        elif strength <= 30:
            factors.append(_factor("rsi", "RSI 과매도", f"RSI {strength:.0f}", 1, "단기에 많이 내려 되돌림 반등이 나올 수 있습니다."))
        else:
            factors.append(_factor("rsi", "RSI 보통", f"RSI {strength:.0f}", 0, "과열도 과매도도 아닙니다."))

    if len(volumes) >= 25 and any(volumes[-25:-5]):
        recent, base = _average(volumes[-5:]), _average(volumes[-25:-5])
        ratio = recent / base if base else None
        five_day = (close / closes[-6] - 1) * 100 if closes[-6] else 0.0
        if ratio and ratio >= 1.5 and five_day > 0:
            factors.append(_factor("volume", "거래량 동반 상승", f"최근 5일 거래량이 평소의 {ratio:.1f}배, 5일 {five_day:+.1f}%", 0,
                                   "거래가 몰리며 올랐지만, 과거 검증에서 이후 수익이 평균보다 낮아 점수에는 넣지 않습니다(참고)."))
        elif ratio and ratio >= 1.5:
            factors.append(_factor("volume", "거래량 동반 하락", f"최근 5일 거래량이 평소의 {ratio:.1f}배, 5일 {five_day:+.1f}%", -1,
                                   "거래가 몰리며 내린 방향이라 신호가 강합니다."))
        elif ratio:
            factors.append(_factor("volume", "거래량 평소 수준", f"평소의 {ratio:.1f}배", 0, "거래량에 뚜렷한 변화가 없습니다."))

    daily = [(current / previous - 1) * 100 for previous, current in zip(closes[-21:-1], closes[-20:]) if previous]
    if len(daily) >= 10:
        mean = sum(daily) / len(daily)
        deviation = math.sqrt(sum((value - mean) ** 2 for value in daily) / len(daily))
        if deviation > 4:
            factors.append(_factor("volatility", "변동성 큼", f"하루 변동 표준편차 {deviation:.1f}%", -1, "가격이 크게 흔들려 손실 폭도 커질 수 있습니다."))
        else:
            factors.append(_factor("volatility", "변동성 보통", f"하루 변동 표준편차 {deviation:.1f}%", 0, "가격 흔들림이 크지 않습니다."))

    window = closes[-60:]
    peak, worst = window[0], 0.0
    for value in window:
        peak = max(peak, value)
        worst = min(worst, (value / peak - 1) * 100 if peak else 0.0)
    if worst <= -25:
        factors.append(_factor("drawdown", "큰 낙폭 경험", f"60일 고점 대비 최대 {worst:.1f}%", -1, "최근 크게 빠진 적이 있어 회복이 더딜 수 있습니다."))
    else:
        factors.append(_factor("drawdown", "낙폭 제한적", f"60일 고점 대비 최대 {worst:.1f}%", 0, "최근 큰 하락은 없었습니다."))
    return factors


def flow_factor(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """국내 종목 투자자별 매매(최근 5일)의 외국인·기관 순매수 수량 합."""
    if not records:
        return None
    recent = records[:FLOW_DAYS]

    def net(group: str) -> float:
        return sum(float((record.get(group) or {}).get("netBuyVolume") or 0) for record in recent)

    foreign, institution = net("foreigner"), net("institution")
    total = foreign + institution
    value = f"최근 {len(recent)}일 외국인 {foreign:+,.0f}주 · 기관 {institution:+,.0f}주"
    if foreign > 0 and institution > 0:
        return _factor("flows", "외국인·기관 동반 순매수", value, 1, "큰손 투자자가 함께 사고 있습니다.")
    if foreign < 0 and institution < 0:
        return _factor("flows", "외국인·기관 동반 순매도", value, -1, "큰손 투자자가 함께 팔고 있습니다.")
    signal = 1 if total > 0 else -1 if total < 0 else 0
    label = "외국인·기관 합산 순매수" if signal > 0 else "외국인·기관 합산 순매도" if signal < 0 else "외국인·기관 수급 중립"
    return _factor("flows", label, value, signal, "외국인과 기관의 방향이 엇갈립니다.")


def warning_factor(warnings: list[dict[str, Any]]) -> dict[str, Any]:
    if warnings:
        labels = ", ".join(warning["label"] for warning in warnings)
        return _factor("warnings", "매수 유의사항 있음", labels, -1, "거래소·증권사가 주의를 표시한 종목입니다.")
    return _factor("warnings", "매수 유의사항 없음", "없음", 0, "주의 표시가 없습니다.")


def news_factor(digest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not digest or not digest.get("article_count"):
        return None
    positive, negative = digest.get("positive", 0), digest.get("negative", 0)
    value = f"호재 {positive} · 악재 {negative}건"
    if positive > negative * 1.5 and positive >= 3:
        return _factor("news", "기사 호재 우위", value, 1, "최근 기사가 호재 쪽입니다.")
    if negative > positive * 1.5 and negative >= 3:
        return _factor("news", "기사 악재 우위", value, -1, "최근 기사가 악재 쪽입니다.")
    return _factor("news", "기사 논조 혼재", value, 0, "호재·악재가 섞여 있습니다.")


WARNINGS_TTL = 10 * 60
FLOWS_TTL = 6 * 60 * 60  # 투자자별 매매는 하루 한 번 바뀝니다


async def cached_warnings(api: TossApiClient, symbol: str) -> list[dict[str, Any]]:
    """매수 유의사항 원본(10분 캐시). 토스 STOCK 그룹 한도(429)를 덜 쓰려고 화면들이 함께 씁니다."""
    key = f"stock:warnings:v1:{symbol}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    warnings = await api.fetch_stock_warnings(symbol)
    await set_json(key, warnings, WARNINGS_TTL)
    return warnings


async def cached_flows(api: TossApiClient, symbol: str) -> list[dict[str, Any]]:
    """국내 종목 투자자별 매매 최근 10일(6시간 캐시)."""
    key = f"stock:flows:v1:{symbol}:{date.today().isoformat()}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    result = await api._get_result(f"/api/v1/stocks/{symbol}/investor-trading", {"count": 10}) or {}
    records = result.get("records", [])
    await set_json(key, records, FLOWS_TTL)
    return records


def summarize_evidence(factors: list[dict[str, Any]]) -> dict[str, Any]:
    """근거들을 0~100점으로 묶습니다. 50이 중립, 긍정 근거가 많고 무거울수록 100에 가깝습니다."""
    total_weight = sum(factor["weight"] for factor in factors)
    if not total_weight:
        return {"score": None, "grade": "근거 부족", "factors": factors, "positives": [], "negatives": []}
    balance = sum(factor["weight"] * factor["signal"] for factor in factors) / total_weight
    score = round(50 + 50 * balance)
    grade = "근거 강함" if score >= 65 else "근거 보통" if score >= 50 else "근거 약함" if score >= 35 else "부정 근거 우세"
    return {
        "score": score,
        "grade": grade,
        "factors": factors,
        "positives": [f"{factor['label']}: {factor['value']}" for factor in factors if factor["signal"] > 0],
        "negatives": [f"{factor['label']}: {factor['value']}" for factor in factors if factor["signal"] < 0],
    }


def compute_signals(candles: list[dict[str, Any]], warnings: list[dict[str, Any]] | None = None,
                    flow_records: list[dict[str, Any]] | None = None, news_digest: dict[str, Any] | None = None) -> dict[str, Any]:
    factors = technical_factors(candles)
    if warnings is not None:
        factors.append(warning_factor(warnings))
    for extra in (flow_factor(flow_records or []), news_factor(news_digest)):
        if extra:
            factors.append(extra)
    return summarize_evidence(factors)


async def stock_evidence(api: TossApiClient, symbol: str, market_country: str, candles: list[dict[str, Any]] | None = None,
                         warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """종목 하나의 근거 전체(일봉·유의사항·국내 수급·캐시된 기사 논조). 이미 받은 일봉·유의사항이 있으면 다시 받지 않습니다."""
    today = date.today()
    if candles is None or len(candles) < 200:
        candles = await fetch_daily_candles(api, symbol, today - timedelta(days=CANDLE_DAYS), today)
    if warnings is None:
        try:
            warnings = describe_warnings(await cached_warnings(api, symbol))
        except TossApiError:
            warnings = []
    flows: list[dict[str, Any]] = []
    if market_country == "KR":
        try:
            flows = await cached_flows(api, symbol)
        except TossApiError:
            logger.info("investor trading unavailable: %s", symbol)
    news_cache = await get_json(f"insight:stock-news:v10:{symbol}:7")
    digest = news_cache.get("news_digest") if news_cache and news_cache.get("found") else None
    return compute_signals(candles, warnings, flows, digest)


async def evidence_for(api: TossApiClient, stocks: list[dict[str, Any]], market_country: str,
                       semaphore: asyncio.Semaphore | None = None) -> dict[str, dict[str, Any]]:
    """여러 종목의 근거를 조회 한도에 맞춰 천천히 모읍니다. {symbol: evidence}"""
    semaphore = semaphore or asyncio.Semaphore(2)

    async def one(stock: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            try:
                return stock["symbol"], await stock_evidence(api, stock["symbol"], market_country, stock.get("candles"), stock.get("warnings"))
            except Exception:
                logger.exception("evidence failed: %s", stock["symbol"])
                return stock["symbol"], None

    pairs = await asyncio.gather(*(one(stock) for stock in stocks))
    return {symbol: evidence for symbol, evidence in pairs if evidence}

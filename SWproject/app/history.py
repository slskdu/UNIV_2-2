from datetime import date
from math import sqrt
from statistics import pstdev
from typing import Any

from .toss_api import TossApiClient

INDICATOR_NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
MAX_CANDLE_PAGES = 8  # 200봉 x 8 = 약 6년치 일봉
DIRECTION_SENSITIVITY = 0.25


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


async def fetch_daily_candles(
    api: TossApiClient,
    symbol: str,
    start: date,
    end: date,
    adjusted: bool = True,
) -> list[dict[str, Any]]:
    """start~end 구간의 일봉을 과거→최신 순으로 반환합니다. adjusted=False면 수정 전 원주가입니다."""
    indicator = symbol.upper() in INDICATOR_NAMES
    if indicator:
        symbol = symbol.upper()
    start_text = start.isoformat()
    # 미국 일봉의 timestamp는 KST 13~14시로 내려오므로 종료일의 끝 시각을 상한으로 둡니다.
    before: str | None = f"{end.isoformat()}T23:59:59+09:00"
    by_day: dict[str, dict[str, Any]] = {}

    for _ in range(MAX_CANDLE_PAGES):
        page = await api.fetch_candles(symbol, "1d", 200, before, indicator, adjusted)
        rows = page.get("candles", [])
        reached_start = False
        for row in rows:
            day = str(row.get("timestamp", ""))[:10]
            if not day:
                continue
            if day < start_text:
                reached_start = True
                continue
            by_day[day] = {
                "date": day,
                "open": _number(row.get("openPrice")),
                "high": _number(row.get("highPrice")),
                "low": _number(row.get("lowPrice")),
                "close": _number(row.get("closePrice")),
                "volume": _number(row.get("volume")),
                "currency": row.get("currency"),
            }
        before = page.get("nextBefore")
        if reached_start or not rows or not before:
            break

    return [by_day[day] for day in sorted(by_day)]


ADJUSTMENT_MIN_CHANGE = 0.03  # 수정비율이 이보다 크게 바뀐 날만 권리 변동으로 봅니다.
WARNING_LABELS = {
    "LIQUIDATION_TRADING": "정리매매(상장폐지 절차 진행 중)",
    "INVESTMENT_RISK": "투자위험종목",
    "INVESTMENT_WARNING": "투자경고종목",
    "OVERHEATED": "단기과열종목",
    "VI_STATIC_AND_DYNAMIC": "변동성 완화장치(VI) 발동",
    "VI_STATIC": "변동성 완화장치(VI) 발동",
    "VI_DYNAMIC": "변동성 완화장치(VI) 발동",
    "STOCK_WARRANTS": "신주인수권증서/증권",
}


def _ratio_text(value: float) -> str:
    rounded = round(value)
    return str(rounded) if abs(value - rounded) < 0.05 else f"{value:.2f}"


def detect_price_adjustments(
    adjusted: list[dict[str, Any]],
    raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """수정주가와 원주가를 비교해 주식 병합·분할 같은 권리 변동이 반영된 날을 찾습니다.

    수정비율(수정주가 ÷ 원주가)은 권리 변동일 전까지 일정하다가 그날 바뀝니다. 예를 들어 15:1 병합이면
    전날까지 15, 당일부터 1입니다. 원주가로는 그날 주가가 15배 뛴 것처럼 보이지만 실제 가치 변화가 아닙니다.
    """
    raw_by_day = {candle["date"]: candle["close"] for candle in raw if candle.get("close")}
    events: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for candle in adjusted:
        day, raw_close = candle["date"], raw_by_day.get(candle["date"])
        if not raw_close or not candle.get("close"):
            continue
        factor = candle["close"] / raw_close
        if previous and abs(factor / previous["factor"] - 1) > ADJUSTMENT_MIN_CHANGE:
            ratio = previous["factor"] / factor  # 1보다 크면 병합, 작으면 분할
            if ratio >= 1.5:
                kind, label = "reverse_split", f"주식 병합 약 {_ratio_text(ratio)}:1"
            elif ratio <= 1 / 1.5:
                kind, label = "split", f"주식 분할 약 1:{_ratio_text(1 / ratio)}"
            else:
                kind, label = "other", "권리 조정(증자·배당 등)"
            events.append({
                "date": day,
                "previous_date": previous["date"],
                "kind": kind,
                "label": label,
                "ratio": round(ratio, 4),
                "raw_change_rate": round((raw_close / previous["raw_close"] - 1) * 100, 2),
                "adjusted_change_rate": round((candle["close"] / previous["close"] - 1) * 100, 2),
            })
        previous = {"date": day, "factor": factor, "raw_close": raw_close, "close": candle["close"]}
    return events


def describe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """토스 매수 유의사항을 화면용 문구로 바꿉니다. 같은 문구는 한 번만 남깁니다."""
    described: dict[str, dict[str, Any]] = {}
    for warning in warnings:
        code = str(warning.get("warningType") or "")
        label = WARNING_LABELS.get(code, code)
        if label and label not in described:
            described[label] = {
                "type": code,
                "label": label,
                "start_date": warning.get("startDate"),
                "end_date": warning.get("endDate"),
                "severe": code in {"LIQUIDATION_TRADING", "INVESTMENT_RISK", "INVESTMENT_WARNING"},
            }
    return sorted(described.values(), key=lambda item: not item["severe"])


def _threshold(volatility: float, trading_days: int) -> float:
    """방향 판정 기준(%). 평소 흔들림(일간 변동성 x 기간의 제곱근)의 일정 비율을 넘어야 상승·하락으로 봅니다."""
    return max(1.0, volatility * sqrt(trading_days) * DIRECTION_SENSITIVITY)


def _direction(change: float, threshold: float) -> str:
    return "상승" if change > threshold else "하락" if change < -threshold else "횡보"


def summarize_trend(candles: list[dict[str, Any]], max_segments: int = 5) -> dict[str, Any] | None:
    """일봉 목록(과거→최신)에서 기간 수익률·낙폭·변동성·구간별 흐름을 계산합니다."""
    candles = [candle for candle in candles if candle.get("close")]
    if len(candles) < 2:
        return None

    closes = [candle["close"] for candle in candles]
    first, last = candles[0], candles[-1]
    change_rate = (closes[-1] / closes[0] - 1) * 100

    daily = [
        {"date": candles[index]["date"], "change_rate": (closes[index] / closes[index - 1] - 1) * 100}
        for index in range(1, len(closes))
    ]
    volatility = pstdev(item["change_rate"] for item in daily) if len(daily) > 1 else 0.0

    highest = max(candles, key=lambda candle: candle["high"] or candle["close"])
    lowest = min(candles, key=lambda candle: candle["low"] or candle["close"])

    peak = candles[0]
    drawdown = {"rate": 0.0, "peak_date": first["date"], "trough_date": first["date"]}
    for candle in candles:
        if candle["close"] > peak["close"]:
            peak = candle
        rate = (candle["close"] / peak["close"] - 1) * 100
        if rate < drawdown["rate"]:
            drawdown = {"rate": rate, "peak_date": peak["date"], "trough_date": candle["date"]}

    # 구간 수는 최대 max_segments개. 방향 판정 기준은 일간 변동성에 구간 길이를 반영한 값입니다.
    segment_count = max(1, min(max_segments, len(daily) // 2))
    size = len(daily) / segment_count
    segments = []
    for index in range(segment_count):
        left = round(index * size)
        right = round((index + 1) * size)
        if right <= left:
            continue
        segment_change = (closes[right] / closes[left] - 1) * 100
        threshold = _threshold(volatility, right - left)
        segments.append({
            "start_date": candles[left]["date"],
            "end_date": candles[right]["date"],
            "trading_days": right - left,
            "change_rate": round(segment_change, 2),
            "direction": _direction(segment_change, threshold),
            "threshold": round(threshold, 2),
        })

    flow: list[str] = []
    for segment in segments:
        if not flow or flow[-1] != segment["direction"]:
            flow.append(segment["direction"])

    overall_threshold = _threshold(volatility, len(daily))
    best = max(daily, key=lambda item: item["change_rate"])
    worst = min(daily, key=lambda item: item["change_rate"])

    return {
        "start_date": first["date"],
        "end_date": last["date"],
        "trading_days": len(candles),
        "start_close": first["close"],
        "end_close": last["close"],
        "change_rate": round(change_rate, 2),
        "direction": _direction(change_rate, overall_threshold),
        "flow": " → ".join(flow),
        "high": {"price": highest["high"] or highest["close"], "date": highest["date"]},
        "low": {"price": lowest["low"] or lowest["close"], "date": lowest["date"]},
        "max_drawdown": {**drawdown, "rate": round(drawdown["rate"], 2)},
        "daily_volatility": round(volatility, 2),
        "up_days": sum(1 for item in daily if item["change_rate"] > 0),
        "down_days": sum(1 for item in daily if item["change_rate"] < 0),
        "best_day": {"date": best["date"], "change_rate": round(best["change_rate"], 2)},
        "worst_day": {"date": worst["date"], "change_rate": round(worst["change_rate"], 2)},
        "segments": segments,
        "note": "기간 첫 거래일 종가 대비 마지막 거래일 종가 기준이며, 오늘 봉은 장중이면 확정값이 아닙니다.",
    }


def build_equal_weight_index(series_by_symbol: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """여러 종목의 일봉을 시작일 100으로 맞춘 뒤 동일 가중 평균한 지수 일봉을 만듭니다."""
    usable = {symbol: rows for symbol, rows in series_by_symbol.items() if len(rows) >= 2}
    if not usable:
        return []
    common_days = sorted(set.intersection(*(
        {row["date"] for row in rows} for rows in usable.values()
    )))
    if len(common_days) < 2:
        return []

    closes = {
        symbol: {row["date"]: row["close"] for row in rows}
        for symbol, rows in usable.items()
    }
    base_day = common_days[0]
    index_rows = []
    for day in common_days:
        level = sum(
            closes[symbol][day] / closes[symbol][base_day] * 100
            for symbol in usable
            if closes[symbol][base_day]
        ) / len(usable)
        index_rows.append({
            "date": day, "open": level, "high": level, "low": level,
            "close": round(level, 2), "volume": 0, "currency": None,
        })
    return index_rows

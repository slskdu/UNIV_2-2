"""보유 종목 매도 신호.

시뮬레이션(app/simulation.py, 11-17 비교)으로 기준을 정했습니다.
- 산 가격 대비 손절(−5·−8·−10%)은 반등을 놓쳐 국장·미장 거의 모든 경우 수익을 깎았다 → 자동 매도 대신 "주의"로만 알립니다.
- 고점 대비 −10%(산 뒤 +5% 이상 오른 다음 최고 종가에서 −10%)는 국장 20일 보유 누적 +45%→+64%로 도움이 됐다 → "매도 고려".

단계: sell(매도 고려) > watch(주의) > hold(보유 유지).
"""
from typing import Any

from .signals import rsi

TRAIL_DROP = 0.10  # 최고 종가 대비 이만큼 빠지면 매도 고려
TRAIL_ARM = 0.05  # 산 가격보다 이만큼 오른 적이 있어야 고점 대비 신호를 봅니다
LOSS_WATCH = 0.10  # 산 가격 대비 이만큼 손실이면 주의
PROFIT_WATCH = 0.20  # 이만큼 이익이고 RSI가 과열이면 이익 실현 고려(주의)
RSI_HOT = 75
EVIDENCE_WEAK = 45
VOLUME_SPIKE = 1.5

LEVEL_LABELS = {"sell": "매도 고려", "watch": "주의", "hold": "보유 유지"}


def _signal(level: str, key: str, label: str, text: str) -> dict[str, str]:
    return {"level": level, "key": key, "label": label, "text": text}


def exit_signals(candles: list[dict[str, Any]], entry_price: float, entry_date: str | None = None,
                 evidence_score: float | None = None) -> dict[str, Any]:
    """candles: 일봉(과거→최신, date·close·volume). 산 날(entry_date) 이후 최고 종가로 고점 대비 하락을 봅니다.

    산 날을 모르면(실제 계좌의 평균가 등) 최근 60일 최고 종가를 대신 씁니다.
    """
    rows = [candle for candle in candles if candle.get("close")]
    if not rows or not entry_price:
        return {"level": "hold", "label": LEVEL_LABELS["hold"], "signals": []}
    closes = [candle["close"] for candle in rows]
    close = closes[-1]
    since = [candle["close"] for candle in rows if entry_date and candle["date"] >= entry_date] or closes[-60:]
    peak = max(entry_price, *since)
    gain = close / entry_price - 1
    from_peak = close / peak - 1
    signals: list[dict[str, str]] = []

    if peak >= entry_price * (1 + TRAIL_ARM) and from_peak <= -TRAIL_DROP:
        signals.append(_signal("sell", "trail", "고점 대비 하락",
                               f"산 뒤 최고 {peak:,.2f}에서 {from_peak * 100:.1f}% 내려왔습니다(기준 −{TRAIL_DROP * 100:.0f}%). 이익을 지키려면 파는 것을 고려하세요."))
    if gain <= -LOSS_WATCH:
        signals.append(_signal("watch", "loss", "손실 확대",
                               f"산 가격보다 {gain * 100:.1f}%입니다. 시뮬레이션에서는 바로 손절하면 반등을 놓쳐 평균적으로 손해였으니, 근거가 여전한지 확인하세요."))
    strength = rsi(closes)
    if gain >= PROFIT_WATCH and strength is not None and strength >= RSI_HOT:
        signals.append(_signal("watch", "profit", "이익 실현 고려",
                               f"+{gain * 100:.1f}% 이익에 RSI {strength:.0f}로 과열입니다. 일부 이익 실현을 고려할 수 있습니다."))
    if len(closes) >= 21:
        average20 = sum(closes[-20:]) / 20
        volumes = [candle.get("volume") or 0 for candle in rows]
        base_volume = sum(volumes[-21:-1]) / 20 if any(volumes[-21:-1]) else 0
        spike = volumes[-1] / base_volume if base_volume else 0
        if close < average20 and closes[-2] >= sum(closes[-21:-1]) / 20 and close < closes[-2] and spike >= VOLUME_SPIKE:
            signals.append(_signal("watch", "trend", "추세 이탈",
                                   f"거래량이 평소의 {spike:.1f}배로 늘며 20일 평균선({average20:,.2f}) 아래로 내려갔습니다."))
    if evidence_score is not None and evidence_score < EVIDENCE_WEAK:
        signals.append(_signal("watch", "evidence", "근거 약화", f"근거 점수가 {evidence_score:.0f}점으로 약해졌습니다(기준 {EVIDENCE_WEAK}점)."))

    level = "sell" if any(signal["level"] == "sell" for signal in signals) else "watch" if signals else "hold"
    return {"level": level, "label": LEVEL_LABELS[level], "signals": signals, "gain": round(gain, 4),
            "peak": peak, "from_peak": round(from_peak, 4)}

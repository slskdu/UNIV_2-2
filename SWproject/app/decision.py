"""자동 운용 매수·매도 판단 모듈.

가격만 보지 않고 세 가지 자료를 함께 봅니다.
- 시장 분석: 대표 지수가 60일 평균선 아래면 약세장(새로 살 때 절반만 투자, 고점 대비 매도 기준을 −7%로 좁힘),
  분야 흐름(insight:sector-flow)에서 종목의 테마가 '하락 지속'·'하락 전환'이면 사지 않고, 들고 있으면 매도 근거로 씁니다.
- 추천: 추천 기록의 근거 점수(0~100), AI 추천 여부, 위험 종목(규칙·AI) 지정.
- AI 분석: 하루 한 번 AI가 보유 종목을 점검해 '매도'로 본 종목은 판다(ai_review, app/managed_live.py가 호출).
여기에 종목의 최근 가격 흐름(5·20일 수익률, 20일 평균선, RSI)을 더합니다.

사기(rank_buys): 위험 지정·단기 과열·근거 부족(50점 미만, 약세장 60점 미만)·하락 흐름 테마는 빼고,
  근거 점수 + AI 추천 가산 − 20일선 아래·RSI 과열 감점으로 순서를 정합니다.
팔기(exit_reason): 아래 중 하나면 팝니다(위에서부터).
  1) 고점 대비 하락: 산 뒤 +5% 넘게 오른 적이 있고 최고가 대비 −10%(약세장 −7%) — 장중 언제든
  2) 위험 종목 지정: 최신 추천 기록에서 규칙·AI가 위험 종목으로 지정 — 장중 언제든
  3) AI 점검 매도 의견 — 장중 언제든
  4) 추천 근거 약화: 최신 추천에서 빠졌고, 20일 평균선 아래, 20일 수익률 −5% 이하, 산 가격보다 손실 — 마감 무렵만
  5) 테마 흐름 하락 전환: 테마가 '하락 지속·하락 전환'이고 20일 평균선 아래 — 마감 무렵만
  6) 이익 실현: 산 뒤 +25% 이상이고 RSI 80 이상(과열) — 마감 무렵만
  "마감 무렵만"은 장중 잠깐의 출렁임으로 팔지 않도록 국장 14:30·미장 15:00(뉴욕) 뒤나 일봉 계산의 종가에서만 봅니다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .ai_client import ai_enabled, chat_completion
from .signals import rsi
from .simulation import is_overheated

logger = logging.getLogger(__name__)

BUY_MIN_SCORE, WEAK_BUY_MIN_SCORE = 50, 60
WEAK_INVEST_FRACTION = 0.5
WEAK_TRAIL = 0.07
AI_BONUS, BOTH_BONUS, BELOW_MA_PENALTY, HOT_RSI_PENALTY = 8, 4, 10, 5
THESIS_R20 = -0.05
PROFIT_TAKE, PROFIT_RSI = 0.25, 80
FALLING_FLOWS = {"하락 지속", "하락 전환"}
RISK_KINDS = {"risk", "ai_risk"}
AI_REVIEW_TIMEOUT = 40.0


def metrics(closes: list[float]) -> dict[str, Any] | None:
    """최근 가격 흐름: 5·20일 수익률(소수), 20일 평균선 위인지, RSI(14)."""
    closes = [value for value in closes if value]
    if len(closes) < 6:
        return None

    def change(days: int) -> float | None:
        return closes[-1] / closes[-1 - days] - 1 if len(closes) > days else None

    average20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    return {"r5": change(5), "r20": change(20), "ma20": average20,
            "above_ma20": (closes[-1] >= average20) if average20 else None, "rsi": rsi(closes)}


@dataclass
class Context:
    """판단에 쓰는 그 시점의 자료."""
    weak_market: bool = False
    flows: dict[str, str] = field(default_factory=dict)  # 테마(분야) 이름 → 흐름
    review: dict[str, dict[str, str]] = field(default_factory=dict)  # 종목 → AI 점검 {"action", "reason"}
    late: bool = True  # 마감 무렵(하루 한 번 보는 매도 기준을 볼 때)
    tech: Callable[[str], dict[str, Any] | None] = lambda symbol: None


def snapshot_info(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """추천 기록 → 종목별 {evidence, kinds, theme}."""
    info: dict[str, dict[str, Any]] = {}
    for item in (snapshot or {}).get("items", []):
        row = info.setdefault(item["symbol"], {"evidence": None, "kinds": set(), "theme": item.get("theme") or ""})
        row["kinds"].add(item.get("kind"))
        if item.get("evidence") is not None:
            row["evidence"] = max(row["evidence"] or 0, item["evidence"])
        if item.get("theme") and not row["theme"]:
            row["theme"] = item["theme"]
    return info


def rank_buys(options: list[tuple[str, str]], snapshot: dict[str, Any] | None, ctx: Context) -> list[tuple[str, str, str]]:
    """살 후보를 걸러 점수 순으로: [(symbol, name, 이유)]."""
    info = snapshot_info(snapshot)
    minimum = WEAK_BUY_MIN_SCORE if ctx.weak_market else BUY_MIN_SCORE
    ranked = []
    for symbol, name in options:
        row = info.get(symbol, {"evidence": None, "kinds": set(), "theme": ""})
        if row["kinds"] & RISK_KINDS:
            continue
        if (ctx.review.get(symbol) or {}).get("action") == "sell":
            continue  # 오늘 AI 점검이 매도로 본 종목은 추천에 있어도 사지 않음
        evidence = row["evidence"]
        if evidence is not None and evidence < minimum:
            continue
        if ctx.flows.get(row["theme"]) in FALLING_FLOWS:
            continue
        tech = ctx.tech(symbol) or {}
        if is_overheated(tech.get("r5"), tech.get("r20")):
            continue
        ai = "ai_pick" in row["kinds"]
        score = (evidence if evidence is not None else 50) + (AI_BONUS if ai else 0) + (BOTH_BONUS if ai and "pick" in row["kinds"] else 0)
        score -= BELOW_MA_PENALTY if tech.get("above_ma20") is False else 0
        score -= HOT_RSI_PENALTY if (tech.get("rsi") or 0) >= 75 else 0
        parts = [f"근거 {evidence}점" if evidence is not None else "근거 점수 없음"] + (["AI 추천"] if ai else [])
        if ctx.flows.get(row["theme"]):
            parts.append(f"{row['theme']} {ctx.flows[row['theme']]}")
        ranked.append((score, symbol, name, " · ".join(parts)))
    ranked.sort(key=lambda item: -item[0])
    return [(symbol, name, reason) for _, symbol, name, reason in ranked]


def exit_reason(item: dict[str, Any], price: float, snapshot: dict[str, Any] | None, ctx: Context,
                trail: float, arm: float) -> str | None:
    """보유 종목을 팔 이유(없으면 None). item에는 buy_price·peak(이 가격을 반영한 최고가)가 있어야 합니다."""
    symbol = item["symbol"]
    drop = min(trail, WEAK_TRAIL) if ctx.weak_market else trail
    if item["peak"] >= item["buy_price"] * (1 + arm) and price / item["peak"] - 1 <= -drop:
        return f"고점 대비 −{round(drop * 100)}%" + (" (약세장 기준)" if ctx.weak_market and drop < trail else "")
    info = snapshot_info(snapshot)
    if info.get(symbol, {}).get("kinds", set()) & RISK_KINDS:
        return "위험 종목으로 지정됨(추천 기록)"
    review = ctx.review.get(symbol)
    if review and review.get("action") == "sell":
        return f"AI 점검: {review.get('reason') or '매도 의견'}"
    if not ctx.late:
        return None
    tech = ctx.tech(symbol) or {}
    gain = price / item["buy_price"] - 1
    recommended = symbol in info and not (info[symbol]["kinds"] & RISK_KINDS)
    if (not recommended and tech.get("above_ma20") is False and tech.get("r20") is not None
            and tech["r20"] <= THESIS_R20 and gain < 0):
        return f"추천 근거 약화(추천에서 빠짐·20일선 아래·20일 {tech['r20'] * 100:+.1f}%)"
    theme = item.get("theme") or info.get(symbol, {}).get("theme") or ""
    if ctx.flows.get(theme) in FALLING_FLOWS and tech.get("above_ma20") is False:
        return f"테마 흐름 하락({theme} {ctx.flows[theme]}·20일선 아래)"
    if gain >= PROFIT_TAKE and (tech.get("rsi") or 0) >= PROFIT_RSI:
        return f"이익 실현(+{gain * 100:.0f}%·RSI {tech['rsi']:.0f} 과열)"
    return None


async def ai_review(market: str, positions: list[dict[str, Any]], weak_market: bool | None) -> dict[str, dict[str, str]]:
    """보유 종목을 AI가 점검해 hold/sell 의견을 냅니다(하루 한 번). AI를 못 쓰면 빈 결과."""
    if not ai_enabled() or not positions:
        return {}
    system = (
        "너는 보유 종목을 점검하는 애널리스트다. 사용자가 주는 JSON 수치만 근거로, 종목마다 계속 들고 갈지(hold) 팔지(sell) 판단한다. "
        "gain은 산 가격 대비 수익률, from_peak은 산 뒤 최고가 대비, r5·r20은 5·20일 수익률(모두 %), ma20은 20일 평균선 위/아래, "
        "rsi는 RSI(14), evidence는 근거 점수(0~100, 50 중립), recommended는 최신 추천에 들어 있는지, risk는 위험 종목 지정 여부, "
        "theme_flow는 그 테마의 흐름, market_weak는 대표 지수가 60일 평균선 아래인지다. "
        "추세가 살아 있고 근거가 유지되면 hold, 근거가 무너졌거나(추천 제외·평균선 아래·하락 흐름이 겹침) 위험이 뚜렷하면 sell이다. "
        "잠깐의 하락만으로 팔지 말고, 확실한 근거가 있을 때만 sell을 고른다. 자료에 없는 사실은 쓰지 않는다. "
        'JSON으로만 답한다: {"reviews": [{"symbol": "...", "action": "hold|sell", "reason": "근거 수치를 포함한 한국어 1문장"}]}'
    )
    context = {"market": market, "market_weak": weak_market, "positions": positions}
    try:
        answer = await chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))}],
            temperature=0.2, json_mode=True, timeout=AI_REVIEW_TIMEOUT,
        )
        parsed = json.loads(answer)
    except Exception:
        logger.exception("AI position review failed: %s", market)
        return {}
    allowed = {position["symbol"] for position in positions}
    out = {}
    for row in parsed.get("reviews") or []:
        if isinstance(row, dict) and row.get("symbol") in allowed and row.get("action") in {"hold", "sell"}:
            out[row["symbol"]] = {"action": row["action"], "reason": str(row.get("reason") or "").strip()[:200]}
    return out

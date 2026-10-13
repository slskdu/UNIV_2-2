import json
import logging
import re
from collections import Counter
from typing import Any

from .ai_client import ai_enabled, chat_completion

logger = logging.getLogger(__name__)

POSITIVE, NEGATIVE, NEUTRAL = "호재", "악재", "중립"
AI_TIMEOUT_SECONDS = 30.0

# 기사 제목에서 찾는 표현과 점수(+ 호재, - 악재). 긴 표현을 먼저 찾아 지운 뒤 짧은 표현을 찾으므로
# "적자 축소"가 "적자"로, "목표가 하향"이 "하향"으로 중복 집계되지 않습니다.
TONE_KEYWORDS: dict[str, int] = {
    # 문맥이 뒤집히는 복합 표현
    "적자 축소": 1, "적자축소": 1, "손실 축소": 1, "흑자전환": 2, "흑자 전환": 2,
    "적자전환": -2, "적자 전환": -2, "적자 확대": -2, "적자 지속": -1,
    "우려 해소": 1, "우려 완화": 1, "규제 완화": 1, "규제 강화": -1,
    "관세 인하": 1, "관세 면제": 1, "관세 유예": 1, "관세 부과": -1, "관세 인상": -1,
    "금리 인하": 1, "금리 인상": -1, "하락 마감": -1, "상승 마감": 1,
    "목표가 상향": 2, "목표주가 상향": 2, "목표가 하향": -2, "목표주가 하향": -2,
    "투자의견 상향": 2, "투자의견 하향": -2, "실적 부진": -2, "실적 개선": 2,
    "어닝 서프라이즈": 2, "어닝서프라이즈": 2, "깜짝 실적": 2, "어닝 쇼크": -2, "어닝쇼크": -2,
    "최대 실적": 2, "사상 최대": 2, "역대 최대": 2, "역대급 실적": 2, "호실적": 2,
    "신고가": 2, "신저가": -2, "상한가": 2, "하한가": -2,
    "순매수": 1, "순매도": -1, "자사주 매입": 1, "자사주 소각": 2,
    "공급계약": 1, "계약 체결": 1, "유상증자": -1, "무상증자": 1,
    "상장폐지": -2, "거래정지": -2, "압수수색": -2, "투자경고": -1, "투자주의": -1,
    "차익실현": -1, "피크아웃": -1, "사이드카": -1,
    # 단일 표현
    "급등": 2, "폭등": 2, "강세": 1, "상승": 1, "반등": 1, "돌파": 1, "호재": 2, "수혜": 1,
    "수주": 1, "흑자": 1, "호조": 1, "성장": 1, "승인": 1, "허가": 1, "상향": 1, "회복": 1,
    "급락": -2, "폭락": -2, "약세": -1, "하락": -1, "부진": -1, "악재": -2, "쇼크": -2,
    "적자": -1, "손실": -1, "하향": -1, "우려": -1, "둔화": -1, "감소": -1, "리콜": -2,
    "소송": -1, "제재": -1, "과징금": -2, "횡령": -2, "배임": -2, "파업": -1, "해킹": -2,
    "화재": -1, "경고": -1, "불확실": -1, "공매도": -1, "반토막": -2,
}
# 해외 종목의 영문 제목용. 소문자로 바꾼 제목에서 단어 단위로 찾습니다.
TONE_KEYWORDS_EN: dict[str, int] = {
    "record high": 2, "all-time high": 2, "price target raised": 2, "raises price target": 2,
    "price target cut": -2, "cuts price target": -2, "beats": 2, "beat estimates": 2, "tops estimates": 2,
    "misses": -2, "miss estimates": -2, "upgrade": 2, "upgraded": 2, "upgrades": 2,
    "downgrade": -2, "downgraded": -2, "downgrades": -2,
    "surge": 2, "surges": 2, "soar": 2, "soars": 2, "skyrocket": 2, "skyrockets": 2, "shoots up": 2, "jump": 1, "jumps": 1, "rally": 1, "rallies": 1,
    "gain": 1, "gains": 1, "rise": 1, "rises": 1, "climb": 1, "climbs": 1, "rebound": 1, "rebounds": 1,
    "approval": 1, "wins": 1, "buyback": 1, "outperform": 1, "bullish": 1,
    "plunge": -2, "plunges": -2, "tumble": -2, "tumbles": -2, "crash": -2, "crashes": -2,
    "slump": -1, "slumps": -1, "drop": -1, "drops": -1, "fall": -1, "falls": -1, "slide": -1, "slides": -1,
    "sink": -1, "sinks": -1, "decline": -1, "declines": -1, "lawsuit": -1, "probe": -1, "recall": -2,
    "layoffs": -1, "warning": -1, "warns": -1, "bearish": -1, "underperform": -1, "selloff": -1,
    "sell-off": -1, "delisting": -2, "bankruptcy": -2, "fraud": -2, "dilution": -1, "offering": -1,
}
_TONE_ORDER = sorted(TONE_KEYWORDS, key=len, reverse=True)
_TONE_ORDER_EN = sorted(TONE_KEYWORDS_EN, key=len, reverse=True)

# 사건 유형. 위에서부터 먼저 일치한 유형 하나를 붙입니다.
EVENT_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("리스크·사고", ("소송", "리콜", "횡령", "배임", "파업", "화재", "해킹", "압수수색", "사고", "과징금", "상장폐지", "거래정지", "lawsuit", "recall", "probe", "fraud")),
    ("실적", ("실적", "영업이익", "매출", "순이익", "어닝", "흑자", "적자", "분기", "earnings", "revenue", "quarter")),
    ("수주·계약", ("수주", "계약", "공급", "납품", "협약", "MOU")),
    ("증권가 의견", ("목표가", "목표주가", "투자의견", "리포트", "증권가", "전망", "price target", "analyst", "upgrade", "downgrade")),
    ("정책·규제", ("정부", "정책", "규제", "관세", "법안", "보조금", "제재", "특구", "금융위", "공정위")),
    ("투자·M&A", ("인수", "합병", "증설", "유상증자", "상장", "IPO", "투자 유치")),
    ("주주·지배구조", ("증여", "상속", "배당", "주주", "지분", "자사주", "경영권")),
    ("기술·신사업", ("개발", "출시", "신제품", "특허", "임상", "승인", "허가", "양산", "기술", "AI")),
    ("수급", ("외국인", "외인", "기관", "순매수", "순매도", "공매도", "연기금", "개미")),
    ("거시", ("금리", "환율", "유가", "인플레", "연준", "경기", "증시")),
    ("시세", ("급등", "급락", "상한가", "하한가", "신고가", "신저가", "강세", "약세", "상승", "하락", "반등", "특징주", "주가")),
]


CONCESSION_MARKERS = ("에도 불구", "에도", "불구하고", "지만", "는데도")


def _tone_score(text: str) -> tuple[int, list[str]]:
    score = 0
    matched: list[str] = []
    for keyword in _TONE_ORDER:
        if keyword in text:
            score += TONE_KEYWORDS[keyword]
            matched.append(keyword)
            text = text.replace(keyword, " ")
    lowered = text.lower()
    for keyword in _TONE_ORDER_EN:
        pattern = rf"(?<![a-z]){re.escape(keyword)}(?![a-z])"
        if re.search(pattern, lowered):
            score += TONE_KEYWORDS_EN[keyword]
            matched.append(keyword)
            lowered = re.sub(pattern, " ", lowered)
    return score, matched


def classify_title(title: str) -> dict[str, Any]:
    """제목 한 건을 호재/악재/중립과 사건 유형으로 분류합니다(규칙 기반)."""
    score, matched = _tone_score(title)
    # "역대급 실적에도 급락"처럼 양보 표현이 있으면 뒤쪽 절의 논조를 결론으로 봅니다.
    for marker in CONCESSION_MARKERS:
        if marker in title:
            tail_score, tail_matched = _tone_score(title.rsplit(marker, 1)[1])
            if tail_score:
                score, matched = tail_score, tail_matched
            break

    category = next(
        (name for name, keywords in EVENT_CATEGORIES if any(keyword in title or keyword in title.lower() for keyword in keywords)),
        "기타",
    )
    return {
        "tone": POSITIVE if score > 0 else NEGATIVE if score < 0 else NEUTRAL,
        "tone_score": score,
        "tone_keywords": matched,
        "category": category,
    }


def _classifiable_title(article: dict[str, Any]) -> str:
    # 분류 규칙은 한국어·영어 표현만 알기 때문에, 그 밖의 언어는 번역된 제목으로 분류합니다.
    if article.get("lang", "ko") not in {"ko", "en"} and article.get("title_ko"):
        return article["title_ko"]
    return article.get("title", "")


def classify_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**article, **classify_title(_classifiable_title(article))} for article in articles]


def build_digest(articles: list[dict[str, Any]], subject: str, days: int) -> dict[str, Any]:
    """분류된 기사 묶음의 호재/악재 비율과 사건 유형을 요약합니다."""
    counts = Counter(article["tone"] for article in articles)
    positive, negative, neutral = counts[POSITIVE], counts[NEGATIVE], counts[NEUTRAL]
    total = len(articles)
    balance = (positive - negative) / total if total else 0.0
    if total < 3:
        tone = "판단 보류"
    elif balance >= 0.2:
        tone = "호재 우위"
    elif balance <= -0.2:
        tone = "악재 우위"
    else:
        tone = "혼재"

    categories = Counter(article["category"] for article in articles).most_common(3)
    top_positive = [a["title"] for a in sorted(articles, key=lambda a: -a["tone_score"]) if a["tone"] == POSITIVE][:2]
    top_negative = [a["title"] for a in sorted(articles, key=lambda a: a["tone_score"]) if a["tone"] == NEGATIVE][:2]

    if not total:
        text = f"최근 {days}일간 {subject} 관련 기사를 찾지 못했습니다."
    else:
        category_text = ", ".join(f"{name} {count}건" for name, count in categories)
        text = (
            f"최근 {days}일 {subject} 관련 기사 {total}건 중 호재 {positive}건·악재 {negative}건·"
            f"중립 {neutral}건으로 기사 논조는 '{tone}'입니다. 주된 사건 유형은 {category_text}입니다."
        )
        if total < 3:
            text += " 기사 수가 적어 논조를 판단하기 어렵습니다."

    return {
        "article_count": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "balance": round(balance, 3),
        "tone": tone,
        "categories": [{"category": name, "count": count} for name, count in categories],
        "top_positive": top_positive,
        "top_negative": top_negative,
        "summary": text,
        "method": "rule",
        "caveat": "제목의 표현만으로 분류한 결과입니다. 본문 내용이나 주가 변동의 원인을 확정하지 않습니다.",
    }


def tone_price_signal(digest: dict[str, Any], change_rate: float | None, period_label: str) -> str | None:
    """기사 논조와 같은 기간 가격 방향이 어긋나는지 알려 줍니다."""
    if change_rate is None or digest["tone"] in {"판단 보류", "혼재"}:
        return None
    rate_text = f"{period_label} 등락률 {change_rate:+.2f}%"
    if digest["tone"] == "호재 우위" and change_rate < 0:
        return f"기사는 호재 우위인데 {rate_text}로 가격은 내렸습니다. 호재가 이미 반영됐거나 다른 요인이 작용했을 수 있습니다."
    if digest["tone"] == "악재 우위" and change_rate > 0:
        return f"기사는 악재 우위인데 {rate_text}로 가격은 올랐습니다. 악재가 선반영됐거나 반등 시도일 수 있습니다."
    return f"기사 논조({digest['tone']})와 {rate_text}의 방향이 일치합니다."


async def classify_and_digest(
    articles: list[dict[str, Any]],
    subject: str,
    days: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """규칙 기반 분류를 기본으로 하고, AI_API_KEY가 있으면 AI 분류·요약으로 보강합니다."""
    classified = classify_articles(articles)
    digest = build_digest(classified, subject, days)
    if not ai_enabled() or not classified:
        return classified, digest

    try:
        ai_result = await _ai_classify(classified, subject, days)
        labels = ai_result.get("labels", [])
        if len(labels) == len(classified) and all(label in {POSITIVE, NEGATIVE, NEUTRAL} for label in labels):
            classified = [
                {**article, "tone": label, "tone_score": {POSITIVE: 1, NEGATIVE: -1, NEUTRAL: 0}[label]}
                for article, label in zip(classified, labels)
            ]
            digest = build_digest(classified, subject, days)
        summary = str(ai_result.get("summary") or "").strip()
        if summary:
            digest["summary"] = summary
        digest["method"] = "ai"
    except Exception:
        logger.exception("AI news classification failed; using rule-based result")
    return classified, digest


async def _ai_classify(articles: list[dict[str, Any]], subject: str, days: int) -> dict[str, Any]:
    titles = [article.get("title_ko") or article["title"] for article in articles]
    prompt = (
        f"다음은 최근 {days}일간 '{subject}' 관련 기사 제목 목록입니다. 각 제목이 '{subject}' 주가에 "
        "호재인지 악재인지 중립인지 제목에 드러난 사실만으로 분류하고, 전체 흐름을 한국어 3~4문장으로 "
        "요약하세요. 제목에 없는 사실이나 전망은 만들지 말고, 기사가 주가 변동의 원인이라고 단정하지 마세요. "
        'JSON으로만 답하세요: {"labels": ["호재"|"악재"|"중립", ...제목 순서대로], "summary": "..."}\n'
        + json.dumps(titles, ensure_ascii=False)
    )
    answer = await chat_completion(
        [{"role": "user", "content": prompt}], temperature=0.1, json_mode=True, timeout=AI_TIMEOUT_SECONDS
    )
    return json.loads(answer)

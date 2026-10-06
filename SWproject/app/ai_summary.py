import json
from typing import Any

import httpx

from .config import get_settings


def fallback_summary(analysis: dict[str, Any]) -> str:
    insights = analysis.get("insights")
    if insights:
        interpretation = analysis.get("interpretation")
        if interpretation:
            signals = " ".join(item["assessment"] for item in interpretation["signals"])
            return (
                f"{interpretation['headline']}. {interpretation['summary']} "
                f"{signals} {interpretation['article_note']} {interpretation['caveat']}"
            )

        overview = insights["overview"]
        breadth = (
            f"{overview['advances']}개 상승, {overview['declines']}개 하락, "
            f"{overview['unchanged']}개 보합"
        )
        sectors = insights.get("sector_focus", [])
        strong = next((item for item in sectors if item["signal"] == "강세"), None)
        weak = next((item for item in sectors if item["signal"] == "주의"), None)

        def sector_text(item: dict[str, Any] | None) -> str:
            if not item:
                return "뚜렷한 분야 신호 없음"
            names = ", ".join(stock["name"] for stock in item["leaders"][:2]) or "대표 종목 없음"
            return (
                f"{item['sector']}({item['stock_count']}개, 평균 "
                f"{item['average_change_rate']:.2f}%, 거래대금 비중 "
                f"{item['trading_amount_share'] * 100:.1f}%, 대표 종목 {names})"
            )

        gainers = ", ".join(
            f"{item['name']} {item['change_rate']:+.2f}%"
            for item in insights.get("top_gainers", [])[:3]
        ) or "자료 없음"
        decliners = ", ".join(
            f"{item['name']} {item['change_rate']:+.2f}%"
            for item in insights.get("top_decliners", [])[:3]
        ) or "자료 없음"
        articles = analysis.get("related_articles", [])
        article_text = (
            "관련 기사: " + " / ".join(item["title"] for item in articles[:2])
            if articles
            else "선정 종목의 최근 관련 기사를 찾지 못했습니다."
        )
        return (
            f"{overview['stock_count']}개 종목 중 {breadth}이며 단순 평균 등락률은 "
            f"{overview['average_change_rate']:+.2f}%입니다. "
            f"강세 분야는 {sector_text(strong)}, 주의 분야는 {sector_text(weak)}입니다. "
            f"상승 상위는 {gainers}, 하락 상위는 {decliners}입니다. "
            f"{article_text} 기사는 관련 종목의 참고 자료이며 등락 원인으로 단정하지 않습니다. "
            "랭킹 기반 참고 분석으로 투자 판단을 대신하지 않습니다."
        )

    hot = analysis.get("top_sectors", [])[:2]
    caution = analysis.get("caution_sectors", [])[:2]
    hot_text = ", ".join(item["sector"] for item in hot) or "뚜렷한 강세 분야 없음"
    caution_text = ", ".join(item["sector"] for item in caution) or "뚜렷한 주의 분야 없음"
    return f"현재 강세로 분류된 분야는 {hot_text}이며, 상대적으로 주의할 분야는 {caution_text}입니다. 이는 랭킹 데이터 기반 참고용 분석이며 투자 판단을 대신하지 않습니다."


def brief_summary(analysis: dict[str, Any]) -> str:
    interpretation = analysis.get("interpretation")
    if interpretation:
        insights = analysis.get("insights", {})
        overview = insights.get("overview", {})
        market_label = {"all": "통합", "kr": "국장", "us": "미장"}.get(
            overview.get("market"), "시장"
        )
        sectors = insights.get("sector_focus", [])
        strong = next((item["sector"] for item in sectors if item["signal"] == "강세"), "없음")
        weak = next((item["sector"] for item in sectors if item["signal"] == "주의"), "없음")
        return (
            f"{market_label}: {interpretation['headline']}. "
            f"상승 {overview.get('advances', 0)}/{overview.get('stock_count', 0)}개 "
            f"({overview.get('advance_ratio', 0) * 100:.1f}%), 평균 "
            f"{overview.get('average_change_rate', 0):+.2f}%, 중앙값 "
            f"{overview.get('median_change_rate', 0):+.2f}%. "
            f"강세 {strong} · 주의 {weak}."
        )

    insights = analysis.get("insights")
    if not insights:
        return fallback_summary(analysis)

    overview = insights["overview"]
    strong = next((item for item in insights.get("sector_focus", []) if item["signal"] == "강세"), None)
    weak = next((item for item in insights.get("sector_focus", []) if item["signal"] == "주의"), None)
    strong_text = strong["sector"] if strong else "뚜렷한 강세 분야 없음"
    weak_text = weak["sector"] if weak else "뚜렷한 주의 분야 없음"
    article_count = len(analysis.get("related_articles", []))
    article_text = f"관련 기사 {article_count}건" if article_count else "관련 기사 없음"
    return (
        f"{overview['stock_count']}개 중 상승 {overview['advances']}개, 하락 {overview['declines']}개 "
        f"(평균 {overview['average_change_rate']:+.2f}%). 강세 {strong_text} · 주의 {weak_text} · {article_text}."
    )


async def create_summary(analysis: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.ai_api_key:
        return fallback_summary(analysis)

    context = {
        "market": analysis.get("market"),
        "interpretation": analysis.get("interpretation"),
        "insights": analysis.get("insights"),
        "related_articles": analysis.get("related_articles", []),
        "top_sectors": analysis.get("top_sectors", [])[:3],
        "caution_sectors": analysis.get("caution_sectors", [])[:3],
    }
    prompt = (
        "다음 자료를 단순 낭독하지 말고 한국어로 5~7문장 시장 해석을 작성하세요. "
        "먼저 시장의 방향과 상승 확산도를 평가하고, 평균과 중앙값 괴리 및 거래대금 집중도가 "
        "그 판단을 어떻게 뒷받침하거나 약화하는지 설명하세요. 강세/주의 분야는 종목 수가 적으면 "
        "표본 한계를 밝혀 주세요. 관련 기사는 관측된 사실과 연결되는 참고 맥락으로만 언급하고 "
        "주가 변동의 직접 원인이라고 단정하지 마세요. 데이터 밖의 전망은 만들지 말고, 마지막에 "
        "랭킹 표본 기반 분석이며 투자 판단을 대신하지 않는다고 적으세요.\n"
        + json.dumps(context, ensure_ascii=False)
    )
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(
            f"{settings.ai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            json={
                "model": settings.ai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()
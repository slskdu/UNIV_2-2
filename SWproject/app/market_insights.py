from typing import Any


def interpret_market(
    insights: dict[str, Any],
    related_articles: list[dict[str, Any]],
) -> dict[str, Any]:
    overview = insights["overview"]
    advances = overview["advances"]
    declines = overview["declines"]
    advance_ratio = overview["advance_ratio"]
    average_change = overview["average_change_rate"]
    median_change = overview["median_change_rate"]
    top_five_share = overview["top_five_trading_amount_share"]
    top_five_share_by_market = overview.get("top_five_trading_amount_share_by_market", {})
    sector_focus = insights.get("sector_focus", [])
    strong_sector = next((item for item in sector_focus if item["signal"] == "강세"), None)
    weak_sector = next((item for item in sector_focus if item["signal"] == "주의"), None)

    if average_change > 0 and median_change > 0 and advance_ratio >= 0.65:
        breadth = "상승이 다수 종목으로 확산된 폭넓은 강세"
        breadth_detail = "상승 종목 비중과 평균 등락이 함께 양수입니다."
    elif average_change > 0 and advance_ratio >= 0.5:
        breadth = "상승 우위지만 종목별 체감은 엇갈림"
        if median_change <= 0:
            breadth_detail = "평균은 플러스지만 중앙값이 0 이하라 대표 종목의 체감은 약하고, 일부 큰 상승의 영향일 수 있습니다."
        else:
            breadth_detail = "평균과 중앙값은 플러스지만 상승 종목이 근소 우위라 상승 확산은 강하지 않습니다."
    elif average_change > 0:
        breadth = "평균은 상승이나 상승 종목 확산은 제한적"
        breadth_detail = "평균 등락이 일부 큰 상승 종목의 영향을 받았을 수 있습니다."
    elif advance_ratio < 0.5:
        breadth = "하락 종목이 우세한 약세"
        breadth_detail = "하락 종목 비중과 평균 등락을 함께 보면 시장 전반의 압력이 우세합니다."
    else:
        breadth = "방향성이 혼재된 장세"
        breadth_detail = "평균 등락과 상승 종목 비중이 같은 방향으로 정렬되지 않았습니다."

    mean_median_gap = average_change - median_change
    if mean_median_gap >= 2:
        dispersion = "평균이 중앙값보다 높아 소수 급등 종목이 전체 평균을 끌어올렸을 가능성이 큽니다."
    elif mean_median_gap <= -2:
        dispersion = "평균이 중앙값보다 낮아 일부 큰 하락 종목이 전체 평균을 끌어내렸을 가능성이 큽니다."
    else:
        dispersion = "평균과 중앙값의 차이가 작아 극단적인 종목이 평균을 크게 왜곡하지는 않았습니다."

    if len(top_five_share_by_market) > 1:
        turnover_evidence = " · ".join(
            f"{market} {share * 100:.1f}%"
            for market, share in sorted(top_five_share_by_market.items())
        )
        turnover = f"통합 거래대금은 통화별로 분리해 비교했습니다. 시장별 상위 5개 비중은 {turnover_evidence}입니다."
    else:
        top_five_share = top_five_share or 0.0
        turnover_evidence = f"상위 5개 거래대금 비중 {top_five_share * 100:.1f}%"
        if top_five_share >= 0.5:
            turnover = f"거래대금 상위 5개 종목 비중이 {top_five_share * 100:.1f}%로 거래가 소수 종목에 집중돼 있습니다."
        elif top_five_share >= 0.3:
            turnover = f"거래대금 상위 5개 종목 비중은 {top_five_share * 100:.1f}%로 일부 종목에 관심이 모여 있습니다."
        else:
            turnover = f"거래대금 상위 5개 종목 비중은 {top_five_share * 100:.1f}%로 비교적 분산돼 있습니다."

    sector_notes = []
    for item in (strong_sector, weak_sector):
        if not item:
            continue
        if item["stock_count"] <= 2:
            sector_notes.append(
                f"{item['sector']} 분야 신호는 {item['stock_count']}개 종목만으로 형성돼 대표성이 낮을 수 있습니다."
            )
        elif item["positive_ratio"] >= 0.7:
            sector_notes.append(f"{item['sector']} 분야는 상승 종목 비율이 높아 방향성이 비교적 일치합니다.")
        elif item["positive_ratio"] <= 0.3:
            sector_notes.append(f"{item['sector']} 분야는 상승 종목 비율이 낮아 약세가 넓게 나타납니다.")
        else:
            sector_notes.append(f"{item['sector']} 분야는 종목별 등락이 엇갈려 신호의 확산이 제한적입니다.")

    headline = breadth
    if average_change > 0 and median_change <= 0:
        headline = "평균은 상승했지만 대표 종목의 체감은 약한 장세"
    elif average_change > 0 and mean_median_gap >= 2:
        headline = "평균은 상승했지만 상승세가 소수 종목에 치우친 장세"
    elif average_change < 0 and advance_ratio >= 0.5:
        headline = "일부 종목의 큰 하락이 평균을 끌어내린 혼조 장세"

    signals = [
        {"label": "시장 폭", "assessment": breadth_detail, "evidence": f"상승 {advances}개 / 하락 {declines}개, 상승 비율 {advance_ratio * 100:.1f}%"},
        {"label": "등락 쏠림", "assessment": dispersion, "evidence": f"평균 {average_change:+.2f}% · 중앙값 {median_change:+.2f}%"},
        {"label": "거래 집중도", "assessment": turnover, "evidence": turnover_evidence},
        *(
            [{"label": "분야 확산", "assessment": " ".join(sector_notes), "evidence": f"강세 {strong_sector['sector']} · 주의 {weak_sector['sector']}"}]
            if strong_sector and weak_sector and sector_notes
            else []
        ),
    ]
    summary = " ".join([dispersion, turnover, *sector_notes])
    if related_articles:
        article_note = f"선정 종목 관련 기사 {len(related_articles)}건을 찾았습니다. 기사 제목은 참고 자료이며 등락 원인으로 단정하지 않습니다."
    else:
        article_note = "선정 종목 관련 기사를 찾지 못해 가격·거래 데이터만으로 해석했습니다."

    return {
        "headline": headline,
        "summary": summary,
        "signals": signals,
        "article_note": article_note,
        "caveat": "랭킹과 기사 검색에 포함된 표본 기반 해석이며, 시장 전체 또는 주가의 원인을 확정하지 않습니다.",
    }

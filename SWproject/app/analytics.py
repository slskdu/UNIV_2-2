from collections import defaultdict
from typing import Any

from .config import get_settings
from .sector_map import sector_weights


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _rows_for_market(data: dict[str, Any], market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ranking in data.values():
        sources = [ranking] if market != "all" else [ranking.get("kr", {}), ranking.get("us", {})]
        for source in sources:
            if isinstance(source, dict):
                rows.extend(source.get("result", {}).get("rankings", []))
    unique = {f"{row.get('symbol')}:{market}": row for row in rows if isinstance(row, dict)}
    return list(unique.values())


def analyze_rankings(data: dict[str, Any], market: str = "all") -> dict[str, Any]:
    rows = _rows_for_market(data, market)
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "rows": set(),
        "weighted_count": 0.0,
        "change_sum": 0.0,
        "amount": 0.0,
        "positive": 0.0,
        "negative": 0.0,
    })
    for row in rows:
        change = _number(row.get("price", {}).get("changeRate")) * 100
        amount = _number(row.get("tradingAmount"))
        for sector, weight in sector_weights(row).items():
            group = groups[sector]
            group["rows"].add(row.get("symbol"))
            group["weighted_count"] += weight
            group["change_sum"] += change * weight
            group["amount"] += amount * weight
            group["positive"] += weight if change > 0 else 0
            group["negative"] += weight if change < 0 else 0

    total_amount = sum(_number(row.get("tradingAmount")) for row in rows) or 1
    sectors = []
    for sector, group in groups.items():
        weighted_count = group["weighted_count"] or 1
        amount = group["amount"]
        positive = group["positive"]
        negative = group["negative"]
        average_change = group["change_sum"] / weighted_count
        breadth = (positive - negative) / weighted_count
        amount_share = amount / total_amount
        score = average_change * 0.5 + breadth * 10 * 0.3 + amount_share * 10 * 0.2
        sectors.append({
            "sector": sector,
            "stock_count": len(group["rows"]),
            "weighted_stock_count": round(weighted_count, 3),
            "average_change_rate": round(average_change, 2),
            "positive_count": round(positive, 3),
            "negative_count": round(negative, 3),
            "positive_ratio": round(positive / weighted_count, 3),
            "trading_amount": round(amount),
            "trading_amount_share": round(amount_share, 4),
            "score": round(score, 2),
        })

    sectors.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(sectors, start=1):
        item["rank"] = index
        item["signal"] = "강세" if item["score"] > 1 else "주의" if item["score"] < -1 else "중립"

    return {
        "market": market,
        "stock_count": len(rows),
        "sector_count": len(sectors),
        "top_sectors": sectors[:5],
        "caution_sectors": sorted(sectors, key=lambda item: item["score"])[:5],
        "sectors": sectors,
        "weighted_analysis": get_settings().weighted_analysis_enabled,
        "note": "가중 분석이 활성화되면 복합 기업은 sector_map.json의 가중치로 여러 분야에 배분됩니다. 미분류 종목은 별도 집계됩니다.",
    }
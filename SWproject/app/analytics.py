from collections import defaultdict
from statistics import median
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
        "amount_by_market": defaultdict(float),
        "positive": 0.0,
        "negative": 0.0,
    })
    total_amount_by_market: dict[str, float] = defaultdict(float)
    for row in rows:
        change = _number(row.get("price", {}).get("changeRate")) * 100
        amount = _number(row.get("tradingAmount"))
        market_key = row.get("marketCountry") or ("US" if row.get("currency") == "USD" else "KR")
        total_amount_by_market[market_key] += amount
        for sector, weight in sector_weights(row).items():
            group = groups[sector]
            group["rows"].add(row.get("symbol"))
            group["weighted_count"] += weight
            group["change_sum"] += change * weight
            group["amount"] += amount * weight
            group["amount_by_market"][market_key] += amount * weight
            group["positive"] += weight if change > 0 else 0
            group["negative"] += weight if change < 0 else 0

    sectors = []
    for sector, group in groups.items():
        weighted_count = group["weighted_count"] or 1
        amount = group["amount"]
        positive = group["positive"]
        negative = group["negative"]
        average_change = group["change_sum"] / weighted_count
        breadth = (positive - negative) / weighted_count
        market_shares = [
            group["amount_by_market"].get(market_key, 0.0) / total
            for market_key, total in total_amount_by_market.items()
            if total
        ]
        amount_share = sum(market_shares) / len(market_shares) if market_shares else 0.0
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
        "note": (
            "통합 거래대금 비중은 KR/US 시장별 비중의 동일 가중 평균이며 원화와 달러를 직접 합산하지 않습니다. "
            if market == "all"
            else ""
        ) + "가중 분석이 활성화되면 복합 기업은 sector_map.json의 가중치로 여러 분야에 배분됩니다. 미분류 종목은 별도 집계됩니다.",
    }


def build_detailed_insights(
    data: dict[str, Any],
    market: str,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    rows = _rows_for_market(data, market)

    def change_rate(row: dict[str, Any]) -> float:
        return _number(row.get("price", {}).get("changeRate")) * 100

    def stock_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "market": row.get("marketCountry") or ("US" if row.get("currency") == "USD" else "KR"),
            "symbol": row.get("symbol"),
            "name": row.get("name") or row.get("symbol"),
            "sector": row.get("industry") or "분야 확인 필요",
            "change_rate": round(change_rate(row), 2),
            "trading_amount": round(_number(row.get("tradingAmount"))),
        }

    summaries = [stock_summary(row) for row in rows]
    advances = sum(1 for row in summaries if row["change_rate"] > 0)
    declines = sum(1 for row in summaries if row["change_rate"] < 0)
    unchanged = len(summaries) - advances - declines
    average_change = (
        sum(row["change_rate"] for row in summaries) / len(summaries)
        if summaries
        else 0.0
    )
    median_change = median(row["change_rate"] for row in summaries) if summaries else 0.0
    total_trading_amount = sum(row["trading_amount"] for row in summaries)
    trading_amounts_by_market: dict[str, list[int]] = defaultdict(list)
    for row in summaries:
        trading_amounts_by_market[row["market"]].append(row["trading_amount"])
    total_trading_amount_by_market = {
        market_key: sum(amounts)
        for market_key, amounts in trading_amounts_by_market.items()
    }
    top_five_share_by_market = {
        market_key: round(
            sum(sorted(amounts, reverse=True)[:5]) / sum(amounts),
            4,
        ) if sum(amounts) else 0
        for market_key, amounts in trading_amounts_by_market.items()
    }

    members_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, summary in zip(rows, summaries):
        for sector in sector_weights(row):
            members_by_sector[sector].append(summary)

    sector_focus = []
    for signal, sectors in (
        ("강세", analysis.get("top_sectors", [])[:3]),
        ("주의", analysis.get("caution_sectors", [])[:3]),
    ):
        for sector in sectors:
            members = members_by_sector.get(sector["sector"], [])
            leaders = sorted(members, key=lambda item: item["change_rate"], reverse=True)[:2]
            laggards = sorted(members, key=lambda item: item["change_rate"])[:2]
            sector_focus.append({
                "signal": signal,
                "sector": sector["sector"],
                "stock_count": sector["stock_count"],
                "average_change_rate": sector["average_change_rate"],
                "positive_ratio": sector["positive_ratio"],
                "trading_amount_share": sector["trading_amount_share"],
                "leaders": leaders,
                "laggards": laggards,
            })

    top_gainers = sorted(summaries, key=lambda item: item["change_rate"], reverse=True)[:5]
    top_decliners = sorted(summaries, key=lambda item: item["change_rate"])[:5]
    most_traded = sorted(summaries, key=lambda item: item["trading_amount"], reverse=True)[:5]

    news_targets = []
    seen_targets = set()
    for row in top_gainers[:2] + top_decliners[:2] + most_traded[:1]:
        key = (row["market"], row["symbol"])
        if key not in seen_targets:
            news_targets.append({
                "market": row["market"],
                "symbol": row["symbol"],
                "name": row["name"],
            })
            seen_targets.add(key)

    return {
        "overview": {
            "market": market,
            "stock_count": len(summaries),
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "advance_ratio": round(advances / len(summaries), 3) if summaries else 0,
            "average_change_rate": round(average_change, 2),
            "median_change_rate": round(median_change, 2),
            "total_trading_amount": total_trading_amount if len(total_trading_amount_by_market) <= 1 else None,
            "total_trading_amount_by_market": total_trading_amount_by_market,
            "top_five_trading_amount_share": (
                next(iter(top_five_share_by_market.values()))
                if len(top_five_share_by_market) == 1
                else None
            ),
            "top_five_trading_amount_share_by_market": top_five_share_by_market,
        },
        "top_gainers": top_gainers,
        "top_decliners": top_decliners,
        "most_traded": most_traded,
        "sector_focus": sector_focus,
        "news_targets": news_targets,
    }
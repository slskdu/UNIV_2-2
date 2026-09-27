from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..config import get_settings
from ..analytics import analyze_rankings
from ..ai_summary import create_summary
from ..redis_client import load_rankings

router = APIRouter(prefix="/api/v1", tags=["market"])
page_router = APIRouter(tags=["web"])
settings = get_settings()

MARKET_TREND_PAGE = """<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Market Trend</title>
    <style>
        :root { color-scheme: light; font-family: "Malgun Gothic", "Segoe UI", sans-serif; }
        * { box-sizing: border-box; }
        body { margin: 0; background: #f3f5f8; color: #172033; }
        main { width: min(1240px, calc(100% - 32px)); margin: 32px auto; }
        header { display: flex; justify-content: space-between; align-items: end; gap: 16px; margin-bottom: 22px; }
        h1 { margin: 0 0 8px; font-size: clamp(24px, 4vw, 38px); letter-spacing: -1px; }
        .subtitle, #updated { color: #667085; font-size: 14px; }
        .toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        button { border: 0; border-radius: 8px; background: #172033; color: white; padding: 10px 15px; cursor: pointer; font-weight: 700; }
        button:hover { background: #2d3a52; }
        .tabs { display: flex; gap: 8px; border-bottom: 1px solid #dfe3ea; margin-bottom: 14px; }
        .tab { border-radius: 8px 8px 0 0; background: transparent; color: #667085; border-bottom: 3px solid transparent; }
        .tab.active { color: #172033; border-bottom-color: #e34b4b; }
        .panel { overflow: hidden; background: white; border: 1px solid #e1e5eb; border-radius: 10px; box-shadow: 0 8px 24px rgba(23,32,51,.06); }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; min-width: 820px; border-collapse: collapse; font-size: 14px; }
        th { background: #f8fafc; color: #667085; font-size: 12px; text-align: right; white-space: nowrap; }
        th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) { text-align: left; }
        th, td { padding: 13px 16px; border-bottom: 1px solid #edf0f4; }
        tbody tr:hover { background: #fff8f8; }
        .rank { color: #e34b4b; font-weight: 800; }
        .name { font-weight: 700; }
        .code { color: #8a93a3; font-size: 12px; }
        .positive { color: #d33d4d; font-weight: 700; }
        .negative { color: #2672c8; font-weight: 700; }
        .empty, .error { padding: 44px 20px; text-align: center; color: #667085; }
        .error { color: #c03645; }
        .analysis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 0 0 18px; }
        .analysis-card { background: white; border: 1px solid #e1e5eb; border-radius: 10px; padding: 18px; }
        .analysis-card h2 { margin: 0 0 12px; font-size: 16px; }
        .analysis-card.hot h2 { color: #c43e4d; }
        .analysis-card.caution h2 { color: #2672c8; }
        .analysis-list { display: grid; gap: 9px; }
        .analysis-item { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px solid #edf0f4; padding-bottom: 9px; }
        .analysis-item:last-child { border-bottom: 0; padding-bottom: 0; }
        .analysis-meta { color: #667085; font-size: 12px; text-align: right; }
        .analysis-note { grid-column: 1 / -1; color: #667085; font-size: 12px; }
        @media (max-width: 680px) { .analysis { grid-template-columns: 1fr; } .analysis-note { grid-column: auto; } }
        @media (max-width: 680px) { main { width: min(100% - 20px, 1240px); margin: 20px auto; } header { align-items: start; flex-direction: column; } }
    </style>
</head>
<body>
    <main>
        <header>
            <div><h1>Market Trend</h1><div class="subtitle">토스증권 랭킹 데이터</div></div>
            <div class="toolbar"><span id="updated">불러오는 중...</span><button id="refresh">새로고침</button></div>
        </header>
        <nav class="tabs market-tabs" aria-label="시장 선택">
            <button class="tab market-tab active" data-market="all">통합</button>
            <button class="tab market-tab" data-market="kr">국장</button>
            <button class="tab market-tab" data-market="us">미장</button>
        </nav>
        <nav class="tabs" aria-label="랭킹 종류">
            <button class="tab active" data-kind="rising">상승률</button>
            <button class="tab" data-kind="falling">하락률</button>
            <button class="tab" data-kind="trading_value">거래대금</button>
        </nav>
        <section class="analysis" aria-live="polite">
            <article class="analysis-card hot"><h2>현재 강세 분야</h2><div id="hot-sectors" class="analysis-list"><div class="empty">분석 중...</div></div></article>
            <article class="analysis-card caution"><h2>주의 분야</h2><div id="caution-sectors" class="analysis-list"><div class="empty">분석 중...</div></div></article>
            <div id="analysis-note" class="analysis-note"></div>
        </section>
        <section class="panel" style="margin-bottom: 18px;"><div class="table-wrap"><table>
            <thead><tr><th>분야 순위</th><th>세부 분야</th><th>종목 수</th><th>평균 등락률</th><th>상승 비율</th><th>거래대금 비중</th><th>분야 점수</th><th>신호</th></tr></thead>
            <tbody id="sector-rows"><tr><td colspan="8" class="empty">분야 분석을 불러오는 중입니다.</td></tr></tbody>
        </table></div></section>
        <section class="panel" style="margin-bottom: 18px; padding: 18px;"><strong>AI 데이터 요약</strong><p id="ai-summary" style="margin: 10px 0 0; color: #667085;">버튼을 누르면 현재 선택 시장을 분석합니다.</p><button id="ai-button" style="margin-top: 12px;">AI 요약 생성</button></section>
        <section class="panel"><div class="table-wrap"><table>
            <thead><tr><th>시장</th><th>순위</th><th>종목/심볼</th><th>종목코드</th><th>분야</th><th>현재가</th><th>등락률</th><th>거래량</th><th>거래대금</th></tr></thead>
            <tbody id="rows"><tr><td colspan="9" class="empty">데이터를 불러오는 중입니다.</td></tr></tbody>
        </table></div></section>
    </main>
    <script>
        let payload = null;
        let selected = "rising";
        let selectedMarket = "all";
        const aliases = {
            rank: ["rank", "ranking", "rankNo", "順位"], name: ["name", "stockName", "companyName", "itemName"],
            code: ["code", "stockCode", "itemCode", "symbol"], price: ["price", "currentPrice", "tradePrice", "closePrice"],
            rate: ["changeRate", "changePercent", "fluctuationRate", "rate", "change"], volume: ["volume", "tradingVolume"],
            amount: ["tradingAmount", "tradingValue", "tradeAmount", "amount"]
        };
        const labels = { rising: "상승률", falling: "하락률", trading_value: "거래대금" };
        function findValue(row, keys) {
            const source = Object.keys(row || {}).reduce((map, key) => (map[key.toLowerCase()] = row[key], map), {});
            for (const key of keys) if (source[key.toLowerCase()] !== undefined) return source[key.toLowerCase()];
            return "-";
        }
        function findArray(value) {
            if (Array.isArray(value)) return value;
            if (!value || typeof value !== "object") return [];
            for (const child of Object.values(value)) { const found = findArray(child); if (found.length) return found; }
            return [];
        }
        function rowsFor(kind) {
            const source = payload?.data?.[kind];
            if (selectedMarket !== "all") return source?.result?.rankings || findArray(source);
            const rows = ["kr", "us"].flatMap(market =>
                (source?.[market]?.result?.rankings || []).map(row => ({
                    ...row,
                    __market: market.toUpperCase(),
                }))
            );
            const metric = row => {
                if (kind === "trading_value") return Number(row.tradingAmount || 0);
                return Number(row.price?.changeRate || 0);
            };
            return rows.sort((left, right) => {
                const difference = metric(right) - metric(left);
                return kind === "falling" ? -difference : difference;
            });
        }
        function rankedTime() {
            if (selectedMarket === "all") {
                const source = payload?.data?.rising || {};
                return `KR 기준 ${source.kr?.result?.rankedAt || "-"} · US 기준 ${source.us?.result?.rankedAt || "-"}`;
            }
            return payload?.data?.rising?.result?.rankedAt || "-";
        }
        function fieldValue(row, field, keys) {
            if (field === "price" && row?.price && typeof row.price === "object") return row.price.lastPrice ?? "-";
            if (field === "rate" && row?.price && typeof row.price === "object") return row.price.changeRate ?? "-";
            return findValue(row, keys);
        }
        function numberText(value) { if (value === "-" || value === null || value === "") return "-"; const number = Number(String(value).replaceAll(",", "")); return Number.isFinite(number) ? number.toLocaleString("ko-KR") : String(value); }
        function rateText(value) {
            if (value === "-") return value;
            const text = String(value);
            if (text.includes("%")) return text;
            const number = Number(text);
            return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : text;
        }
        function analysisItem(item) {
            return `<div class="analysis-item"><strong>${item.sector}</strong><span class="analysis-meta">평균 ${item.average_change_rate.toFixed(2)}% · 거래대금 ${(item.trading_amount_share * 100).toFixed(1)}%</span></div>`;
        }
        function renderAnalysis(analysis) {
            document.getElementById("hot-sectors").innerHTML = analysis.top_sectors.length ? analysis.top_sectors.map(analysisItem).join("") : '<div class="empty">분야 데이터가 없습니다.</div>';
            document.getElementById("caution-sectors").innerHTML = analysis.caution_sectors.length ? analysis.caution_sectors.map(analysisItem).join("") : '<div class="empty">분야 데이터가 없습니다.</div>';
            document.getElementById("analysis-note").textContent = `${analysis.stock_count}개 종목 · ${analysis.sector_count}개 분야 · ${analysis.note}`;
            document.getElementById("sector-rows").innerHTML = analysis.sectors.map(item => `<tr><td class="rank">${item.rank}</td><td class="name">${item.sector}</td><td>${item.stock_count}</td><td>${item.average_change_rate.toFixed(2)}%</td><td>${(item.positive_ratio * 100).toFixed(1)}%</td><td>${(item.trading_amount_share * 100).toFixed(1)}%</td><td>${item.score.toFixed(2)}</td><td>${item.signal}</td></tr>`).join("");
        }
        function render() {
            const body = document.getElementById("rows"); const rows = rowsFor(selected);
            if (!rows.length) { body.innerHTML = `<tr><td colspan="9" class="empty">${labels[selected]} 데이터가 없습니다.</td></tr>`; return; }
            body.innerHTML = rows.map((row, index) => {
                const rankValue = findValue(row, aliases.rank);
                const rank = selectedMarket === "all" ? index + 1 : rankValue === "-" ? index + 1 : rankValue;
                const rate = fieldValue(row, "rate", aliases.rate); const numericRate = Number(String(rate).replace(/[^0-9.-]/g, ""));
                const rateClass = numericRate > 0 ? "positive" : numericRate < 0 ? "negative" : "";
                const symbol = findValue(row, ["symbol"]); const displayName = findValue(row, aliases.name);
                const code = displayName === symbol ? "-" : findValue(row, aliases.code);
                const marketLabel = row.__market || selectedMarket.toUpperCase();
                const sector = row.industry || (row.sector && row.market ? `${row.sector} · ${row.market}` : row.sector || row.market || "-");
                return `<tr><td class="code">${marketLabel}</td><td class="rank">${rank}</td><td class="name">${displayName}</td><td class="code">${code}</td><td>${sector}</td><td>${numberText(fieldValue(row, "price", aliases.price))}</td><td class="${rateClass}">${rateText(rate)}</td><td>${numberText(fieldValue(row, "volume", aliases.volume))}</td><td>${numberText(fieldValue(row, "amount", aliases.amount))}</td></tr>`;
            }).join("");
        }
        async function load() {
            const body = document.getElementById("rows"); body.innerHTML = '<tr><td colspan="9" class="empty">데이터를 불러오는 중입니다.</td></tr>';
            try {
                const [response, analysisResponse] = await Promise.all([
                    fetch(`/api/v1/market-trend?market=${selectedMarket}`),
                    fetch(`/api/v1/market-analysis?market=${selectedMarket}`),
                ]);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                payload = await response.json();
                render();
                if (analysisResponse.ok) renderAnalysis(await analysisResponse.json());
                document.getElementById("updated").textContent = `랭킹 기준 ${rankedTime()}`;
            }
            catch (error) { body.innerHTML = `<tr><td colspan="9" class="error">데이터를 불러오지 못했습니다. (${error.message})</td></tr>`; document.getElementById("updated").textContent = "갱신 실패"; }
        }
        document.querySelectorAll(".market-tab").forEach(button => button.addEventListener("click", () => { selectedMarket = button.dataset.market; document.querySelectorAll(".market-tab").forEach(tab => tab.classList.toggle("active", tab === button)); load(); }));
        document.querySelectorAll(".tab:not(.market-tab)").forEach(button => button.addEventListener("click", () => { selected = button.dataset.kind; document.querySelectorAll(".tab:not(.market-tab)").forEach(tab => tab.classList.toggle("active", tab === button)); render(); }));
        document.getElementById("ai-button").addEventListener("click", async () => {
            const output = document.getElementById("ai-summary");
            output.textContent = "분석 중...";
            try {
                const response = await fetch(`/api/v1/market-analysis?market=${selectedMarket}&ai=true`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                output.textContent = (await response.json()).ai_summary;
            } catch (error) { output.textContent = `AI 분석을 불러오지 못했습니다. (${error.message})`; }
        });
        document.getElementById("refresh").addEventListener("click", load); load(); setInterval(load, 20000);
    </script>
</body>
</html>"""


@router.get("/market-trend")
async def market_trend(
    market: Literal["all", "kr", "us"] = Query("all")
):
    """외부 API를 호출하지 않고 Redis의 최신 랭킹 데이터만 반환합니다."""
    if market == "all":
        kr_data, us_data = await load_rankings("kr"), await load_rankings("us")
        data = {
            name: {
                "kr": kr_data[name],
                "us": us_data[name],
            }
            for name in kr_data
        }
    else:
        data = await load_rankings(market)
    if not any(
        value is not None and (
            not isinstance(value, dict)
            or any(part is not None for part in value.values())
        )
        for value in data.values()
    ):
        raise HTTPException(status_code=503, detail="아직 수집된 랭킹 데이터가 없습니다.")

    return {
        "data": data,
        "source": "redis",
        "cache_ttl_seconds": settings.cache_ttl_seconds,
    }


@router.get("/market-analysis")
async def market_analysis(
    market: Literal["all", "kr", "us"] = Query("all"),
    ai: bool = Query(False),
):
    if market == "all":
        kr_data, us_data = await load_rankings("kr"), await load_rankings("us")
        data = {
            name: {"kr": kr_data[name], "us": us_data[name]}
            for name in kr_data
        }
    else:
        data = await load_rankings(market)
    analysis = analyze_rankings(data, market)
    if analysis["stock_count"] == 0:
        raise HTTPException(status_code=503, detail="분석할 랭킹 데이터가 없습니다.")
    if ai:
        try:
            analysis["ai_summary"] = await create_summary(analysis)
        except Exception:
            analysis["ai_summary"] = "AI 요약을 생성하지 못했습니다. 수치 기반 분석을 확인해 주세요."
    return analysis


@page_router.get("/market-trend", response_class=HTMLResponse)
async def market_trend_page():
    return HTMLResponse(MARKET_TREND_PAGE)
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..config import get_settings
from ..analytics import analyze_rankings, build_detailed_insights
from ..ai_summary import brief_summary, create_summary
from ..market_insights import interpret_market
from ..news import fetch_related_news
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
        th { background: #f8fafc; color: #667085; font-size: 12px; white-space: nowrap; }
        th, td { padding: 13px 16px; border-bottom: 1px solid #edf0f4; text-align: right; vertical-align: middle; }
        .sector-table th:nth-child(2), .sector-table td:nth-child(2),
        .ranking-table th:first-child, .ranking-table td:first-child,
        .ranking-table th:nth-child(3), .ranking-table td:nth-child(3),
        .ranking-table th:nth-child(4), .ranking-table td:nth-child(4),
        .ranking-table th:nth-child(5), .ranking-table td:nth-child(5) { text-align: left; }
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
        [hidden] { display: none !important; }
        .view-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
        .view-tab { background: white; color: #475467; border: 1px solid #dfe3ea; padding: 11px 18px; }
        .view-tab:hover { background: #f3f5f8; }
        .view-tab.active, .view-tab.active:hover { background: #172033; color: white; border-color: #172033; }
        .pick-group { display: grid; grid-template-columns: 92px 1fr; gap: 8px; align-items: start; margin-bottom: 8px; }
        .pick-group > span { color: #667085; font-size: 12px; font-weight: 700; padding-top: 9px; }
        button.pick-link.active { background: #172033; color: white; }
        button.pick-link.active .code { color: #cfd5df; }
        .briefing-headline { margin: 0 0 10px; font-size: clamp(18px, 2.4vw, 24px); letter-spacing: -0.5px; }
        .briefing-summary { margin: 0 0 12px; font-size: 15px; line-height: 1.75; }
        .briefing-points { margin: 0; padding-left: 20px; display: grid; gap: 6px; font-size: 14px; line-height: 1.6; }
        .briefing-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .briefing-card { border: 1px solid #e1e5eb; border-radius: 10px; padding: 14px; }
        .briefing-card h4 { margin: 0 0 8px; font-size: 14px; }
        .briefing-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 0; border-bottom: 1px solid #edf0f4; }
        .briefing-row:last-child { border-bottom: 0; }
        .stat.clickable:hover, .briefing-row.clickable:hover { background: #fff8f8; }
        @media (max-width: 680px) { .briefing-grid { grid-template-columns: 1fr; } }
        .section-head { display: flex; justify-content: space-between; align-items: end; gap: 12px; flex-wrap: wrap; padding: 18px 18px 12px; }
        .section-title { margin: 0 0 4px; font-size: 16px; }
        .form-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        input[type=text], input[type=date], select { border: 1px solid #d0d5dd; border-radius: 8px; padding: 9px 10px; font: inherit; background: white; color: inherit; }
        input[type=text] { width: 130px; }
        button.ghost { background: #eef1f5; color: #172033; padding: 8px 11px; }
        button.ghost:hover { background: #dfe3ea; }
        .pad { padding: 0 18px 18px; }
        .pad h3 { margin: 18px 0 8px; font-size: 14px; }
        .chip { display: inline-block; border-radius: 999px; padding: 3px 9px; font-size: 12px; font-weight: 700; background: #eef1f5; color: #475467; white-space: nowrap; }
        .chip.pos { background: #fdecee; color: #c43e4d; }
        .chip.neg { background: #e8f1fb; color: #2672c8; }
        .chip-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 12px 0; }
        .stat { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }
        .stat small { display: block; color: #667085; font-size: 12px; margin-bottom: 3px; }
        .stat strong { font-size: 15px; }
        .chart { width: 100%; height: 220px; display: block; }
        .clickable { cursor: pointer; }
        .flow-table { min-width: 720px; }
        .flow-table th:first-child, .flow-table td:first-child, .flow-table th:last-child, .flow-table td:last-child { text-align: left; }
        .flow-table tr.selected { background: #fff1f1; }
        .news-list { list-style: none; padding: 0 6px 0 0; margin: 0; display: grid; gap: 9px; max-height: 720px; overflow-y: auto; }
        .article-summary { margin-top: 6px; padding: 9px 11px; background: #f8fafc; border-radius: 6px; font-size: 13px; line-height: 1.6; color: #344054; }
        button.summary-button { padding: 2px 9px; font-size: 12px; border-radius: 999px; margin-left: 6px; }
        button.news-filter { border-radius: 999px; padding: 5px 12px; font-size: 12px; }
        button.news-filter.active { background: #172033; color: white; }
        .news-list li { border-bottom: 1px solid #edf0f4; padding-bottom: 9px; font-size: 14px; }
        .news-list a { color: #172033; text-decoration: none; font-weight: 600; }
        .news-list a:hover { text-decoration: underline; }
        .signal { background: #f8fafc; border-left: 3px solid #172033; border-radius: 4px; padding: 10px 12px; margin: 10px 0; font-size: 14px; }
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
        <nav class="view-tabs" aria-label="보기 선택">
            <button class="view-tab active" data-view-target="briefing">시장 분석</button>
            <button class="view-tab" data-view-target="ranking">시장 랭킹</button>
            <button class="view-tab" data-view-target="sector">분야 흐름·기사</button>
            <button class="view-tab" data-view-target="history">기간별 주가 흐름</button>
        </nav>
        <section class="panel" id="briefing-panel" data-view="briefing" style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">시장 분석 <span class="code" id="briefing-updated"></span></h2><div class="subtitle">지수 흐름 · 분야 흐름 · 기사 논조 · 랭킹 표본을 종합한 해석입니다.</div></div>
                <div class="form-row"><select id="briefing-market" aria-label="분석 시장"><option value="all">통합(비교)</option><option value="kr" selected>국장</option><option value="us">미장</option></select><button id="briefing-refresh" type="button">다시 분석</button></div>
            </div>
            <div class="pad" id="briefing-body"><p class="empty">시장 분석을 불러오는 중입니다. 10초쯤 걸립니다.</p></div>
        </section>
        <nav class="tabs market-tabs" data-view="ranking" aria-label="시장 선택">
            <button class="tab market-tab active" data-market="all">통합</button>
            <button class="tab market-tab" data-market="kr">국장</button>
            <button class="tab market-tab" data-market="us">미장</button>
        </nav>
        <nav class="tabs" data-view="ranking" aria-label="랭킹 종류">
            <button class="tab active" data-kind="rising">상승률</button>
            <button class="tab" data-kind="falling">하락률</button>
            <button class="tab" data-kind="trading_value">거래대금</button>
        </nav>
        <section class="analysis" data-view="ranking" aria-live="polite">
            <article class="analysis-card hot"><h2>현재 강세 분야</h2><div id="hot-sectors" class="analysis-list"><div class="empty">분석 중...</div></div></article>
            <article class="analysis-card caution"><h2>주의 분야</h2><div id="caution-sectors" class="analysis-list"><div class="empty">분석 중...</div></div></article>
            <div id="analysis-note" class="analysis-note"></div>
        </section>
        <section class="panel" data-view="ranking" style="margin-bottom: 18px;"><div class="table-wrap"><table class="ranking-table">
            <thead><tr><th>시장</th><th>순위</th><th>종목명</th><th>종목코드</th><th>분야</th><th>현재가</th><th>등락률</th><th>거래량</th><th>거래대금</th></tr></thead>
            <tbody id="rows"><tr><td colspan="9" class="empty">데이터를 불러오는 중입니다.</td></tr></tbody>
        </table></div></section>
        <section class="panel" id="history-panel" data-view="history" hidden style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">기간별 주가 흐름</h2><div class="subtitle">아래 목록에서 종목을 누르면 바로 볼 수 있습니다. 목록에 없는 종목은 종목코드·티커로 조회합니다.</div></div>
                <form id="history-form" class="form-row">
                    <input id="history-symbol" type="text" value="KOSPI" maxlength="15" aria-label="종목코드 또는 티커">
                    <input id="history-start" type="date" aria-label="시작일"><input id="history-end" type="date" aria-label="종료일">
                    <button type="submit">조회</button>
                    <button type="button" class="ghost range-button" data-days="30">1개월</button><button type="button" class="ghost range-button" data-days="90">3개월</button><button type="button" class="ghost range-button" data-days="180">6개월</button><button type="button" class="ghost range-button" data-days="365">1년</button>
                </form>
            </div>
            <div class="pad"><div id="quick-picks" style="margin-bottom: 14px;"></div><div id="history-result" class="empty">조회할 종목과 기간을 선택하세요.</div><div id="history-news"></div></div>
        </section>
        <section class="panel" id="sector-flow-panel" data-view="sector" hidden style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">분야 흐름 <span class="code" id="flow-updated"></span></h2><div class="subtitle">토스증권 공식 섹터(대분류) 기준. 분야 행을 누르면 표 아래에 기사(호재/악재 분류)와 구성 종목이 열립니다.</div></div>
                <div class="form-row"><select id="flow-market" aria-label="분야 흐름 시장"><option value="kr">국장</option><option value="us">미장</option></select>
                <select id="news-days" aria-label="기사 기간"><option value="1">기사 1일</option><option value="7" selected>기사 7일</option><option value="30">기사 30일</option></select></div>
            </div>
            <div class="pad" style="padding-bottom: 6px;"><div id="flow-summary" class="signal">분야 흐름을 불러오는 중입니다.</div></div>
            <div class="table-wrap"><table class="flow-table">
                <thead><tr><th>분야</th><th>1일</th><th>1주</th><th>1개월</th><th>3개월</th><th>거래대금</th><th>흐름 단계</th></tr></thead>
                <tbody id="flow-rows"><tr><td colspan="7" class="empty">불러오는 중입니다. 서버 시작 직후에는 10초쯤 걸립니다.</td></tr></tbody>
            </table></div>
            <div class="pad" id="sector-report"></div>
        </section>
        <section class="panel" data-view="ranking" style="margin-bottom: 18px;"><div class="table-wrap"><table class="sector-table">
            <thead><tr><th>분야 순위</th><th>세부 분야</th><th>종목 수</th><th>평균 등락률</th><th>상승 비율</th><th>거래대금 비중</th><th>분야 점수</th><th>신호</th></tr></thead>
            <tbody id="sector-rows"><tr><td colspan="8" class="empty">분야 분석을 불러오는 중입니다.</td></tr></tbody>
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
        function sourceRank(row) {
            const rank = Number(row.rank);
            return Number.isFinite(rank) ? rank : Number.MAX_SAFE_INTEGER;
        }
        function findValue(row, keys) {
            const source = Object.keys(row || {}).reduce((map, key) => (map[key.toLowerCase()] = row[key], map), {});
            for (const key of keys) {
                const value = source[key.toLowerCase()];
                if (value !== undefined && value !== null && !(typeof value === "string" && !value.trim())) return value;
            }
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
            const rows = selectedMarket === "all"
                ? ["kr", "us"].flatMap(market =>
                    (source?.[market]?.result?.rankings || []).map(row => ({
                        ...row,
                        __market: market.toUpperCase(),
                    }))
                )
                : source?.result?.rankings || findArray(source);

            if (kind === "trading_value") {
                if (selectedMarket === "all") {
                    return rows.sort((left, right) =>
                        sourceRank(left) - sourceRank(right)
                        || String(left.__market).localeCompare(String(right.__market))
                    );
                }
                return rows.sort((left, right) =>
                    Number(right.tradingAmount || 0) - Number(left.tradingAmount || 0)
                    || sourceRank(left) - sourceRank(right)
                );
            }

            const direction = kind === "falling" ? 1 : -1;
            return rows.sort((left, right) => {
                const leftRate = Number(left.price?.changeRate || 0);
                const rightRate = Number(right.price?.changeRate || 0);
                return (leftRate - rightRate) * direction || sourceRank(left) - sourceRank(right);
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
        function priceText(value, row) {
            const text = numberText(value);
            if (text === "-") return text;
            const market = row.__market || selectedMarket.toUpperCase();
            const currency = row.currency || (market === "KR" ? "KRW" : market === "US" ? "USD" : "");
            if (currency === "KRW") return `${text}원`;
            if (currency === "USD") return `$${text}`;
            return `${text} ${currency}`;
        }
        function rateText(value) {
            if (value === "-") return value;
            const text = String(value);
            if (text.includes("%")) return text;
            const number = Number(text);
            return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : text;
        }
        function escapeHtml(value) {
            const replacements = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
            return String(value ?? "").replace(/[&<>"']/g, character => {
                if (character.charCodeAt(0) === 34) return "&quot;";
                if (character.charCodeAt(0) === 39) return "&#39;";
                return replacements[character];
            });
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
        function renderQuickPicks() {
            const root = document.getElementById("quick-picks");
            if (!root) return;
            const current = document.getElementById("history-symbol").value.trim().toUpperCase();
            const pick = (symbol, label, detail) => `<button type="button" class="ghost pick-link${String(symbol).toUpperCase() === current ? " active" : ""}" data-symbol="${escapeHtml(symbol)}">${escapeHtml(label)}${detail ? ` <span class="code">${detail}</span>` : ""}</button>`;
            const group = (title, items) => items ? `<div class="pick-group"><span>${title}</span><div class="chip-row">${items}</div></div>` : "";
            const stocks = kind => rowsFor(kind).slice(0, 10).filter(row => row.symbol).map(row => {
                const name = findValue(row, aliases.name);
                const rate = Number(row.price?.changeRate);
                const detail = Number.isFinite(rate) ? `<span class="${rate > 0 ? "positive" : rate < 0 ? "negative" : ""}">${rate > 0 ? "+" : ""}${(rate * 100).toFixed(2)}%</span>` : "";
                return pick(row.symbol, name === "-" ? row.symbol : name, detail);
            }).join("");
            const marketLabel = { all: "통합", kr: "국장", us: "미장" }[selectedMarket];
            const marketSelect = `<select id="pick-market" aria-label="목록 시장">${["all", "kr", "us"].map(market => `<option value="${market}"${market === selectedMarket ? " selected" : ""}>${{ all: "통합", kr: "국장", us: "미장" }[market]} 랭킹</option>`).join("")}</select>`;
            root.innerHTML = group("지수", pick("KOSPI", "코스피") + pick("KOSDAQ", "코스닥") + marketSelect)
                + group(`거래대금 상위<br>(${marketLabel})`, stocks("trading_value"))
                + group(`상승률 상위<br>(${marketLabel})`, stocks("rising"))
                + group(`하락률 상위<br>(${marketLabel})`, stocks("falling"));
        }
        function render() {
            renderQuickPicks();
            const body = document.getElementById("rows"); const rows = rowsFor(selected);
            if (!rows.length) { body.innerHTML = `<tr><td colspan="9" class="empty">${labels[selected]} 데이터가 없습니다.</td></tr>`; return; }
            body.innerHTML = rows.map((row, index) => {
                const rank = index + 1;
                const rate = fieldValue(row, "rate", aliases.rate); const numericRate = Number(String(rate).replace(/[^0-9.-]/g, ""));
                const rateClass = numericRate > 0 ? "positive" : numericRate < 0 ? "negative" : "";
                const symbol = findValue(row, ["symbol"]); const displayName = findValue(row, aliases.name);
                const marketLabel = row.__market || selectedMarket.toUpperCase();
                const sector = row.industry || (row.sector && row.market ? `${row.sector} · ${row.market}` : row.sector || row.market || "-");
                const sectorValues = [sector, row.sector, row.industry, row.market, row.securityType];
                const stockName = displayName !== "-" && !sectorValues.includes(displayName) ? displayName : symbol;
                const code = stockName === symbol ? "-" : findValue(row, aliases.code);
                return `<tr class="clickable" data-symbol="${escapeHtml(symbol)}" title="기간별 주가 흐름 보기"><td class="code">${marketLabel}</td><td class="rank">${rank}</td><td class="name">${stockName}</td><td class="code">${code}</td><td>${sector}</td><td>${priceText(fieldValue(row, "price", aliases.price), row)}</td><td class="${rateClass}">${rateText(rate)}</td><td>${numberText(fieldValue(row, "volume", aliases.volume))}</td><td>${numberText(fieldValue(row, "amount", aliases.amount))}</td></tr>`;
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
        document.getElementById("refresh").addEventListener("click", load); load(); setInterval(load, 20000);

        // ---- 기간별 주가 흐름 · 분야 흐름 · 기사 분류 ----
        let historyRequest = 0;
        let sectorRequest = 0;
        let selectedSector = null;
        async function fetchJson(url) {
            const response = await fetch(url);
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
            return body;
        }
        function isoDate(date) {
            return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
        }
        function setRange(days) {
            const end = new Date(); const start = new Date(); start.setDate(end.getDate() - days);
            document.getElementById("history-start").value = isoDate(start);
            document.getElementById("history-end").value = isoDate(end);
        }
        function rangeQuery() {
            return `start=${encodeURIComponent(document.getElementById("history-start").value)}&end=${encodeURIComponent(document.getElementById("history-end").value)}`;
        }
        function signedRate(value) {
            if (value === null || value === undefined) return "-";
            return `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
        }
        function rateClassOf(value) { return value > 0 ? "positive" : value < 0 ? "negative" : ""; }
        function rateCell(value) { return `<td class="${rateClassOf(value)}">${signedRate(value)}</td>`; }
        function rateChip(label, value) {
            return `<span class="chip ${value > 0 ? "pos" : value < 0 ? "neg" : ""}">${escapeHtml(label)} ${signedRate(value)}</span>`;
        }
        function amountText(value, currency) {
            if (value === null || value === undefined) return "-";
            const number = Number(value);
            if (currency === "USD") return `$${number.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
            if (number >= 1e12) return `${(number / 1e12).toFixed(1)}조원`;
            if (number >= 1e8) return `${Math.round(number / 1e8).toLocaleString("ko-KR")}억원`;
            return `${Math.round(number).toLocaleString("ko-KR")}원`;
        }
        function priceLabel(value, currency) {
            const text = Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
            return currency === "KRW" ? `${text}원` : currency === "USD" ? `$${text}` : text;
        }
        function toneChip(tone) {
            return `<span class="chip ${tone === "호재" ? "pos" : tone === "악재" ? "neg" : ""}">${escapeHtml(tone)}</span>`;
        }
        function newsMarkup(articles) {
            if (!articles.length) return '<p class="analysis-meta" style="text-align:left">조건에 맞는 기사를 찾지 못했습니다.</p>';
            const count = tone => articles.filter(article => article.tone === tone).length;
            const countries = [...new Set(articles.map(article => article.country || "한국"))];
            const countryNote = countries.length > 1 ? `<div class="code" style="margin-bottom:8px">수집 국가: ${countries.map(country => `${escapeHtml(country)} ${articles.filter(article => (article.country || "한국") === country).length}건`).join(" · ")}</div>` : "";
            const filters = `${countryNote}<div class="chip-row news-filters" style="margin-bottom:10px">${[["", "전체", articles.length], ["호재", "호재", count("호재")], ["악재", "악재", count("악재")], ["중립", "중립", count("중립")]].map(([tone, label, total], index) => `<button type="button" class="ghost news-filter${index ? "" : " active"}" data-tone="${tone}">${label} ${total}</button>`).join("")}</div>`;
            return `${filters}<ul class="news-list">${articles.map(article => {
                const url = String(article.url || "");
                const safeUrl = url.startsWith("https://") ? escapeHtml(url) : "#";
                const similar = article.similar_count ? ` · 유사 기사 ${article.similar_count}건` : "";
                const evidence = article.tone_keywords?.length ? ` · 근거 표현: ${escapeHtml(article.tone_keywords.join(", "))}` : "";
                return `<li data-tone="${escapeHtml(article.tone)}" data-url="${safeUrl}"><div class="chip-row">${toneChip(article.tone)}<span class="chip">${escapeHtml(article.category)}</span><a href="${safeUrl}" target="_blank" rel="noopener noreferrer"${article.title_ko ? ` title="${escapeHtml(article.title)}"` : ""}>${escapeHtml(article.title_ko || article.title)}</a>${article.title_ko ? '<span class="chip">번역</span>' : ""}</div><div class="code" style="margin-top:4px">${article.country && article.country !== "한국" ? `<strong>${escapeHtml(article.country)}</strong> · ` : ""}${escapeHtml(article.publisher)} · ${escapeHtml(article.published_at)} · ${escapeHtml(article.matched)}${similar}${evidence}<button type="button" class="ghost summary-button">요약 보기</button></div><div class="article-summary" hidden></div></li>`;
            }).join("")}</ul>`;
        }
        async function loadSummary(item, toggle) {
            const box = item.querySelector(".article-summary"), button = item.querySelector(".summary-button");
            if (box.dataset.state) {
                if (toggle && box.dataset.state !== "loading") { box.hidden = !box.hidden; button.textContent = box.hidden ? "요약 보기" : "요약 접기"; }
                return;
            }
            box.dataset.state = "loading"; box.hidden = false; box.textContent = "기사 본문을 읽어 요약하는 중입니다."; button.textContent = "요약 접기";
            try {
                const data = await fetchJson(`/api/v1/article-summary?url=${encodeURIComponent(item.dataset.url)}`);
                if (data.ok) {
                    const origin = data.source === "ai" ? "AI 요약" : data.source === "body" ? "본문 추출 요약" : "매체 제공 요약";
                    const language = data.summary_ko || data.translated ? " · 번역" : data.lang === "en" ? " · 번역 실패, 원문 표시" : "";
                    box.innerHTML = `${escapeHtml(data.summary_ko || data.summary)} <span class="code">(${origin}${language})</span>`;
                    if (data.summary_ko) box.title = data.summary;
                } else { box.innerHTML = `<span class="code">${escapeHtml(data.reason)}</span>`; }
            } catch (error) { box.innerHTML = `<span class="code">요약을 불러오지 못했습니다. (${escapeHtml(error.message)})</span>`; }
            box.dataset.state = "done";
        }
        async function loadTopSummaries(root, limit = 8) {
            // 위쪽 기사부터 두 건씩 자동으로 요약합니다. 나머지는 '요약 보기'를 누를 때 가져옵니다.
            const queue = [...root.querySelectorAll(".news-list li[data-url]")].slice(0, limit);
            const worker = async () => { while (queue.length) { const item = queue.shift(); if (item.isConnected) await loadSummary(item, false); } };
            await Promise.all([worker(), worker()]);
        }
        function digestMarkup(digest, signal) {
            const counts = `<div class="chip-row" style="margin:8px 0"><span class="chip pos">호재 ${digest.positive}</span><span class="chip neg">악재 ${digest.negative}</span><span class="chip">중립 ${digest.neutral}</span><span class="chip">논조 ${escapeHtml(digest.tone)}</span><span class="chip">${digest.method === "ai" ? "AI 분류" : "규칙 기반 분류"}</span></div>`;
            return `<p style="margin:0">${escapeHtml(digest.summary)}</p>${counts}${signal ? `<div class="signal">${escapeHtml(signal)}</div>` : ""}<p class="analysis-meta" style="text-align:left;margin:0 0 10px">${escapeHtml(digest.caveat)}</p>`;
        }
        function mountChart(container, candles, currency, markers = []) {
            const width = 800, height = 220, left = 8, right = 8, top = 16, bottom = 24;
            const closes = candles.map(candle => candle.close);
            const min = Math.min(...closes), max = Math.max(...closes), span = max - min || 1;
            const x = index => left + (width - left - right) * index / Math.max(1, candles.length - 1);
            const y = value => top + (height - top - bottom) * (1 - (value - min) / span);
            const points = candles.map((candle, index) => `${x(index).toFixed(1)},${y(candle.close).toFixed(1)}`).join(" ");
            const color = closes[closes.length - 1] >= closes[0] ? "#d33d4d" : "#2672c8";
            const base = height - bottom;
            container.innerHTML = `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="종가 추이">
                <polygon points="${left},${base} ${points} ${width - right},${base}" fill="${color}" opacity="0.08"></polygon>
                <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"></polyline>
                ${markers.map(day => candles.findIndex(candle => candle.date === day)).filter(index => index >= 0).map(index => `<line x1="${x(index)}" x2="${x(index)}" y1="${top}" y2="${base}" stroke="#f79009" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"><title>권리 변동일</title></line>`).join("")}
                <line class="cursor" x1="0" x2="0" y1="${top}" y2="${base}" stroke="#98a2b3" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"></line>
            </svg><div class="code chart-caption" style="display:flex;justify-content:space-between"><span>${escapeHtml(candles[0].date)}</span><span class="chart-hover">최고 ${priceLabel(max, currency)} · 최저 ${priceLabel(min, currency)} (종가)</span><span>${escapeHtml(candles[candles.length - 1].date)}</span></div>`;
            const svg = container.querySelector("svg"), cursor = container.querySelector(".cursor"), hover = container.querySelector(".chart-hover");
            const rest = hover.textContent;
            svg.addEventListener("mousemove", event => {
                const box = svg.getBoundingClientRect();
                const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
                const index = Math.round(ratio * (candles.length - 1));
                cursor.setAttribute("x1", x(index)); cursor.setAttribute("x2", x(index)); cursor.setAttribute("visibility", "visible");
                hover.textContent = `${candles[index].date} · 종가 ${priceLabel(candles[index].close, currency)}`;
            });
            svg.addEventListener("mouseleave", () => { cursor.setAttribute("visibility", "hidden"); hover.textContent = rest; });
        }
        function trendMarkup(trend, currency) {
            const stat = (label, value, detail) => `<div class="stat"><small>${label}</small><strong>${value}</strong>${detail ? `<div class="code">${detail}</div>` : ""}</div>`;
            const segments = trend.segments.map(segment => `<span class="chip ${segment.direction === "상승" ? "pos" : segment.direction === "하락" ? "neg" : ""}" title="판정 기준 ±${segment.threshold}%">${escapeHtml(segment.start_date.slice(5))}~${escapeHtml(segment.end_date.slice(5))} ${escapeHtml(segment.direction)} ${signedRate(segment.change_rate)}</span>`).join("");
            return `<div class="stat-grid">
                ${stat("기간 등락률", `<span class="${rateClassOf(trend.change_rate)}">${signedRate(trend.change_rate)}</span>`, `${priceLabel(trend.start_close, currency)} → ${priceLabel(trend.end_close, currency)}`)}
                ${stat("흐름", escapeHtml(trend.flow), `${trend.trading_days}거래일 · 상승 ${trend.up_days}일 / 하락 ${trend.down_days}일`)}
                ${stat("최대 낙폭", `<span class="negative">${signedRate(trend.max_drawdown.rate)}</span>`, `${escapeHtml(trend.max_drawdown.peak_date)} → ${escapeHtml(trend.max_drawdown.trough_date)}`)}
                ${stat("고가 / 저가", `${priceLabel(trend.high.price, currency)} / ${priceLabel(trend.low.price, currency)}`, `${escapeHtml(trend.high.date)} / ${escapeHtml(trend.low.date)}`)}
                ${stat("일간 변동성", `${trend.daily_volatility.toFixed(2)}%`, `최대 상승 ${signedRate(trend.best_day.change_rate)} (${escapeHtml(trend.best_day.date)}) · 최대 하락 ${signedRate(trend.worst_day.change_rate)} (${escapeHtml(trend.worst_day.date)})`)}
            </div><div class="chip-row">${segments}</div><p class="analysis-meta" style="text-align:left">${escapeHtml(trend.note)}</p>`;
        }
        function noticeMarkup(data) {
            const warnings = (data.warnings || []).map(warning => `<span class="chip ${warning.severe ? "pos" : ""}" title="${escapeHtml(warning.start_date || "")}${warning.end_date ? ` ~ ${escapeHtml(warning.end_date)}` : ""}">⚠ ${escapeHtml(warning.label)}</span>`).join("");
            const adjustments = (data.adjustments || []).map(event => {
                const jump = event.kind === "other"
                    ? `원주가로는 그날 ${signedRate(event.raw_change_rate)}로 보이지만`
                    : `원주가로는 그날 ${signedRate(event.raw_change_rate)} ${event.raw_change_rate > 0 ? "급등" : "급락"}한 것처럼 보이지만`;
                return `<div class="signal" style="border-left-color:#f79009"><strong>${escapeHtml(event.date)} ${escapeHtml(event.label)}</strong> — ${jump} 주식 수가 바뀐 것일 뿐 가치 변화가 아닙니다. 이 화면은 이를 걸러낸 수정주가 기준이며, 그날의 실제 등락은 ${signedRate(event.adjusted_change_rate)}입니다.</div>`;
            }).join("");
            const reverse = (data.adjustments || []).some(event => event.kind === "reverse_split");
            const hint = reverse ? '<p class="analysis-meta" style="text-align:left;margin:0 0 8px">주식 병합은 주가가 낮아져 상장 유지 요건을 맞추려 할 때 자주 쓰입니다. 차트의 주황 점선이 병합일입니다.</p>' : "";
            return `${warnings ? `<div class="chip-row" style="margin:6px 0 10px">${warnings}</div>` : ""}${adjustments}${hint}`;
        }
        async function loadHistory() {
            const symbol = document.getElementById("history-symbol").value.trim();
            const result = document.getElementById("history-result"), news = document.getElementById("history-news");
            if (!symbol) return;
            const request = ++historyRequest;
            document.querySelectorAll(".pick-link").forEach(button => button.classList.toggle("active", button.dataset.symbol.toUpperCase() === symbol.toUpperCase()));
            result.className = "empty"; result.textContent = "불러오는 중입니다."; news.replaceChildren();
            try {
                const data = await fetchJson(`/api/v1/price-history?symbol=${encodeURIComponent(symbol)}&${rangeQuery()}`);
                if (request !== historyRequest) return;
                result.className = "";
                result.innerHTML = `<h3 style="margin-top:0">${escapeHtml(data.name)} <span class="code">${escapeHtml(data.symbol)}${data.market ? ` · ${escapeHtml(data.market)}` : ""}${data.price_basis ? ` · ${escapeHtml(data.price_basis)} 기준` : ""}</span></h3>${noticeMarkup(data)}<div class="chart-box"></div>${trendMarkup(data.trend, data.currency)}`;
                mountChart(result.querySelector(".chart-box"), data.candles, data.currency, (data.adjustments || []).map(event => event.date));
                if (data.market === "INDEX") { news.innerHTML = '<h3>관련 기사</h3><p class="analysis-meta" style="text-align:left">지수는 기사를 제공하지 않습니다. 종목코드(예: 005930)를 조회하거나 상단 분야 흐름·기사 탭에서 분야를 누르면 기사가 나옵니다.</p>'; return; }
                news.innerHTML = '<h3>관련 기사</h3><p class="analysis-meta" style="text-align:left">기사를 불러오는 중입니다.</p>';
                const days = document.getElementById("news-days").value;
                try {
                    const report = await fetchJson(`/api/v1/stock-news?symbol=${encodeURIComponent(data.symbol)}&days=${days}`);
                    if (request !== historyRequest) return;
                    news.innerHTML = `<h3>${escapeHtml(report.name)} 관련 기사 (최근 ${report.news_days}일)</h3>${digestMarkup(report.news_digest, report.tone_price_signal)}${newsMarkup(report.articles)}`;
                    loadTopSummaries(news);
                } catch (error) { if (request === historyRequest) news.innerHTML = `<h3>관련 기사</h3><p class="error" style="padding:10px 0">기사를 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`; }
            } catch (error) {
                if (request !== historyRequest) return;
                result.className = "error"; result.textContent = `주가 흐름을 불러오지 못했습니다. (${error.message})`;
            }
        }
        function flowMarket() { return document.getElementById("flow-market").value; }
        async function loadSectorFlow() {
            const rows = document.getElementById("flow-rows"), summary = document.getElementById("flow-summary");
            const market = flowMarket();
            try {
                const flow = await fetchJson(`/api/v1/sector-flow?market=${market}`);
                if (market !== flowMarket()) return;
                summary.textContent = flow.overview.summary;
                document.getElementById("flow-updated").textContent = `${flow.generated_at} 기준`;
                rows.innerHTML = flow.sectors.map(item => `<tr class="clickable${item.sector_id === selectedSector ? " selected" : ""}" data-sector="${escapeHtml(item.sector_id)}"><td class="name">${escapeHtml(item.name)}</td>${flow.durations.map(duration => rateCell(item.rates[duration])).join("")}<td>${amountText(item.trading_amount, item.currency)}</td><td><span class="chip ${item.flow.startsWith("상승 지속") || item.flow === "반등 전환" ? "pos" : item.flow.startsWith("하락 지속") || item.flow === "상승 후 꺾임" ? "neg" : ""}">${escapeHtml(item.flow)}</span></td></tr>`).join("") || '<tr><td colspan="7" class="empty">분야 자료가 없습니다.</td></tr>';
            } catch (error) {
                summary.textContent = `분야 흐름을 불러오지 못했습니다. (${error.message})`;
                rows.innerHTML = '<tr><td colspan="7" class="error">분야 흐름을 불러오지 못했습니다.</td></tr>';
            }
        }
        async function loadSectorReport(sectorId) {
            const root = document.getElementById("sector-report");
            const market = flowMarket(), days = document.getElementById("news-days").value;
            const request = ++sectorRequest;
            selectedSector = sectorId;
            document.querySelectorAll("#flow-rows tr").forEach(row => row.classList.toggle("selected", row.dataset.sector === sectorId));
            root.innerHTML = '<p class="empty">분야 기사와 구성 종목을 불러오는 중입니다.</p>';
            root.scrollIntoView({ behavior: "smooth", block: "start" });
            try {
                const report = await fetchJson(`/api/v1/sectors/${encodeURIComponent(sectorId)}/report?market=${market}&days=${days}`);
                if (request !== sectorRequest) return;
                const durationLabels = { "1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월", "6mo": "6개월", "1y": "1년" };
                const rates = Object.entries(report.rates).map(([duration, value]) => rateChip(durationLabels[duration] || duration, value)).join("");
                const children = report.children.length
                    ? `<h3>세부 분야 (오늘 등락률 순)</h3><div class="chip-row">${report.children.map(child => `<button type="button" class="ghost sector-link" data-sector="${escapeHtml(child.sector_id)}">${escapeHtml(child.name)} <span class="${rateClassOf(child.rates["1d"])}">${signedRate(child.rates["1d"])}</span> · ${escapeHtml(child.flow)}</button>`).join("")}</div>`
                    : "";
                const stocks = report.top_stocks.map(stock => `<button type="button" class="ghost stock-link" data-symbol="${escapeHtml(stock.symbol)}">${escapeHtml(stock.name)} <span class="code">${amountText(stock.trading_amount, "KRW")}</span></button>`).join("");
                root.innerHTML = `<h3 style="font-size:16px">${escapeHtml(report.name)} <span class="chip">${escapeHtml(report.flow)}</span></h3>
                    ${report.summary ? `<p class="analysis-meta" style="text-align:left;margin:0 0 8px">${escapeHtml(report.summary)}</p>` : ""}
                    <div class="chip-row">${rates}</div>${children}
                    <h3>시가총액 상위 종목 (전체 ${report.stock_count}개 · 거래대금 표시 · 누르면 주가 흐름)</h3><div class="chip-row">${stocks || "-"}</div>
                    <h3>지정 기간 분야 흐름</h3><div class="form-row"><input id="sector-start" type="date" aria-label="분야 흐름 시작일" value="${escapeHtml(document.getElementById("history-start").value)}"><input id="sector-end" type="date" aria-label="분야 흐름 종료일" value="${escapeHtml(document.getElementById("history-end").value)}"><button type="button" id="sector-trend-button" class="ghost">이 기간으로 계산</button></div><div id="sector-trend"></div>
                    <h3>기사 논조 (최근 ${report.news_days}일)</h3>${digestMarkup(report.news_digest, report.tone_price_signal)}${newsMarkup(report.articles)}`;
                loadTopSummaries(root);
            } catch (error) {
                if (request === sectorRequest) root.innerHTML = `<p class="error">분야 정보를 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`;
            }
        }
        async function loadSectorTrend() {
            const root = document.getElementById("sector-trend"), sectorId = selectedSector, request = sectorRequest;
            root.innerHTML = '<p class="analysis-meta" style="text-align:left">구성 종목 일봉을 불러오는 중입니다.</p>';
            try {
                const data = await fetchJson(`/api/v1/sectors/${encodeURIComponent(sectorId)}/trend?market=${flowMarket()}&start=${encodeURIComponent(document.getElementById("sector-start").value)}&end=${encodeURIComponent(document.getElementById("sector-end").value)}`);
                if (request !== sectorRequest) return;
                const members = data.members.map(member => rateChip(member.name || member.symbol, member.change_rate)).join("");
                root.innerHTML = `<div class="chart-box"></div>${trendMarkup(data.trend, null)}<div class="chip-row">${members}</div><p class="analysis-meta" style="text-align:left">${escapeHtml(data.note)}</p>`;
                mountChart(root.querySelector(".chart-box"), data.candles, null);
            } catch (error) { if (request === sectorRequest) root.innerHTML = `<p class="error" style="padding:10px 0">분야 흐름을 계산하지 못했습니다. (${escapeHtml(error.message)})</p>`; }
        }
        let briefingRequest = 0;
        const PERIOD_LABELS = { "1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월" };
        function periodChips(rates, keys = ["1d", "1w", "1mo", "3mo"]) {
            return keys.map(key => rateChip(PERIOD_LABELS[key], rates?.[key])).join("");
        }
        function flowChip(flow) {
            return `<span class="chip ${flow.startsWith("상승 지속") || flow === "반등 전환" ? "pos" : flow.startsWith("하락 지속") || flow === "상승 후 꺾임" ? "neg" : ""}">${escapeHtml(flow)}</span>`;
        }
        function renderCombinedBriefing(data) {
            const narrative = data.narrative, cross = data.cross;
            const gauges = data.gauges.map(gauge => `<div class="stat clickable" data-symbol="${escapeHtml(gauge.symbol)}" title="기간별 주가 흐름 보기"><small>${gauge.market === "KR" ? "국장" : "미장"} · ${escapeHtml(gauge.name)} · ${escapeHtml(gauge.date)}</small><strong>${priceLabel(gauge.close, gauge.currency)}</strong><div class="chip-row" style="margin:8px 0 6px">${periodChips(gauge.rates)}</div><div class="code">최근 3개월: ${escapeHtml(gauge.flow || "-")}</div></div>`).join("");
            const marketCard = (key, label) => {
                const market = data.markets[key];
                const brief = items => items.map(item => `${escapeHtml(item.name)} <span class="${rateClassOf(item.rates["1d"])}">${signedRate(item.rates["1d"])}</span>`).join(" · ");
                return `<div class="briefing-card"><h4>${label} <span class="code">대분류 ${market.sector_count}개 중 ${market.advancing}개 상승</span></h4><p style="margin:0 0 8px"><strong>${escapeHtml(market.headline)}</strong></p><p class="briefing-summary" style="font-size:14px">${escapeHtml(market.summary)}</p><div class="code">강한 분야: ${brief(market.strongest)}</div><div class="code" style="margin-top:4px">약한 분야: ${brief(market.weakest)}</div><button type="button" class="ghost" data-briefing-market="${key}" style="margin-top:10px">${label} 상세 분석 보기</button></div>`;
            };
            const crossCard = (key, tone) => {
                const items = cross.groups[key];
                const rows = items.map(item => `<div class="briefing-row clickable" data-sector="${escapeHtml(item.sector_id)}" data-market="${key === "us_only" ? "us" : "kr"}" title="분야 기사와 구성 종목 보기"><strong>${escapeHtml(item.name)}</strong><span class="chip-row">${rateChip("국장", item.kr.rates["1d"])}${rateChip("미장", item.us.rates["1d"])}</span></div>`).join("");
                return `<div class="briefing-card"><h4 class="${tone}">${escapeHtml(cross.labels[key])} <span class="code">${cross.counts[key]}개</span></h4>${rows || '<p class="analysis-meta" style="text-align:left;margin:0">해당 분야가 없습니다.</p>'}</div>`;
            };
            document.getElementById("briefing-updated").textContent = `${data.generated_at} 기준`;
            document.getElementById("briefing-body").innerHTML = `
                <h3 class="briefing-headline">${escapeHtml(narrative.headline)} <span class="chip">${narrative.method === "ai" ? "AI 분석" : "규칙 기반 분석"}</span></h3>
                <p class="briefing-summary">${escapeHtml(narrative.summary)}</p>
                <ul class="briefing-points">${narrative.points.map(point => `<li>${escapeHtml(point)}</li>`).join("")}</ul>
                <h3>지수 흐름</h3><div class="stat-grid" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr))">${gauges}</div>
                <h3>시장별 요약</h3><div class="briefing-grid">${marketCard("kr", "국장")}${marketCard("us", "미장")}</div>
                <h3>같은 분야, 두 시장 비교 <span class="code">1일 등락률 기준 · ${cross.compared}개 분야 중 ${(cross.same_direction_ratio * 100).toFixed(0)}%가 같은 방향</span></h3>
                <div class="briefing-grid">${crossCard("both_up", "positive")}${crossCard("both_down", "negative")}${crossCard("kr_only", "")}${crossCard("us_only", "")}</div>
                <p class="analysis-meta" style="text-align:left;margin-top:14px">${escapeHtml(data.caveat)}</p>`;
        }
        function renderBriefing(data) {
            if (data.market === "ALL") { renderCombinedBriefing(data); return; }
            const narrative = data.narrative, overview = data.sector_overview;
            const gauges = data.gauges.map(gauge => `<div class="stat clickable" data-symbol="${escapeHtml(gauge.symbol)}" title="기간별 주가 흐름 보기"><small>${escapeHtml(gauge.name)} · ${escapeHtml(gauge.date)}</small><strong>${priceLabel(gauge.close, gauge.currency)}</strong><div class="chip-row" style="margin:8px 0 6px">${periodChips(gauge.rates)}</div><div class="code">최근 3개월: ${escapeHtml(gauge.flow || "-")}${gauge.max_drawdown ? ` · 최대 낙폭 ${signedRate(gauge.max_drawdown)}` : ""}</div></div>`).join("");
            const sectorRow = item => `<div class="briefing-row clickable" data-sector="${escapeHtml(item.sector_id)}" title="분야 기사와 구성 종목 보기"><strong>${escapeHtml(item.name)}</strong><span class="chip-row">${periodChips(item.rates, ["1d", "1w", "1mo"])}${flowChip(item.flow)}</span></div>`;
            const turning = (label, items) => items.length ? `<div class="chip-row" style="margin-top:8px"><span class="code">${label}</span>${items.map(item => `<button type="button" class="ghost" data-sector="${escapeHtml(item.sector_id)}">${escapeHtml(item.name)} <span class="${rateClassOf(item.rates["1d"])}">${signedRate(item.rates["1d"])}</span></button>`).join("")}</div>` : "";
            const flowCounts = Object.entries(overview.flow_counts).sort((left, right) => right[1] - left[1]).map(([flow, count]) => `<span class="chip">${escapeHtml(flow)} ${count}</span>`).join("");
            const focus = data.focus_sectors.map(sector => {
                const articles = sector.articles.map(article => {
                    const url = String(article.url || "");
                    return `<li>${toneChip(article.tone)} <a href="${url.startsWith("https://") ? escapeHtml(url) : "#"}" target="_blank" rel="noopener noreferrer"${article.title_ko ? ` title="${escapeHtml(article.title)}"` : ""}>${escapeHtml(article.title_ko || article.title)}</a>${article.title_ko ? ' <span class="chip">번역</span>' : ""} <span class="code">${article.country && article.country !== "한국" ? `${escapeHtml(article.country)} · ` : ""}${escapeHtml(article.publisher)}</span></li>`;
                }).join("");
                const counts = sector.article_count
                    ? `<span class="chip pos">호재 ${sector.positive}</span><span class="chip neg">악재 ${sector.negative}</span><span class="chip">중립 ${sector.neutral}</span><span class="chip">논조 ${escapeHtml(sector.tone)}</span>`
                    : '<span class="chip">기사 없음</span>';
                return `<div class="briefing-card"><h4 class="clickable" data-sector="${escapeHtml(sector.sector_id)}">${escapeHtml(sector.name)} ${flowChip(sector.flow)} <span class="${rateClassOf(sector.rates["1d"])}">${signedRate(sector.rates["1d"])}</span></h4><div class="chip-row">${counts}</div>${sector.signal ? `<div class="signal">${escapeHtml(sector.signal)}</div>` : ""}${articles ? `<ul class="news-list" style="margin-top:10px">${articles}</ul>` : ""}<div class="code" style="margin-top:8px">검색 기준: ${escapeHtml(sector.news_basis.join(", "))}</div></div>`;
            }).join("");
            const interpretation = data.interpretation, sample = data.sample_overview, movers = data.movers;
            const moverRow = (label, items) => `<div class="chip-row" style="margin-top:8px"><span class="code">${label}</span>${items.slice(0, 5).map(stock => `<button type="button" class="ghost" data-symbol="${escapeHtml(stock.symbol)}">${escapeHtml(stock.name)} <span class="${rateClassOf(stock.change_rate)}">${signedRate(stock.change_rate)}</span></button>`).join("")}</div>`;
            const sampleMarkup = interpretation ? `<h3>랭킹 표본 해석 <span class="code">상승·하락·거래대금 상위 ${sample.stock_count}개 종목 기준</span></h3>
                <div class="briefing-card"><h4>${escapeHtml(interpretation.headline)}</h4><ul class="briefing-points">${interpretation.signals.map(signal => `<li><strong>${escapeHtml(signal.label)}</strong>: ${escapeHtml(signal.assessment)} <span class="code">(${escapeHtml(signal.evidence)})</span></li>`).join("")}</ul>
                ${moverRow("상승 상위", movers.top_gainers)}${moverRow("하락 상위", movers.top_decliners)}${moverRow("거래대금 상위", movers.most_traded)}</div>` : "";
            document.getElementById("briefing-updated").textContent = `${data.generated_at} 기준`;
            document.getElementById("briefing-body").innerHTML = `
                <h3 class="briefing-headline">${escapeHtml(narrative.headline)} <span class="chip">${narrative.method === "ai" ? "AI 분석" : "규칙 기반 분석"}</span></h3>
                <p class="briefing-summary">${escapeHtml(narrative.summary)}</p>
                <ul class="briefing-points">${narrative.points.map(point => `<li>${escapeHtml(point)}</li>`).join("")}</ul>
                <h3>지수 흐름</h3><div class="stat-grid" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr))">${gauges || '<p class="analysis-meta">지수 자료가 없습니다.</p>'}</div>
                <h3>분야 흐름 <span class="code">대분류 ${overview.sector_count}개 중 ${overview.advancing}개 상승</span></h3>
                <div class="chip-row" style="margin-bottom:10px">${flowCounts}</div>
                <div class="briefing-grid"><div class="briefing-card"><h4 class="positive">강한 분야</h4>${overview.strongest.map(sectorRow).join("")}</div><div class="briefing-card"><h4 class="negative">약한 분야</h4>${overview.weakest.map(sectorRow).join("")}</div></div>
                ${turning("반등 시도", overview.turning_up)}${turning("상승 뒤 약세", overview.turning_down)}
                <h3>주목 분야의 기사 논조 <span class="code">최근 ${data.news_days}일 · 제목 기준 규칙 분류</span></h3><div class="briefing-grid">${focus}</div>
                ${sampleMarkup}
                <p class="analysis-meta" style="text-align:left;margin-top:14px">${escapeHtml(data.caveat)}</p>`;
        }
        async function loadBriefing(refresh = false) {
            const body = document.getElementById("briefing-body"), market = document.getElementById("briefing-market").value;
            const request = ++briefingRequest;
            body.innerHTML = `<p class="empty">지수·분야·기사를 모아 분석하는 중입니다. ${market === "all" ? "두 시장을 차례로 분석해 30초 넘게 걸릴 수 있습니다." : "10~20초 걸릴 수 있습니다."}</p>`;
            try {
                const data = await fetchJson(`/api/v1/market-briefing?market=${market}${refresh ? "&refresh=true" : ""}`);
                if (request === briefingRequest) renderBriefing(data);
            } catch (error) {
                if (request === briefingRequest) body.innerHTML = `<p class="error">시장 분석을 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`;
            }
        }
        function openSector(sectorId, market) {
            const select = document.getElementById("flow-market");
            if (select.value !== market) { select.value = market; selectedSector = null; loadSectorFlow(); }
            setView("sector");
            loadSectorReport(sectorId);
        }
        document.getElementById("briefing-market").addEventListener("change", () => loadBriefing());
        document.getElementById("briefing-refresh").addEventListener("click", () => loadBriefing(true));
        document.getElementById("briefing-body").addEventListener("click", event => {
            if (event.target.closest("a")) return;
            const stock = event.target.closest("[data-symbol]"), sector = event.target.closest("[data-sector]"), detail = event.target.closest("[data-briefing-market]");
            const select = document.getElementById("briefing-market");
            if (detail) { select.value = detail.dataset.briefingMarket; loadBriefing(); }
            else if (stock) showHistory(stock.dataset.symbol);
            else if (sector) openSector(sector.dataset.sector, sector.dataset.market || (select.value === "us" ? "us" : "kr"));
        });
        function setView(view) {
            document.querySelectorAll("[data-view]").forEach(element => { element.hidden = element.dataset.view !== view; });
            document.querySelectorAll(".view-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.viewTarget === view));
        }
        function showHistory(symbol) {
            document.getElementById("history-symbol").value = symbol;
            setView("history");
            window.scrollTo({ top: 0, behavior: "smooth" });
            loadHistory();
        }
        document.querySelectorAll(".view-tab").forEach(tab => tab.addEventListener("click", () => setView(tab.dataset.viewTarget)));
        document.getElementById("quick-picks").addEventListener("change", event => {
            if (event.target.id === "pick-market") document.querySelector(`.market-tab[data-market="${event.target.value}"]`).click();
        });
        document.getElementById("quick-picks").addEventListener("click", event => {
            const button = event.target.closest(".pick-link");
            if (button) { document.getElementById("history-symbol").value = button.dataset.symbol; loadHistory(); }
        });
        document.addEventListener("click", event => {
            const summaryButton = event.target.closest(".summary-button");
            if (summaryButton) { loadSummary(summaryButton.closest("li"), true); return; }
            const filter = event.target.closest(".news-filter");
            if (!filter) return;
            filter.parentElement.querySelectorAll(".news-filter").forEach(button => button.classList.toggle("active", button === filter));
            filter.parentElement.nextElementSibling.querySelectorAll("li").forEach(item => { item.hidden = Boolean(filter.dataset.tone) && item.dataset.tone !== filter.dataset.tone; });
        });
        document.getElementById("history-form").addEventListener("submit", event => { event.preventDefault(); loadHistory(); });
        document.querySelectorAll(".range-button").forEach(button => button.addEventListener("click", () => { setRange(Number(button.dataset.days)); loadHistory(); }));
        document.getElementById("rows").addEventListener("click", event => { const row = event.target.closest("tr[data-symbol]"); if (row) showHistory(row.dataset.symbol); });
        document.getElementById("flow-rows").addEventListener("click", event => { const row = event.target.closest("tr[data-sector]"); if (row) loadSectorReport(row.dataset.sector); });
        document.getElementById("sector-report").addEventListener("click", event => {
            const stock = event.target.closest(".stock-link"), sector = event.target.closest(".sector-link");
            if (stock) showHistory(stock.dataset.symbol);
            else if (sector) loadSectorReport(sector.dataset.sector);
            else if (event.target.closest("#sector-trend-button")) loadSectorTrend();
        });
        document.getElementById("flow-market").addEventListener("change", () => { selectedSector = null; sectorRequest++; document.getElementById("sector-report").replaceChildren(); loadSectorFlow(); });
        document.getElementById("news-days").addEventListener("change", () => { if (selectedSector) loadSectorReport(selectedSector); });
        document.querySelectorAll(".market-tab").forEach(button => button.addEventListener("click", () => {
            const market = button.dataset.market === "us" ? "us" : "kr";
            if (market !== flowMarket()) { document.getElementById("flow-market").value = market; document.getElementById("flow-market").dispatchEvent(new Event("change")); }
        }));
        setView("briefing"); setRange(90); loadBriefing(); loadHistory(); loadSectorFlow(); setInterval(loadSectorFlow, 300000);
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
            insights = build_detailed_insights(data, market, analysis)
            news_targets = insights.pop("news_targets")
            analysis["insights"] = insights
            analysis["related_articles"] = await fetch_related_news(news_targets)
            analysis["interpretation"] = interpret_market(
                insights,
                analysis["related_articles"],
            )
            analysis["brief_summary"] = brief_summary(analysis)
            analysis["ai_summary"] = await create_summary(analysis)
        except Exception:
            analysis["ai_summary"] = "AI 요약을 생성하지 못했습니다. 수치 기반 분석을 확인해 주세요."
    return analysis


@page_router.get("/market-trend", response_class=HTMLResponse)
async def market_trend_page():
    return HTMLResponse(MARKET_TREND_PAGE)
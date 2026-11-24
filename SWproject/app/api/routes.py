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
        .caveat-box { background: #fff8eb; border: 1px solid #f5d9a8; border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.6; margin-bottom: 14px; }
        .theme-list { display: grid; gap: 12px; }
        .theme-card ul { margin: 6px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.6; }
        .theme-card .risks li { color: #b54708; }
        .theme-card.risk-card { border-color: #f4c7c3; background: #fffbfa; }
        .theme-card.risk-card .danger li { color: #b42318; }
        h3.risk-heading { color: #b42318; }
        .evidence-list { margin: 6px 0 0; padding-left: 0; list-style: none; font-size: 13px; line-height: 1.7; }
        .evidence-list .plus { color: #027a48; } .evidence-list .minus { color: #b42318; } .evidence-list .zero { color: #667085; }
        details.evidence summary { cursor: pointer; font-size: 12px; color: #475467; margin-top: 4px; }
        .sim-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .sim-table th, .sim-table td { padding: 6px 8px; border-bottom: 1px solid #edf0f4; text-align: right; }
        .sim-table th:first-child, .sim-table td:first-child { text-align: left; min-width: 150px; }
        .reason-line { display: block; margin-top: 2px; font-size: 11px; line-height: 1.4; color: #667085; white-space: normal; max-width: 320px; text-align: left; }
        .sim-verdict { border-radius: 10px; padding: 12px 14px; margin: 8px 0 12px; font-size: 15px; line-height: 1.6; }
        .sim-verdict.good { background: #ecfdf3; border: 1px solid #a6f4c5; color: #05603a; }
        .sim-verdict.bad { background: #fef3f2; border: 1px solid #fda29b; color: #912018; }
        .sim-verdict.mixed { background: #f2f4f7; border: 1px solid #d0d5dd; color: #344054; }
        .sim-big { font-size: 22px; font-weight: 700; display: block; margin-top: 2px; }
        .sim-bars { display: grid; gap: 6px; margin: 8px 0; }
        .sim-bar { display: grid; grid-template-columns: minmax(120px, 190px) 1fr 72px; gap: 8px; align-items: center; font-size: 13px; }
        .sim-bar .track { position: relative; height: 16px; background: #f2f4f7; border-radius: 4px; }
        .sim-bar .track span { position: absolute; top: 0; bottom: 0; border-radius: 4px; }
        .sim-bar .zero { position: absolute; top: -2px; bottom: -2px; width: 1px; background: #98a2b3; }
        .sim-bar .value { text-align: right; font-weight: 600; }
        .win-badge { display: inline-block; min-width: 20px; padding: 0 6px; border-radius: 999px; font-size: 12px; font-weight: 700; text-align: center; }
        .win-badge.win { background: #ecfdf3; color: #027a48; } .win-badge.lose { background: #fef3f2; color: #b42318; }
        .pick-stock { border-top: 1px solid #edf0f4; padding: 10px 0 2px; }
        .pick-stock strong { cursor: pointer; }
        .pick-stock strong:hover { text-decoration: underline; }
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
        .trade-banner { border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.6; margin-bottom: 12px; }
        .trade-banner.off { background: #f2f4f7; border: 1px solid #d0d5dd; }
        .trade-banner.on { background: #fef3f2; border: 1px solid #fda29b; color: #912018; }
        .order-form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
        .search-results { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
        .search-results button { font-size: 13px; padding: 4px 10px; }
        .order-form input { width: 120px; }
        .order-preview { border: 1px solid #e1e5eb; border-radius: 10px; padding: 12px 14px; margin-top: 10px; font-size: 14px; line-height: 1.7; }
        .order-preview .problem { color: #b42318; }
        .order-preview .note { color: #b54708; }
        button.danger { background: #d92d20; border-color: #d92d20; color: white; }
        .status-card .line-pos { color: #d33d4d; } .status-card .line-neg { color: #2672c8; } .status-card .line-warn { color: #b54708; }
        .status-card ul { margin: 6px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.6; }
        .stat small { display: block; color: #667085; font-size: 12px; margin-bottom: 3px; }
        .stat strong { font-size: 15px; }
        .chart { width: 100%; height: 220px; display: block; }
        .clickable { cursor: pointer; }
        .flow-table { min-width: 720px; }
        .flow-table th:first-child, .flow-table td:first-child, .flow-table th:last-child, .flow-table td:last-child { text-align: left; }
        .flow-table tr.selected { background: #fff1f1; }
        .news-list { list-style: none; padding: 0 6px 0 0; margin: 0; display: grid; gap: 9px; max-height: 720px; overflow-y: auto; }
        .article-summary { margin-top: 6px; padding: 9px 11px; background: #f8fafc; border-radius: 6px; font-size: 13px; line-height: 1.6; color: #344054; }
        button.summary-button, button.ai-summary-button { padding: 2px 9px; font-size: 12px; border-radius: 999px; margin-left: 6px; }
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
            <button class="view-tab" data-view-target="recommend">AI분석</button>
            <button class="view-tab" data-view-target="account">내 계좌</button>
            <button class="view-tab" data-view-target="virtual">가상 투자</button>
        </nav>
        <section class="panel" id="briefing-panel" data-view="briefing" style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">시장 분석 <span class="code" id="briefing-updated"></span></h2><div class="subtitle">지수 흐름 · 분야 흐름 · 기사 논조 · 랭킹 표본을 종합한 해석입니다.</div></div>
                <div class="form-row"><select id="briefing-market" aria-label="분석 시장"><option value="all">통합(비교)</option><option value="kr" selected>국장</option><option value="us">미장</option></select><button id="briefing-refresh" type="button">다시 분석</button></div>
            </div>
            <div class="pad" id="briefing-body"><p class="empty">시장 분석을 불러오는 중입니다. 10초쯤 걸립니다.</p></div>
        </section>
        <section class="panel" id="virtual-panel" data-view="virtual" hidden style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">가상 투자</h2><div class="subtitle">가상의 돈으로 추천대로 투자했다면 어떻게 됐을지(과거), 앞으로 어떻게 되는지(오늘부터) 금액으로 보여 줍니다. 실제 주문은 나가지 않습니다.</div></div>
            </div>
            <div class="pad" id="virtual-body">
                <div class="briefing-card"><h4>① 과거로 가상 투자해 보기</h4>
                    <p class="analysis-meta" style="text-align:left;margin:0 0 8px">정한 금액으로, 과거 날짜마다 근거 점수 상위 종목을 같은 금액씩 정수 주로 사고 보유 기간이 지나면 팔아 다시 샀다면 얼마가 됐을지 계산합니다(시뮬레이션과 같은 규칙, 거래 비용 차감).</p>
                    <div class="order-form"><select id="vb-market" aria-label="시장"><option value="kr">국장</option><option value="us">미장</option></select><input id="vb-capital" type="number" min="100000" step="100000" value="10000000" aria-label="시작 금액(원)" style="width:150px"><span class="code">원</span><select id="vb-horizon" aria-label="보유 기간"><option value="20">20거래일마다 교체</option><option value="5">5거래일마다 교체</option></select><select id="vb-top" aria-label="종목 수"><option value="5">5종목</option><option value="3">3종목</option><option value="10">10종목</option></select><label class="code"><input id="vb-signals" type="checkbox" checked> 매도 신호 따르기(고점 대비 −10%)</label><select id="vb-months" aria-label="기간"><option value="12">최근 1년</option><option value="6">최근 6개월</option><option value="3">최근 3개월</option></select><button type="button" data-virtual-action="backtest">가상 투자 실행</button></div>
                    <div id="vb-result"></div></div>
                <div class="briefing-card" style="margin-top:12px"><h4>② 오늘부터 추천대로 가상 투자</h4>
                    <p class="analysis-meta" style="text-align:left;margin:0 0 8px">AI분석 탭의 지금 추천 종목을 같은 금액씩 <strong>다음 거래일 시가</strong>에 산 것으로 치고, 종료일 종가까지 매일 평가합니다. 같은 돈을 지수에 넣었을 때와 비교합니다.</p>
                    <div id="vp-form"></div><div id="vp-list"></div></div>
            </div>
        </section>
        <section class="panel" id="account-panel" data-view="account" hidden style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">내 계좌 <span class="code" id="account-updated"></span></h2><div class="subtitle">토스증권 계좌의 보유 종목·주문 가능 금액, 매수·매도 주문, 보유·관심 종목 현황입니다.</div></div>
                <div class="form-row"><button id="account-refresh" type="button">새로고침</button></div>
            </div>
            <div class="pad" id="account-body"><p class="empty">탭을 열면 계좌를 불러옵니다.</p></div>
        </section>
        <section class="panel" id="recommend-panel" data-view="recommend" hidden style="margin-bottom: 18px;">
            <div class="section-head">
                <div><h2 class="section-title">AI 분석 · 추천과 위험 <span class="code" id="recommend-updated"></span></h2><div class="subtitle">시장 분석 결과(분야 흐름 · 기사 논조 · 종목 주가 흐름)로 추천 테마·종목과 위험 테마·종목을 고르고, AI가 분석해 고른 종목을 근거와 함께 보여 줍니다.</div></div>
                <div class="form-row"><select id="recommend-market" aria-label="추천 시장"><option value="kr" selected>국장</option><option value="us">미장</option></select><button id="recommend-refresh" type="button">다시 계산</button></div>
            </div>
            <div class="pad" id="recommend-body"><p class="empty">탭을 열면 분석을 시작합니다.</p></div>
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
                    <button type="button" class="ghost range-button" data-days="30">30일</button><button type="button" class="ghost range-button" data-days="90">90일</button><button type="button" class="ghost range-button" data-days="180">180일</button><button type="button" class="ghost range-button" data-days="365">1년</button>
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
                <thead><tr><th>분야</th><th>1일</th><th>1주</th><th title="장이 열린 날 기준 20일">20일</th><th title="장이 열린 날 기준 60일">60일</th><th>거래대금</th><th>흐름 단계</th></tr></thead>
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
        function toneChip(tone, strong) {
            const style = tone === "무관" ? ' style="opacity:.6"' : strong ? ' style="font-weight:700"' : "";
            return `<span class="chip tone-chip ${tone === "호재" ? "pos" : tone === "악재" ? "neg" : ""}"${style}>${strong ? "강한 " : ""}${escapeHtml(tone)}</span>`;
        }
        const AI_TONES = { "강한 호재": ["호재", true], "호재": ["호재", false], "중립": ["중립", false], "악재": ["악재", false], "강한 악재": ["악재", true], "무관": ["무관", false] };
        function refreshFilterCounts(list) {
            const filters = list?.previousElementSibling;
            if (!filters?.classList.contains("news-filters")) return;
            const items = [...list.querySelectorAll("li")];
            filters.querySelectorAll(".news-filter").forEach(button => {
                const total = button.dataset.tone ? items.filter(item => item.dataset.tone === button.dataset.tone).length : items.length;
                button.textContent = button.textContent.replace(/[0-9]+$/, total);
            });
        }
        function newsMarkup(articles) {
            if (!articles.length) return '<p class="analysis-meta" style="text-align:left">조건에 맞는 기사를 찾지 못했습니다.</p>';
            const count = tone => articles.filter(article => article.tone === tone).length;
            const countries = [...new Set(articles.map(article => article.country || "한국"))];
            const countryNote = countries.length > 1 ? `<div class="code" style="margin-bottom:8px">수집 국가: ${countries.map(country => `${escapeHtml(country)} ${articles.filter(article => (article.country || "한국") === country).length}건`).join(" · ")}</div>` : "";
            const filters = `${countryNote}<div class="chip-row news-filters" style="margin-bottom:10px">${[["", "전체", articles.length], ["호재", "호재", count("호재")], ["악재", "악재", count("악재")], ["중립", "중립", count("중립")], ["무관", "무관", count("무관")]].map(([tone, label, total], index) => `<button type="button" class="ghost news-filter${index ? "" : " active"}" data-tone="${tone}">${label} ${total}</button>`).join("")}</div>`;
            return `${filters}<ul class="news-list">${articles.map(article => {
                const url = String(article.url || "");
                const safeUrl = url.startsWith("https://") ? escapeHtml(url) : "#";
                const similar = article.similar_count ? ` · 유사 기사 ${article.similar_count}건` : "";
                const evidence = article.tone_keywords?.length ? ` · 근거 표현: ${escapeHtml(article.tone_keywords.join(", "))}` : "";
                const alts = (article.alternates || []).filter(alt => String(alt).startsWith("https://")).join(" ");
                return `<li data-tone="${escapeHtml(article.tone)}" data-url="${safeUrl}" data-subject="${escapeHtml(article.matched || "")}" data-title="${escapeHtml(article.title)}" data-edition="${escapeHtml(article.edition || "")}" data-published="${escapeHtml(article.published_at || "")}" data-alts="${escapeHtml(alts)}"><div class="chip-row">${toneChip(article.tone, article.tone_strong)}<span class="chip">${escapeHtml(article.category)}</span><a href="${safeUrl}" target="_blank" rel="noopener noreferrer"${article.title_ko ? ` title="${escapeHtml(article.title)}"` : ""}>${escapeHtml(article.title_ko || article.title)}</a>${article.title_ko ? '<span class="chip">번역</span>' : ""}</div><div class="code" style="margin-top:4px">${article.country && article.country !== "한국" ? `<strong>${escapeHtml(article.country)}</strong> · ` : ""}${escapeHtml(article.publisher)} · ${escapeHtml(article.published_at)} · ${escapeHtml(article.matched)}${similar}${evidence}<button type="button" class="ghost summary-button">요약 보기</button></div><div class="article-summary" hidden></div></li>`;
            }).join("")}</ul>`;
        }
        const hasExtension = () => Boolean(document.documentElement.dataset.tossExtension);
        // 자동으로 불러오는 요약은 AI 없이(본문 추출·매체 설명문) 만들어 AI 한도를 아끼고, "AI 요약"을 누른 기사만 AI로 다시 요약합니다.
        async function loadSummary(item, toggle, force, useAi = false) {
            const box = item.querySelector(".article-summary"), button = item.querySelector(".summary-button");
            if (force) delete box.dataset.state;
            if (box.dataset.state) {
                if (toggle && box.dataset.state !== "loading") { box.hidden = !box.hidden; button.textContent = box.hidden ? "요약 보기" : "요약 접기"; }
                return;
            }
            box.dataset.state = "loading"; box.hidden = false; box.textContent = "기사 본문을 읽어 요약하는 중입니다."; button.textContent = "요약 접기";
            try {
                const params = new URLSearchParams({ url: item.dataset.url, subject: item.dataset.subject || "", title: item.dataset.title || "", edition: item.dataset.edition || "", published: item.dataset.published || "", ai: useAi ? "true" : "false" });
                (item.dataset.alts || "").split(" ").filter(Boolean).forEach(alt => params.append("alt", alt));
                const data = await fetchJson(`/api/v1/article-summary?${params}`);
                item.dataset.summary = data.ok && !data.blocked ? "ok" : data.blocked ? "blocked" : "fail";
                item.dataset.publisherUrl = data.publisher_url || "";
                if (data.ok && AI_TONES[data.tone]) {
                    // 본문을 읽은 AI 판정으로 제목 기준 판정을 바꿉니다.
                    const [tone, strong] = AI_TONES[data.tone];
                    item.dataset.tone = tone;
                    item.querySelector(".body-tone")?.remove();
                    item.querySelector(".tone-chip").outerHTML = toneChip(tone, strong) + '<span class="chip body-tone" title="기사 본문을 읽고 다시 판정했습니다">본문 기준</span>';
                    const active = item.closest(".news-list")?.previousElementSibling?.querySelector(".news-filter.active");
                    if (active?.dataset.tone) item.hidden = item.dataset.tone !== active.dataset.tone;
                    refreshFilterCounts(item.closest(".news-list"));
                }
                if (data.ok) {
                    const origin = data.source === "title" ? "제목 기반 설명 · 본문 미확인" : data.source === "sec" ? "SEC 공시 원문 AI 요약" : data.source === "sec_body" ? "SEC 공시 원문 추출 요약" : data.source === "ai" ? "AI 요약" : data.source === "body" ? "본문 추출 요약" : "매체 제공 요약";
                    const language = data.summary_ko || data.translated ? " · 번역" : data.lang === "en" ? " · 번역 실패, 원문 표시" : "";
                    let altHost = "";
                    try { altHost = data.alternate ? new URL(data.alternate.publisher_url).hostname.replace(/^www[.]/, "") : ""; } catch (error) { altHost = "다른 매체"; }
                    const via = (data.via === "browser" ? " · 브라우저로 읽음" : "") + (data.alternate ? ` · 원문이 막혀 같은 내용의 ${altHost} 기사로 요약` : "");
                    const filing = data.filing ? `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(data.filing.description)} <a href="${String(data.filing.url).startsWith("https://www.sec.gov/") ? escapeHtml(data.filing.url) : "#"}" target="_blank" rel="noopener noreferrer" class="code">공시 원문</a></div>` : "";
                    const aiMissed = useAi && !["ai", "sec", "title"].includes(data.source) ? '<div class="code">AI가 지금 응답하지 않아 본문에서 뽑은 요약을 보여 줍니다. 잠시 뒤 다시 눌러 보세요.</div>' : "";
                    const aiButton = (data.ai_available && !["ai", "sec", "title"].includes(data.source) ? '<button type="button" class="ghost ai-summary-button" title="AI가 본문을 읽고 다시 요약합니다">AI 요약</button>' : "") + aiMissed;
                    box.innerHTML = `${filing}${escapeHtml(data.summary_ko || data.summary)} <span class="code">(${origin}${language}${via})</span>${aiButton}`;
                    if (data.summary_ko) box.title = data.summary;
                } else { box.innerHTML = `<span class="code">${escapeHtml(data.reason)}</span>`; }
                item.dataset.fallback = data.ok && data.blocked ? "1" : "";
                if (data.blocked) sendToExtension(item, box, data.ok);
            } catch (error) { box.innerHTML = `<span class="code">요약을 불러오지 못했습니다. (${escapeHtml(error.message)})</span>`; }
            box.dataset.state = "done";
        }
        const sentToExtension = new Set();
        function sendToExtension(item, box, hasFallback) {
            // 서버가 원문을 못 읽은 기사는 확장 프로그램이 있으면 자동으로 브라우저 탭에서 읽게 합니다. 한 기사는 한 번만 보냅니다.
            if (!hasExtension() || sentToExtension.has(item.dataset.url)) return;
            sentToExtension.add(item.dataset.url);
            if (!hasFallback) box.innerHTML = '<span class="code">원문이 막혀 브라우저로 대신 읽을 차례를 기다리는 중입니다.</span>';
            window.postMessage({ type: "toss-ext:summarize", jobs: [{ url: item.dataset.url, subject: item.dataset.subject || "", open_url: item.dataset.publisherUrl || "" }] }, location.origin);
        }
        window.addEventListener("message", event => {
            // 확장 프로그램이 보내는 진행 상황: 기사 한 건을 열기 시작함(reading) / 끝남(done)
            if (event.source !== window || event.origin !== location.origin || event.data?.type !== "toss-ext:progress") return;
            const { url, state, ok, reason } = event.data;
            document.querySelectorAll(".news-list li[data-url]").forEach(item => {
                if (item.dataset.url !== url) return;
                const box = item.querySelector(".article-summary");
                box.hidden = false;
                // 제목 기반 설명이 이미 떠 있으면 읽는 동안에도 그대로 두고, 원문 요약이 오면 바꿉니다.
                const hasFallback = item.dataset.fallback === "1";
                if (state === "reading") { if (!hasFallback) { box.dataset.state = "loading"; box.textContent = "원문이 막혀 브라우저 탭에서 대신 읽는 중입니다."; } return; }
                if (ok) { loadSummary(item, false, true); }
                else if (!hasFallback) { box.innerHTML = `<span class="code">원문을 열 수 없는 기사입니다. ${escapeHtml(reason || "")}</span>`; box.dataset.state = "done"; }
            });
        });
        // 기사가 화면에 들어오면 자동으로 요약합니다. 동시에 세 건까지 요청하고, 지금 화면에 보이는 기사를 먼저 처리합니다.
        const SUMMARY_CONCURRENCY = 3;
        const summaryQueue = [], visibleItems = new Set();
        let summaryWorkers = 0;
        async function drainSummaries() {
            if (summaryWorkers >= SUMMARY_CONCURRENCY) return;
            summaryWorkers += 1;
            try {
                while (summaryQueue.length) {
                    const index = Math.max(0, summaryQueue.findIndex(item => visibleItems.has(item)));
                    const [item] = summaryQueue.splice(index, 1);
                    summaryObserver.unobserve(item);
                    visibleItems.delete(item);
                    if (item.isConnected && !item.querySelector(".article-summary").dataset.state) await loadSummary(item, false);
                }
            } finally { summaryWorkers -= 1; }
        }
        const summaryObserver = new IntersectionObserver(entries => {
            for (const entry of entries) {
                if (!entry.isIntersecting) { visibleItems.delete(entry.target); continue; }
                visibleItems.add(entry.target);
                if (!summaryQueue.includes(entry.target)) summaryQueue.push(entry.target);
            }
            for (let worker = 0; worker < SUMMARY_CONCURRENCY; worker++) drainSummaries();
        }, { rootMargin: "200px 0px" });
        function loadTopSummaries(root) {
            root.querySelectorAll(".news-list li[data-url]").forEach(item => summaryObserver.observe(item));
        }
        // 기사 목록은 규칙 기반 분류로 먼저 오고(ai_pending), AI 분류가 끝나면 같은 주소에서 바뀐 판정을 받아 덧입힙니다.
        const AI_POLL_MS = 4000, AI_POLL_LIMIT = 30;
        async function followAiUpgrade(root, url, report, isCurrent) {
            if (!report.ai_pending) return;
            for (let attempt = 0; attempt < AI_POLL_LIMIT; attempt++) {
                await new Promise(resolve => setTimeout(resolve, AI_POLL_MS));
                if (!isCurrent()) return;
                let next;
                try { next = await fetchJson(url); } catch (error) { continue; }
                if (!isCurrent()) return;
                if (!next.ai_pending) { applyAiLabels(root, next); return; }
            }
            const status = root.querySelector(".ai-pending");
            if (status) status.textContent = "AI 분류가 늦어져 규칙 기반 판정을 표시합니다";
        }
        function applyAiLabels(root, report) {
            const digest = root.querySelector(".news-digest");
            if (digest) digest.innerHTML = digestMarkup(report.news_digest, report.tone_price_signal);
            const byUrl = new Map(report.articles.map(article => [String(article.url || ""), article]));
            root.querySelectorAll(".news-list li[data-url]").forEach(item => {
                const article = byUrl.get(item.dataset.url);
                // 요약하면서 본문 기준으로 다시 판정한 기사는 그 판정을 유지합니다.
                if (!article || item.querySelector(".body-tone")) return;
                item.dataset.tone = article.tone;
                item.querySelector(".tone-chip").outerHTML = toneChip(article.tone, article.tone_strong);
            });
            root.querySelectorAll(".news-list").forEach(list => {
                const active = list.previousElementSibling?.querySelector(".news-filter.active");
                if (active?.dataset.tone) list.querySelectorAll("li").forEach(item => { item.hidden = item.dataset.tone !== active.dataset.tone; });
                refreshFilterCounts(list);
            });
        }
        function digestMarkup(digest, signal, pending) {
            const counts = `<div class="chip-row" style="margin:8px 0"><span class="chip pos">호재 ${digest.positive}</span><span class="chip neg">악재 ${digest.negative}</span><span class="chip">중립 ${digest.neutral}</span>${digest.unrelated ? `<span class="chip" style="opacity:.6" title="주가와 관계없는 기사라 논조 계산에서 뺐습니다">무관 ${digest.unrelated}</span>` : ""}${digest.strong_positive || digest.strong_negative ? `<span class="chip">강한 호재 ${digest.strong_positive || 0} · 강한 악재 ${digest.strong_negative || 0}</span>` : ""}<span class="chip">논조 ${escapeHtml(digest.tone)}</span><span class="chip${pending ? " ai-pending" : ""}">${pending ? "규칙 기반 분류 · AI 분류 진행 중" : digest.method === "ai" ? "AI 분류" : digest.method === "ai_partial" ? `AI 분류 ${Math.round((digest.ai_coverage || 0) * 100)}% · 나머지 규칙` : "규칙 기반 분류"}</span></div>`;
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
                ${stat("흐름", escapeHtml(trend.flow), `장이 열린 ${trend.trading_days}일 중 상승 ${trend.up_days}일 / 하락 ${trend.down_days}일`)}
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
                    const newsUrl = `/api/v1/stock-news?symbol=${encodeURIComponent(data.symbol)}&days=${days}`;
                    const report = await fetchJson(newsUrl);
                    if (request !== historyRequest) return;
                    news.innerHTML = `<h3>${escapeHtml(report.name)} 관련 기사 (최근 ${report.news_days}일)</h3><div class="news-digest">${digestMarkup(report.news_digest, report.tone_price_signal, report.ai_pending)}</div>${newsMarkup(report.articles)}`;
                    loadTopSummaries(news);
                    followAiUpgrade(news, newsUrl, report, () => request === historyRequest);
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
                const reportUrl = `/api/v1/sectors/${encodeURIComponent(sectorId)}/report?market=${market}&days=${days}`;
                const report = await fetchJson(reportUrl);
                if (request !== sectorRequest) return;
                const durationLabels = { "1d": "1일", "1w": "1주", "1mo": "20일", "3mo": "60일", "6mo": "120일", "1y": "1년" };
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
                    <h3>기사 논조 (최근 ${report.news_days}일)</h3><div class="news-digest">${digestMarkup(report.news_digest, report.tone_price_signal, report.ai_pending)}</div>${newsMarkup(report.articles)}`;
                loadTopSummaries(root);
                followAiUpgrade(root, reportUrl, report, () => request === sectorRequest);
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
        const PERIOD_LABELS = { "1d": "1일", "1w": "1주", "1mo": "20일", "3mo": "60일" };
        function periodChips(rates, keys = ["1d", "1w", "1mo", "3mo"]) {
            return keys.map(key => rateChip(PERIOD_LABELS[key], rates?.[key])).join("");
        }
        function flowChip(flow) {
            return `<span class="chip ${flow.startsWith("상승 지속") || flow === "반등 전환" ? "pos" : flow.startsWith("하락 지속") || flow === "상승 후 꺾임" ? "neg" : ""}">${escapeHtml(flow)}</span>`;
        }
        function renderCombinedBriefing(data) {
            const narrative = data.narrative, cross = data.cross;
            const gauges = data.gauges.map(gauge => `<div class="stat clickable" data-symbol="${escapeHtml(gauge.symbol)}" title="기간별 주가 흐름 보기"><small>${gauge.market === "KR" ? "국장" : "미장"} · ${escapeHtml(gauge.name)} · ${escapeHtml(gauge.date)}</small><strong>${priceLabel(gauge.close, gauge.currency)}</strong><div class="chip-row" style="margin:8px 0 6px">${periodChips(gauge.rates)}</div><div class="code">최근 60일: ${escapeHtml(gauge.flow || "-")}</div></div>`).join("");
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
            const gauges = data.gauges.map(gauge => `<div class="stat clickable" data-symbol="${escapeHtml(gauge.symbol)}" title="기간별 주가 흐름 보기"><small>${escapeHtml(gauge.name)} · ${escapeHtml(gauge.date)}</small><strong>${priceLabel(gauge.close, gauge.currency)}</strong><div class="chip-row" style="margin:8px 0 6px">${periodChips(gauge.rates)}</div><div class="code">최근 60일: ${escapeHtml(gauge.flow || "-")}${gauge.max_drawdown ? ` · 최대 낙폭 ${signedRate(gauge.max_drawdown)}` : ""}</div></div>`).join("");
            const sectorRow = item => `<div class="briefing-row clickable" data-sector="${escapeHtml(item.sector_id)}" title="분야 기사와 구성 종목 보기"><strong>${escapeHtml(item.name)}</strong><span class="chip-row">${periodChips(item.rates, ["1d", "1w", "1mo"])}${flowChip(item.flow)}</span></div>`;
            const turning = (label, items) => items.length ? `<div class="chip-row" style="margin-top:8px"><span class="code">${label}</span>${items.map(item => `<button type="button" class="ghost" data-sector="${escapeHtml(item.sector_id)}">${escapeHtml(item.name)} <span class="${rateClassOf(item.rates["1d"])}">${signedRate(item.rates["1d"])}</span></button>`).join("")}</div>` : "";
            const flowCounts = Object.entries(overview.flow_counts).sort((left, right) => right[1] - left[1]).map(([flow, count]) => `<span class="chip">${escapeHtml(flow)} ${count}</span>`).join("");
            const focus = data.focus_sectors.map(sector => {
                const articles = sector.articles.map(article => {
                    const url = String(article.url || "");
                    return `<li>${toneChip(article.tone, article.tone_strong)} <a href="${url.startsWith("https://") ? escapeHtml(url) : "#"}" target="_blank" rel="noopener noreferrer"${article.title_ko ? ` title="${escapeHtml(article.title)}"` : ""}>${escapeHtml(article.title_ko || article.title)}</a>${article.title_ko ? ' <span class="chip">번역</span>' : ""} <span class="code">${article.country && article.country !== "한국" ? `${escapeHtml(article.country)} · ` : ""}${escapeHtml(article.publisher)}</span></li>`;
                }).join("");
                const counts = sector.article_count
                    ? `<span class="chip pos">호재 ${sector.positive}</span><span class="chip neg">악재 ${sector.negative}</span><span class="chip">중립 ${sector.neutral}</span>${sector.unrelated ? `<span class="chip" style="opacity:.6">무관 ${sector.unrelated}</span>` : ""}<span class="chip">논조 ${escapeHtml(sector.tone)}</span>`
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
                <h3>주목 분야의 기사 논조 <span class="code">최근 ${data.news_days}일 · ${data.focus_sectors.some(sector => sector.classification && sector.classification !== "rule") ? "제목 기준 AI 분류(실패 시 규칙)" : "제목 기준 규칙 분류"}</span></h3><div class="briefing-grid">${focus}</div>
                ${sampleMarkup}
                <p class="analysis-meta" style="text-align:left;margin-top:14px">${escapeHtml(data.caveat)}</p>`;
        }
        // AI 분석 탭: 규칙으로 고른 추천·위험 테마와 종목, AI가 고른 추천·위험 종목을 근거와 함께 보여 줍니다(참고용).
        let recommendLoaded = false, recommendRequest = 0;
        const listMarkup = (items, cls) => items.length ? `<ul class="${cls}">${items.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : "";
        function stockHead(stock, market, themeButton) {
            const warnings = stock.warnings.map(warning => `<span class="chip neg">${escapeHtml(warning.label)}</span>`).join("");
            const theme = themeButton && stock.theme_sector_id ? `<button type="button" class="ghost" data-sector="${escapeHtml(stock.theme_sector_id)}" data-market="${market}" title="분야 기사와 구성 종목 보기">${escapeHtml(stock.theme)} 테마</button>` : "";
            return `<div class="chip-row"><strong class="clickable" data-symbol="${escapeHtml(stock.symbol)}" title="기간별 주가 흐름 보기">${escapeHtml(stock.name)}</strong><span class="code">${escapeHtml(stock.symbol)} · ${priceLabel(stock.close, stock.currency)}</span>${theme}</div>
                <div class="chip-row" style="margin-top:6px">${rateChip("5일", stock.rates["5d"])}${rateChip("20일", stock.rates["20d"])}${rateChip("60일", stock.rates["60d"])}${warnings}${evidenceChip(stock.evidence)}</div>${evidenceDetails(stock.evidence)}`;
        }
        // 추천 근거 모듈(app/signals.py): 근거 10가지를 +·0·−로 판정해 0~100점으로 묶은 값과 그 내역.
        function evidenceChip(evidence) {
            if (!evidence || evidence.score === null || evidence.score === undefined) return "";
            const cls = evidence.score >= 65 ? "pos" : evidence.score < 35 ? "neg" : "";
            return `<span class="chip ${cls}" title="근거 10가지를 합친 점수(50이 중립)">근거 ${evidence.score}점 · ${escapeHtml(evidence.grade)}</span>`;
        }
        function evidenceDetails(evidence) {
            if (!evidence || !evidence.factors?.length) return "";
            const mark = signal => signal > 0 ? ["plus", "✓"] : signal < 0 ? ["minus", "✕"] : ["zero", "·"];
            // 참고 근거(미국 주식의 매수·매도 압력)는 ◇로 따로 표시하고 긍정·부정 개수에 넣지 않습니다.
            const scored = evidence.factors.filter(factor => !factor.reference);
            const items = evidence.factors.map(factor => { const [cls, symbol] = mark(factor.signal); return `<li class="${cls}">${factor.reference ? "◇" : symbol} <strong>${escapeHtml(factor.label)}</strong> — ${escapeHtml(factor.value)} <span class="code">${escapeHtml(factor.text)}</span></li>`; }).join("");
            return `<details class="evidence"><summary>근거 보기 (긍정 ${scored.filter(factor => factor.signal > 0).length} · 부정 ${scored.filter(factor => factor.signal < 0).length})</summary><ul class="evidence-list">${items}</ul></details>`;
        }
        // 통합 추천: AI·테마 양쪽 추천을 합친 목록(기존 "AI 추천 종목"·"추천 테마" 칸은 그대로 둡니다).
        function viaChips(pick) {
            return (pick.via || []).map(via => `<span class="chip ${via === "ai" ? "pos" : ""}">${via === "ai" ? "AI 추천" : "테마 추천"}</span>`).join("");
        }
        function combinedMarkup(pick, market) {
            const reasons = pick.reason ? [pick.reason] : (pick.reasons || []);
            const risks = pick.risk ? [pick.risk] : (pick.risks || []);
            return `<div class="briefing-card theme-card"><div class="chip-row" style="margin-bottom:6px">${viaChips(pick)}<button type="button" class="ghost" data-open-order="${escapeHtml(pick.symbol)}" title="내 계좌 탭의 주문 칸에 채웁니다">이 종목 주문하기</button></div>${stockHead(pick, market, true)}
                ${listMarkup(reasons, "")}${listMarkup(risks, "risks")}</div>`;
        }
        function aiPickMarkup(pick, market, risky) {
            const order = risky ? "" : `<div style="margin-top:6px"><button type="button" class="ghost" data-open-order="${escapeHtml(pick.symbol)}" title="내 계좌 탭의 주문 칸에 채웁니다">이 종목 주문하기</button></div>`;
            return `<div class="briefing-card theme-card${risky ? " risk-card" : ""}">${order}${stockHead(pick, market, true)}
                ${risky ? listMarkup([pick.reason], "danger") : listMarkup([pick.reason], "") + (pick.risk ? listMarkup([pick.risk], "risks") : "")}</div>`;
        }
        function recommendStock(stock, market, risky) {
            // 위험 종목은 위험 근거를 붉게, 반대로 볼 점을 기본색으로 보여 줍니다.
            const body = risky ? listMarkup(stock.reasons, "danger") + listMarkup(stock.risks, "") : listMarkup(stock.reasons, "") + listMarkup(stock.risks, "risks");
            return `<div class="pick-stock">${stockHead(stock, market, false)}${body}</div>`;
        }
        function recommendTheme(theme, market) {
            const risky = theme.kind === "risk";
            const news = theme.news && theme.news.article_count ? `<span class="chip pos">호재 ${theme.news.positive}</span><span class="chip neg">악재 ${theme.news.negative}</span><span class="chip">논조 ${escapeHtml(theme.news.tone)}</span>` : '<span class="chip">기사 없음</span>';
            const stocks = theme.stocks.length ? theme.stocks.map(stock => recommendStock(stock, market, risky)).join("") : `<p class="analysis-meta" style="text-align:left;margin:8px 0 0">조건에 맞는 ${risky ? "위험" : "추천"} 종목을 찾지 못했습니다.</p>`;
            const labels = risky
                ? ["위험 근거", "반대로 볼 점", "이 테마의 위험 종목 (위험 점수 순)"]
                : ["근거", "주의할 점", "이 테마의 추천 종목 (테마 안에서 비교한 점수 순)"];
            return `<div class="briefing-card theme-card${risky ? " risk-card" : ""}"><h4 class="clickable" data-sector="${escapeHtml(theme.sector_id)}" data-market="${market}" title="분야 기사와 구성 종목 보기">${escapeHtml(theme.name)} ${flowChip(theme.flow)}</h4>
                <div class="chip-row">${periodChips(theme.rates, ["1d", "1w", "1mo", "3mo"])}${news}</div>
                <div class="code" style="margin-top:8px">${labels[0]}</div>${listMarkup(theme.reasons, risky ? "danger" : "")}
                ${theme.risks.length ? `<div class="code" style="margin-top:6px">${labels[1]}</div>${listMarkup(theme.risks, risky ? "" : "risks")}` : ""}
                <div class="code" style="margin-top:10px">${labels[2]}</div>${stocks}</div>`;
        }
        function renderRecommendations(data) {
            const market = data.market.toLowerCase();
            const section = (kind, title, note, cls = "") => {
                const themes = data.themes.filter(theme => theme.kind === kind);
                return `<h3 class="${cls}">${title} <span class="code">${note}</span></h3><div class="theme-list">${themes.length ? themes.map(theme => recommendTheme(theme, market)).join("") : '<p class="analysis-meta" style="text-align:left">지금 조건에 맞는 분야가 없습니다.</p>'}</div>`;
            };
            const aiMissing = '<p class="analysis-meta" style="text-align:left">AI가 지금 응답하지 않아 고르지 못했습니다. 아래 테마별 종목을 참고하시고, 잠시 뒤 "다시 계산"을 눌러 보세요.</p>';
            const aiRisk = data.method !== "ai" ? aiMissing
                : data.ai_risk_picks.length ? `<div class="theme-list">${data.ai_risk_picks.map(pick => aiPickMarkup(pick, market, true)).join("")}</div>`
                : '<p class="analysis-meta" style="text-align:left">AI가 특히 주의할 종목을 따로 고르지 않았습니다. 아래 위험 테마의 종목을 참고하세요.</p>';
            const flagged = data.flagged_stocks.length
                ? `<h3 class="risk-heading">매수 유의사항으로 추천에서 뺀 종목 <span class="code">추천 테마에 속하지만 투자경고·위험·정리매매가 걸린 종목</span></h3><div class="theme-list">${data.flagged_stocks.map(stock => `<div class="briefing-card theme-card risk-card">${stockHead(stock, market, true)}${listMarkup(stock.reasons, "danger")}</div>`).join("")}</div>`
                : "";
            document.getElementById("recommend-updated").textContent = `${data.generated_at} 기준`;
            document.getElementById("recommend-body").innerHTML = `
                <div class="caveat-box"><strong>참고용 분석입니다.</strong> ${escapeHtml(data.caveat.replace("참고용 분석입니다. ", ""))}</div>
                <p class="briefing-summary">${escapeHtml(data.summary)} <span class="chip">${data.method === "ai" ? "AI 분석" : "규칙 기반 분석"}</span></p>
                ${(data.combined_picks || []).length ? `<h3>통합 추천 종목 <span class="code">AI 추천과 테마 추천을 합친 상위 ${data.combined_picks.length}종목 · 양쪽에서 모두 추천된 종목 → 나머지 AI 추천 → 테마 종목(근거 점수 순)</span></h3><div class="theme-list">${data.combined_picks.map(pick => combinedMarkup(pick, market)).join("")}</div>` : ""}
                <h3>AI 추천 종목 <span class="code">추천 테마의 종목 ${data.ai_pool_size}개를 AI가 비교해 고른 종목</span></h3>
                ${data.ai_picks.length ? `<div class="theme-list">${data.ai_picks.map(pick => aiPickMarkup(pick, market, false)).join("")}</div>` : aiMissing}
                <h3 class="risk-heading">AI 위험 종목 <span class="code">위험 테마 종목과 유의사항 종목 ${data.ai_risk_pool_size}개 중 AI가 특히 주의할 종목으로 고른 것 · 매도 권유 아님</span></h3>
                ${aiRisk}
                ${section("trend", "추천 테마 · 추세 강세", "1주·20일 모두 오른 분야 중 점수 순")}
                ${section("rebound", "추천 테마 · 반등 시도", "하락하다 최근 1주 반등한 분야 중 반등 폭 순")}
                ${section("risk", "위험 테마", "하락 지속·상승 후 꺾임 분야 우선, 모자라면 20일이 마이너스인 약한 분야 · 약한 순", "risk-heading")}
                ${flagged}
                <p class="analysis-meta" style="text-align:left;margin-top:14px">계산 방법: ${escapeHtml(data.method_note)}</p>
                <div id="validation-section">${validationShell()}</div>`;
            loadSimulation(false);
            loadTrack();
        }
        // 내 계좌: 보유 종목, 주문(미리보기 → 확인), 미체결 주문 취소, 관심 종목 현황.
        // 주문·취소·관심 종목 변경은 X-Toss-Order 헤더를 붙여 보냅니다(서버가 다른 사이트의 요청을 거르는 데 씁니다).
        let accountLoaded = false, accountRequest = 0, accountTrading = null;
        async function sendJson(url, body, method = "POST") {
            const response = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-Toss-Order": "1" }, body: body === undefined ? undefined : JSON.stringify(body) });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
            return data;
        }
        function moneyText(value, currency) {
            if (value === null || value === undefined) return "-";
            return currency === "USD" ? `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : `${Math.round(Number(value)).toLocaleString("ko-KR")}원`;
        }
        function signedClass(value) { return value > 0 ? "positive" : value < 0 ? "negative" : ""; }
        function rateText(value) { return value === null || value === undefined ? "-" : `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}%`; }
        function renderAccount(data) {
            const holdings = data.holdings, trading = data.trading;
            accountTrading = trading;
            const banner = trading.enabled
                ? `<div class="trade-banner on"><strong>실제 주문 켜짐.</strong> 이 화면의 주문은 토스증권 실제 계좌에서 바로 체결됩니다(모의투자 없음). 1회 예상 금액 상한: ${moneyText(trading.max_amount.KRW, "KRW")} · ${moneyText(trading.max_amount.USD, "USD")}</div>`
                : !trading.server_enabled
                ? '<div class="trade-banner off"><strong>실제 주문 꺼짐(서버).</strong> 이 서버는 실제 주문을 받지 않도록 설정되어 있어 미리보기까지만 됩니다(서버 관리자가 <span class="code">.env</span>의 <span class="code">TRADING_ENABLED</span>로 켭니다).</div>'
                : '<div class="trade-banner off"><strong>실제 주문 꺼짐(내 계정).</strong> 미리보기까지만 됩니다. 실제로 주문하려면 위 계정 설정에서 "실제 주문 허용"을 켜세요. 토스증권 Open API에는 모의투자가 없어 주문은 실제로 체결됩니다.</div>';
            const stat = (label, value, cls = "") => `<div class="stat"><small>${label}</small><strong class="${cls}">${value}</strong></div>`;
            const rows = holdings.items.map(item => `<tr><td class="name"><strong class="clickable" data-symbol="${escapeHtml(item.symbol)}" title="기간별 주가 흐름 보기">${escapeHtml(item.name)}</strong> <span class="code">${escapeHtml(item.symbol)}</span></td><td>${Number(item.quantity).toLocaleString("ko-KR")}</td><td>${moneyText(item.average_price, item.currency)}</td><td>${moneyText(item.last_price, item.currency)}</td><td>${moneyText(item.market_value, item.currency)}</td><td class="${signedClass(item.profit_loss)}">${moneyText(item.profit_loss, item.currency)} (${rateText(item.profit_rate)})</td><td class="${signedClass(item.daily_rate)}">${rateText(item.daily_rate)}</td><td><button type="button" class="ghost" data-action="buy" data-symbol-value="${escapeHtml(item.symbol)}">매수</button> <button type="button" class="ghost" data-action="sell" data-symbol-value="${escapeHtml(item.symbol)}" data-quantity="${escapeHtml(String(item.quantity))}">매도</button> <button type="button" class="ghost" data-action="watch" data-symbol-value="${escapeHtml(item.symbol)}">관심</button></td></tr>`).join("");
            const orders = data.open_orders.length ? data.open_orders.map(order => `<div class="briefing-row"><span><strong>${escapeHtml(order.name || order.symbol)}</strong> <span class="chip ${order.side === "BUY" ? "pos" : "neg"}">${escapeHtml(order.side_label)}</span> <span class="chip">${escapeHtml(order.order_type_label)}</span> ${order.price !== null ? moneyText(order.price, order.currency) : "시장가"} × ${order.quantity} (체결 ${order.filled_quantity}) · <span class="code">${escapeHtml(order.status_label)} · ${escapeHtml(order.ordered_at || "")}</span></span>${order.cancelable && trading.enabled ? `<button type="button" class="ghost" data-action="cancel" data-order="${escapeHtml(order.order_id)}">취소</button>` : ""}</div>`).join("") : '<p class="analysis-meta" style="text-align:left">미체결 주문이 없습니다.</p>';
            document.getElementById("account-updated").textContent = `${data.generated_at} 기준 · 계좌 ${data.account.number}`;
            document.getElementById("account-body").innerHTML = `${accountBar(accountUser)}${banner}
                <div class="stat-grid">${stat("평가금액", moneyText(holdings.market_value.krw, "KRW"))}${stat("총 손익", `${moneyText(holdings.profit_loss.krw, "KRW")} (${rateText(holdings.profit_rate)})`, signedClass(holdings.profit_loss.krw))}${stat("오늘 손익", `${moneyText(holdings.daily_profit_loss.krw, "KRW")} (${rateText(holdings.daily_rate)})`, signedClass(holdings.daily_profit_loss.krw))}${stat("주문 가능 (원)", moneyText(data.buying_power.KRW, "KRW"))}${stat("주문 가능 (달러)", moneyText(data.buying_power.USD, "USD"))}</div>
                <h3>보유 종목 <span class="code">${holdings.items.length}개 · 손익률은 토스증권 기준</span></h3>
                ${holdings.items.length ? `<div class="table-wrap"><table><thead><tr><th>종목</th><th>수량</th><th>평균가</th><th>현재가</th><th>평가금액</th><th>손익</th><th>오늘</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="analysis-meta" style="text-align:left">보유 종목이 없습니다.</p>'}
                <h3>주문 <span class="code">미리보기로 금액·주문 가능 여부를 확인한 뒤 보냅니다</span></h3>
                <div class="order-form"><input id="order-symbol" placeholder="종목 이름 또는 코드 (삼성전자, NVDA)" aria-label="종목 이름 또는 코드" autocomplete="off" data-search="order" style="width:230px"><select id="order-side" aria-label="매수/매도"><option value="BUY">매수</option><option value="SELL">매도</option></select><select id="order-type" aria-label="주문 유형"><option value="LIMIT">지정가</option><option value="MARKET">시장가</option></select><input id="order-quantity" type="number" min="0" step="any" placeholder="수량" aria-label="수량"><input id="order-price" type="number" min="0" step="any" placeholder="가격(지정가)" aria-label="가격"><select id="order-price-currency" aria-label="가격 단위"><option value="USD">달러($)</option><option value="KRW">원(₩)</option></select><button type="button" data-action="preview">주문 미리보기</button></div>
                <div class="search-results" id="order-search"></div>
                <div class="code" id="order-quote" style="margin-top:6px">종목 이름이나 코드를 넣고 목록에서 고르면 거래 통화와 최근 가격이 나옵니다.</div>
                <div id="order-preview"></div>
                <h3>미체결 주문</h3>${orders}
                <h3>주문 내역 <span class="code">최근 7일 체결·취소 · 토스증권 기준</span> <button type="button" class="ghost" data-action="history">불러오기</button></h3>
                <div id="order-history"><p class="analysis-meta" style="text-align:left">불러오기를 누르면 최근 7일 동안 끝난 주문(체결·취소·거부)을 보여 줍니다.</p></div>
                <h3>보유·관심 종목 현황 <span class="code">최근 등락 · 20·60일 흐름 · 평균선 · 변동성 · 유의사항 · 보유 손익 · 기사 논조</span></h3>
                <div class="form-row" style="margin-bottom:4px"><input id="watch-symbol" placeholder="관심 종목 이름 또는 코드 (삼성전자, NVDA)" aria-label="관심 종목 이름 또는 코드" autocomplete="off" data-search="watch" style="width:260px"><button type="button" data-action="watch-add">관심 종목 추가</button></div><div class="search-results" id="watch-search"></div>
                <div id="stock-status"><p class="analysis-meta" style="text-align:left">종목 현황을 불러오는 중입니다.</p></div>`;
            loadStockStatus();
            if (pendingOrder) { const order = pendingOrder; pendingOrder = null; fillOrder(order.symbol, order.side, order.quantity); }
        }
        async function loadOrderHistory() {
            const root = document.getElementById("order-history");
            root.innerHTML = '<p class="analysis-meta" style="text-align:left">주문 내역을 불러오는 중입니다.</p>';
            try {
                const data = await fetchJson("/api/v1/account/orders?status=CLOSED");
                if (!data.orders.length) { root.innerHTML = '<p class="analysis-meta" style="text-align:left">최근 7일 동안 끝난 주문이 없습니다.</p>'; return; }
                const statusClass = status => status === "FILLED" ? "pos" : ["REJECTED", "CANCEL_REJECTED", "REPLACE_REJECTED"].includes(status) ? "neg" : "";
                const filled = data.orders.filter(order => order.filled_quantity > 0);
                const rows = data.orders.map(order => {
                    const amount = order.filled_quantity && order.average_filled_price ? order.filled_quantity * order.average_filled_price : null;
                    return `<tr><td class="code">${escapeHtml((order.ordered_at || "").replace("T", " ").slice(0, 16))}</td><td>${escapeHtml(order.name || order.symbol)} <span class="code">${escapeHtml(order.symbol || "")}</span></td><td><span class="chip ${order.side === "BUY" ? "pos" : "neg"}">${escapeHtml(order.side_label)}</span></td><td>${escapeHtml(order.order_type_label)}</td><td>${order.price !== null ? moneyText(order.price, order.currency) : "시장가"}</td><td>${order.quantity ?? "-"}</td><td>${order.filled_quantity}</td><td>${order.average_filled_price !== null ? moneyText(order.average_filled_price, order.currency) : "-"}</td><td>${amount !== null ? moneyText(amount, order.currency) : "-"}</td><td><span class="chip ${statusClass(order.status)}">${escapeHtml(order.status_label)}</span></td></tr>`;
                }).join("");
                root.innerHTML = `<p class="code">${data.orders.length}건 · 체결 ${filled.length}건 (매수 ${filled.filter(order => order.side === "BUY").length} · 매도 ${filled.filter(order => order.side === "SELL").length})</p>
                    <div class="table-wrap"><table><thead><tr><th>주문 시각</th><th>종목</th><th>구분</th><th>유형</th><th>주문가</th><th>수량</th><th>체결 수량</th><th>평균 체결가</th><th>체결 금액</th><th>상태</th></tr></thead><tbody>${rows}</tbody></table></div>`;
            } catch (error) {
                root.innerHTML = `<p class="error">주문 내역을 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`;
            }
        }

        async function loadStockStatus() {
            const root = document.getElementById("stock-status");
            if (!root) return;
            try {
                const [status, watch] = await Promise.all([fetchJson("/api/v1/account/stock-status"), fetchJson("/api/v1/watchlist")]);
                const watched = new Set(watch.items.map(item => item.symbol));
                root.innerHTML = status.stocks.length ? `<div class="briefing-grid">${status.stocks.map(stock => `<div class="briefing-card status-card"><div class="chip-row"><strong class="clickable" data-symbol="${escapeHtml(stock.symbol)}" title="기간별 주가 흐름·기사 보기">${escapeHtml(stock.name)}</strong><span class="code">${escapeHtml(stock.symbol)}</span>${stock.held ? '<span class="chip pos">보유</span>' : ""}${watched.has(stock.symbol) ? '<span class="chip">관심</span>' : ""}<button type="button" class="ghost" data-action="buy" data-symbol-value="${escapeHtml(stock.symbol)}">매수</button>${stock.held ? `<button type="button" class="ghost" data-action="sell" data-symbol-value="${escapeHtml(stock.symbol)}">매도</button>` : ""}${watched.has(stock.symbol) ? `<button type="button" class="ghost" data-action="watch-remove" data-symbol-value="${escapeHtml(stock.symbol)}">관심 해제</button>` : ""}</div><ul>${stock.lines.map(line => `<li class="line-${line.tone}">${escapeHtml(line.text)}</li>`).join("")}</ul>${stock.exit_signal ? `<div class="chip-row" style="margin-top:6px"><span class="code">매도 신호</span>${signalChip(stock.exit_signal)}</div>${signalList(stock.exit_signal)}` : ""}${stock.news_loaded ? "" : '<div class="code">기사 논조는 종목 이름을 눌러 기사를 한 번 불러오면 함께 표시됩니다.</div>'}</div>`).join("")}</div>` : '<p class="analysis-meta" style="text-align:left">보유 종목도 관심 종목도 없습니다. 위에서 관심 종목을 추가해 보세요.</p>';
            } catch (error) { root.innerHTML = `<p class="error">종목 현황을 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`; }
        }
        // 로그인: 각자 자기 토스 키로 로그인합니다. 세션은 HttpOnly 쿠키라 화면 코드에서는 읽을 수 없습니다.
        let accountUser = null, authMeta = {};
        const KEY_GUIDE = `<details style="margin:10px 0"><summary>토스증권 API 키 만드는 법</summary><ol style="font-size:13px;line-height:1.7;margin:6px 0">
            <li>토스증권 PC(WTS) <strong>설정 &gt; Open API</strong>에서 키를 발급받아 client_id와 client_secret을 확인합니다.</li>
            <li>같은 화면의 <strong>허용 IP 관리</strong>에 이 서비스 서버의 공인 IP를 추가합니다(없으면 연결이 거절됩니다).</li>
            <li>아래에 두 값을 넣고 연결합니다. client_secret은 서버에 암호화해 저장되고, 화면에 다시 보여 주지 않습니다.</li>
            <li>이 키는 내 계좌의 조회와 주문 권한을 가집니다. 믿을 수 있는 서버에만 등록하고, 쓰지 않으면 "키 삭제"를 누르거나 토스 앱에서 키를 폐기하세요.</li></ol></details>`;
        function accountBar(user) {
            if (!user) return "";
            const trading = user.trading_enabled
                ? '<button type="button" class="ghost" data-action="trading-off">실제 주문 허용 끄기</button>'
                : `<input id="trading-password" type="password" placeholder="비밀번호 확인" aria-label="비밀번호 확인" autocomplete="current-password" style="width:140px"><button type="button" class="ghost" data-action="trading-on">실제 주문 허용 켜기</button>`;
            return `<div class="briefing-card" style="margin-bottom:12px"><div class="chip-row"><strong>${escapeHtml(user.username)}</strong><span class="chip">${user.toss_connected ? `토스 키 ${escapeHtml(user.toss_client_id)}` : "토스 키 없음"}</span><span class="chip ${user.trading_enabled ? "neg" : ""}">실제 주문 ${user.trading_enabled ? "허용" : "꺼짐"}</span>
                ${user.toss_connected ? trading + ' <button type="button" class="ghost" data-action="key-remove">키 삭제</button>' : ""}
                <button type="button" class="ghost" data-action="logout">로그아웃</button>
                <details style="display:inline-block"><summary class="code" style="cursor:pointer">회원 탈퇴</summary><input id="delete-password" type="password" placeholder="비밀번호" aria-label="탈퇴 비밀번호" autocomplete="current-password" style="width:120px"> <button type="button" class="danger" data-action="delete-account">탈퇴</button></details></div></div>`;
        }
        function renderAuth() {
            const invite = authMeta.invite_required ? '<input id="auth-invite" placeholder="초대 코드" aria-label="초대 코드" style="width:140px">' : "";
            document.getElementById("account-updated").textContent = "";
            document.getElementById("account-body").innerHTML = `<p class="briefing-summary">로그인하면 내 토스증권 계좌의 보유 종목을 보고 주문하며, 관심 종목 현황을 볼 수 있습니다. 계좌는 각자 자기 토스 API 키로 연결합니다.</p>
                <div class="order-form" style="margin:10px 0"><input id="auth-username" placeholder="아이디" aria-label="아이디" autocomplete="username" style="width:150px"><input id="auth-password" type="password" placeholder="비밀번호(8자 이상)" aria-label="비밀번호" autocomplete="current-password" style="width:170px">${invite}<button type="button" data-action="login">로그인</button>${authMeta.signup_enabled ? '<button type="button" class="ghost" data-action="signup">회원가입</button>' : ""}</div>
                <div id="auth-message"></div>${KEY_GUIDE}`;
        }
        function renderKeyForm() {
            document.getElementById("account-updated").textContent = "";
            document.getElementById("account-body").innerHTML = `${accountBar(accountUser)}<h3>토스증권 계좌 연결</h3>${KEY_GUIDE}
                <div class="order-form"><input id="key-client-id" placeholder="client_id" aria-label="client_id" autocomplete="off" style="width:240px"><input id="key-client-secret" type="password" placeholder="client_secret" aria-label="client_secret" autocomplete="off" style="width:240px"><button type="button" data-action="key-save">연결</button></div>
                <div id="auth-message"></div>
                <h3>관심 종목 현황 <span class="code">계좌를 연결하지 않아도 관심 종목은 볼 수 있습니다</span></h3>
                <div class="form-row" style="margin-bottom:4px"><input id="watch-symbol" placeholder="관심 종목 이름 또는 코드 (삼성전자, NVDA)" aria-label="관심 종목 이름 또는 코드" autocomplete="off" data-search="watch" style="width:260px"><button type="button" data-action="watch-add">관심 종목 추가</button></div><div class="search-results" id="watch-search"></div>
                <div id="stock-status"></div>`;
            loadStockStatus();
        }
        async function loadAccount() {
            const body = document.getElementById("account-body"), request = ++accountRequest;
            accountLoaded = true;
            body.innerHTML = '<p class="empty">계정을 확인하는 중입니다.</p>';
            try {
                const me = await fetchJson("/api/v1/auth/me");
                if (request !== accountRequest) return;
                authMeta = me;
                accountUser = me.logged_in ? me.user : null;
                if (!accountUser) { renderAuth(); return; }
                if (!accountUser.toss_connected) { renderKeyForm(); return; }
                body.innerHTML = '<p class="empty">토스증권 계좌를 불러오는 중입니다.</p>';
                const data = await fetchJson("/api/v1/account/summary");
                if (request === accountRequest) renderAccount(data);
            } catch (error) {
                if (request === accountRequest) body.innerHTML = `${accountBar(accountUser)}<p class="error">계좌를 불러오지 못했습니다. (${escapeHtml(error.message)})</p>`;
            }
        }
        async function authAction(action) {
            const message = document.getElementById("auth-message");
            const show = text => { if (message) message.innerHTML = `<p class="error" style="padding:6px 0">${escapeHtml(text)}</p>`; else alert(text); };
            try {
                if (action === "login" || action === "signup") {
                    await sendJson(`/api/v1/auth/${action}`, { username: document.getElementById("auth-username").value.trim(), password: document.getElementById("auth-password").value, invite_code: document.getElementById("auth-invite")?.value || "" });
                } else if (action === "logout") {
                    await sendJson("/api/v1/auth/logout", {});
                } else if (action === "key-save") {
                    if (message) message.innerHTML = '<p class="analysis-meta" style="text-align:left">토스에 연결해 키를 확인하는 중입니다.</p>';
                    await sendJson("/api/v1/auth/toss-key", { client_id: document.getElementById("key-client-id").value, client_secret: document.getElementById("key-client-secret").value });
                } else if (action === "key-remove") {
                    await sendJson("/api/v1/auth/toss-key", undefined, "DELETE");
                } else if (action === "trading-on") {
                    await sendJson("/api/v1/auth/trading", { enabled: true, password: document.getElementById("trading-password").value });
                } else if (action === "trading-off") {
                    await sendJson("/api/v1/auth/trading", { enabled: false });
                } else if (action === "delete-account") {
                    await sendJson("/api/v1/auth/delete-account", { password: document.getElementById("delete-password").value });
                }
                loadAccount();
            } catch (error) { show(error.message); }
        }
        function renderPreview(preview) {
            const box = document.getElementById("order-preview");
            const amount = preview.estimated_amount !== null ? moneyText(preview.estimated_amount, preview.currency) : "-";
            const available = preview.side === "BUY" ? `주문 가능 금액 ${moneyText(preview.buying_power, preview.currency)}` : `매도 가능 ${preview.sellable_quantity}주`;
            const problems = preview.problems.map(text => `<div class="problem">✕ ${escapeHtml(text)}</div>`).join("");
            const notes = preview.notes.map(text => `<div class="note">! ${escapeHtml(text)}</div>`).join("");
            const action = preview.confirm_token
                ? `<div style="margin-top:8px"><button type="button" class="danger" data-action="confirm" data-token="${escapeHtml(preview.confirm_token)}">실제로 ${escapeHtml(preview.side_label)} 주문 보내기</button> <button type="button" class="ghost" data-action="preview-cancel">취소</button> <span class="code">${preview.confirm_seconds}초 안에 눌러야 하며, 누르면 실제 계좌에서 주문이 체결됩니다.</span></div>`
                : !preview.trading_enabled ? '<div class="code" style="margin-top:8px">실제 주문이 꺼져 있어 미리보기까지만 됩니다.</div>'
                : '<div class="code" style="margin-top:8px">위 문제를 고친 뒤 다시 미리보기를 하세요.</div>';
            box.innerHTML = `<div class="order-preview"><strong>${escapeHtml(preview.name)}</strong> <span class="code">${escapeHtml(preview.symbol)}</span> <span class="chip ${preview.side === "BUY" ? "pos" : "neg"}">${escapeHtml(preview.side_label)}</span> <span class="chip">${escapeHtml(preview.order_type_label)}</span>
                <div>수량 ${preview.quantity}주 · 가격 ${preview.input_price_krw ? `${moneyText(preview.input_price_krw, "KRW")} → <strong>${moneyText(preview.price, "USD")}</strong>` : preview.price !== null && preview.price !== undefined ? moneyText(preview.price, preview.currency) : "시장가"} · 최근 종가 ${moneyText(preview.last_price, preview.currency)}</div>
                <div>예상 금액 <strong>${amount}</strong>${preview.estimated_amount_krw ? ` (약 ${moneyText(preview.estimated_amount_krw, "KRW")})` : ""} · ${available}${preview.fx_rate ? ` · 환율 1달러 = ${Number(preview.fx_rate).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원(참고용)` : ""}</div>${problems}${notes}${action}</div>`;
        }
        document.getElementById("account-refresh").addEventListener("click", () => loadAccount());
        // 종목코드를 넣으면 거래 통화를 보여 주고 가격 단위를 맞춥니다. 토스 주문은 국내 주식을 원, 미국 주식을 달러로만 받으며,
        // 미국 주식은 원으로 입력해도 서버가 환율로 달러로 바꿔 주문합니다.
        // 종목 이름 검색: 토스 전체 종목 목록에서 서버가 찾아 줍니다. 목록에서 누르면 주문 칸(또는 관심 종목)에 들어갑니다.
        let searchTimer = null, searchRequest = 0, pendingOrder = null;
        function stockSearch(input) {
            const kind = input.dataset.search, box = document.getElementById(`${kind}-search`), query = input.value.trim();
            clearTimeout(searchTimer);
            if (!box) return;
            if (!query) { box.innerHTML = ""; return; }
            searchTimer = setTimeout(async () => {
                const request = ++searchRequest;
                try {
                    const data = await fetchJson(`/api/v1/stocks/search?q=${encodeURIComponent(query)}`);
                    if (request !== searchRequest) return;
                    box.innerHTML = data.results.length
                        ? data.results.map(item => `<button type="button" class="ghost" data-action="pick-stock" data-kind="${kind}" data-symbol-value="${escapeHtml(item.symbol)}">${escapeHtml(item.name)} <span class="code">${escapeHtml(item.symbol)} · ${escapeHtml(item.market)}</span></button>`).join("")
                        : "";  // 결과가 없으면 아무것도 보여 주지 않습니다(입력 중간에 문구가 깜빡이지 않게)
                } catch (error) { if (request === searchRequest) box.innerHTML = ""; }
            }, 250);
        }
        function fillOrder(symbol, side, quantity) {
            const input = document.getElementById("order-symbol");
            if (!input) { pendingOrder = { symbol, side, quantity }; return; }
            input.value = symbol;
            if (side) document.getElementById("order-side").value = side;
            document.getElementById("order-quantity").value = quantity || "";
            document.getElementById("order-search").innerHTML = "";
            loadOrderQuote();
            input.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        document.getElementById("account-body").addEventListener("input", event => { if (event.target.dataset?.search) stockSearch(event.target); });
        document.getElementById("recommend-body").addEventListener("click", event => {
            const button = event.target.closest("[data-open-order]");
            if (!button) return;
            event.stopPropagation();
            pendingOrder = { symbol: button.dataset.openOrder, side: "BUY" };
            setView("account");
            if (document.getElementById("order-symbol")) { const order = pendingOrder; pendingOrder = null; fillOrder(order.symbol, order.side); }
        }, true);
        let quoteRequest = 0;
        async function loadOrderQuote() {
            const input = document.getElementById("order-symbol"), note = document.getElementById("order-quote"), unit = document.getElementById("order-price-currency");
            if (!input || !note) return;
            const symbol = input.value.trim(), request = ++quoteRequest;
            if (!symbol) { note.textContent = "종목코드를 넣으면 거래 통화와 최근 가격이 나옵니다."; unit.disabled = false; return; }
            note.textContent = "종목을 확인하는 중입니다.";
            try {
                const quote = await fetchJson(`/api/v1/account/quote?symbol=${encodeURIComponent(symbol)}`);
                if (request !== quoteRequest) return;
                if (quote.currency === "KRW") {
                    unit.value = "KRW"; unit.disabled = true;
                    note.textContent = `${quote.name}: 원으로 거래하는 국내 주식 · 최근 종가 ${moneyText(quote.last_price, "KRW")} · 가격은 원 단위 정수로 입력`;
                } else {
                    unit.disabled = false;
                    if (unit.value !== "KRW") unit.value = "USD";
                    const fx = quote.fx ? ` · 1달러 = ${Number(quote.fx.mid).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원(참고용)` : "";
                    const krw = quote.fx && quote.last_price ? ` (약 ${moneyText(quote.last_price * quote.fx.mid, "KRW")})` : "";
                    note.textContent = `${quote.name}: 달러로 거래하는 미국 주식 · 최근 종가 ${moneyText(quote.last_price, "USD")}${krw}${fx} · 가격을 원으로 넣으면 달러로 바꿔 주문합니다`;
                }
            } catch (error) { if (request === quoteRequest) note.textContent = `종목 정보를 불러오지 못했습니다. (${error.message})`; }
        }
        document.getElementById("account-body").addEventListener("change", event => { if (event.target.id === "order-symbol") loadOrderQuote(); });
        document.getElementById("account-body").addEventListener("click", async event => {
            const target = event.target.closest("[data-action], [data-symbol]");
            if (!target) return;
            const action = target.dataset.action, box = document.getElementById("order-preview");
            if (["login", "signup", "logout", "key-save", "key-remove", "trading-on", "trading-off", "delete-account"].includes(action)) { authAction(action); return; }
            try {
                if (!action) { showHistory(target.dataset.symbol); return; }
                if (action === "sell" || action === "buy") {
                    fillOrder(target.dataset.symbolValue, action === "sell" ? "SELL" : "BUY", target.dataset.quantity);
                } else if (action === "pick-stock") {
                    const kind = target.dataset.kind;
                    document.getElementById(`${kind}-search`).innerHTML = "";
                    if (kind === "order") { fillOrder(target.dataset.symbolValue, null); }
                    else {
                        document.getElementById("watch-symbol").value = "";
                        await sendJson("/api/v1/watchlist", { symbol: target.dataset.symbolValue });
                        loadStockStatus();
                    }
                } else if (action === "preview") {
                    if (!/^[A-Za-z0-9.-]{1,15}$/.test(document.getElementById("order-symbol").value.trim())) {
                        // 이름만 넣은 경우 종목을 추측해 주문하지 않습니다(같은 이름이 여러 개일 수 있음).
                        box.innerHTML = '<div class="order-preview"><div class="problem">✕ 종목 이름을 넣었다면 위 검색 목록에서 종목을 눌러 골라 주세요.</div></div>';
                        return;
                    }
                    box.innerHTML = '<p class="analysis-meta" style="text-align:left">주문 내용을 확인하는 중입니다.</p>';
                    const price = document.getElementById("order-price").value;
                    renderPreview(await sendJson("/api/v1/account/orders/preview", {
                        symbol: document.getElementById("order-symbol").value.trim(), side: document.getElementById("order-side").value,
                        order_type: document.getElementById("order-type").value, quantity: Number(document.getElementById("order-quantity").value),
                        price: document.getElementById("order-type").value === "LIMIT" && price ? Number(price) : null,
                        price_currency: document.getElementById("order-price-currency").value,
                    }));
                } else if (action === "history") {
                    await loadOrderHistory();
                } else if (action === "preview-cancel") {
                    box.innerHTML = "";
                } else if (action === "confirm") {
                    target.disabled = true;
                    const result = await sendJson("/api/v1/account/orders/confirm", { confirm_token: target.dataset.token });
                    box.innerHTML = `<div class="order-preview"><strong>주문을 보냈습니다.</strong> ${escapeHtml(result.name)} ${escapeHtml(result.side === "BUY" ? "매수" : "매도")} ${result.quantity}주 · 주문번호 <span class="code">${escapeHtml(result.order_id || "-")}</span><div class="code">체결 여부는 아래 미체결 주문과 보유 종목에서 확인하세요.</div></div>`;
                    setTimeout(loadAccount, 1500);
                } else if (action === "cancel") {
                    if (target.dataset.armed !== "1") { target.dataset.armed = "1"; target.textContent = "정말 취소?"; target.classList.add("danger"); return; }
                    target.disabled = true;
                    await sendJson(`/api/v1/account/orders/${encodeURIComponent(target.dataset.order)}/cancel`, {});
                    setTimeout(loadAccount, 1000);
                } else if (action === "watch" || action === "watch-add") {
                    let symbol = action === "watch" ? target.dataset.symbolValue : document.getElementById("watch-symbol").value.trim();
                    if (!symbol) return;
                    if (action === "watch-add" && !/^[A-Za-z0-9.-]{1,15}$/.test(symbol)) {
                        // 이름을 넣고 바로 추가를 누르면 검색 결과 첫 종목을 담습니다.
                        const found = (await fetchJson(`/api/v1/stocks/search?q=${encodeURIComponent(symbol)}`)).results[0];
                        if (!found) throw new Error("찾는 종목이 없습니다.");
                        symbol = found.symbol;
                        document.getElementById("watch-search").innerHTML = "";
                    }
                    await sendJson("/api/v1/watchlist", { symbol });
                    if (action === "watch-add") document.getElementById("watch-symbol").value = "";
                    loadStockStatus();
                } else if (action === "watch-remove") {
                    await sendJson(`/api/v1/watchlist/${encodeURIComponent(target.dataset.symbolValue)}`, undefined, "DELETE");
                    loadStockStatus();
                }
            } catch (error) {
                if (["preview", "confirm"].includes(action)) box.innerHTML = `<div class="order-preview"><div class="problem">✕ ${escapeHtml(error.message)}</div></div>`;
                else alertInline(target, error.message);
                target.disabled = false;
            }
        });
        function alertInline(target, message) {
            const note = document.createElement("div");
            note.className = "error"; note.style.padding = "4px 0"; note.textContent = message;
            target.insertAdjacentElement("afterend", note);
            setTimeout(() => note.remove(), 5000);
        }
        // 매도 신호(app/exit_signals.py): 매도 고려(고점 대비 −10%) > 주의(손실 확대·근거 약화·추세 이탈·이익 실현 고려) > 보유 유지.
        function signalChip(signal) {
            if (!signal) return "";
            const cls = signal.level === "sell" ? "neg" : signal.level === "watch" ? "" : "pos";
            const title = (signal.signals || []).map(item => `${item.label}: ${item.text}`).join(" · ") || "매도 신호 없음";
            return `<span class="chip ${cls}" title="${escapeHtml(title)}"${signal.level === "watch" ? ' style="background:#fffaeb;color:#b54708"' : ""}>${escapeHtml(signal.label)}</span>`;
        }
        function signalList(signal) {
            if (!signal || !(signal.signals || []).length) return "";
            return `<ul style="margin:4px 0 0;padding-left:16px;font-size:12px;line-height:1.5">${signal.signals.map(item => `<li class="${item.level === "sell" ? "negative" : ""}"><strong>${escapeHtml(item.label)}</strong> ${escapeHtml(item.text)}</li>`).join("")}</ul>`;
        }
        // 가상 투자: ① 과거로 가상 투자(백테스트를 금액으로) ② 오늘부터 추천대로 가상 포트폴리오.
        let virtualLoaded = false;
        function manwon(value) {
            if (value === null || value === undefined) return "-";
            const abs = Math.abs(value);
            const text = abs >= 1e8 ? `${(abs / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억원` : `${(abs / 1e4).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만원`;
            return value < 0 ? `-${text}` : text;
        }
        function signedManwon(value) { return value === null || value === undefined ? "-" : `${value > 0 ? "+" : ""}${manwon(value)}`; }
        function equityChart(points, keys) {
            const usable = points.filter(point => keys.some(([key]) => point[key] !== null && point[key] !== undefined));
            if (usable.length < 2) return "";
            const values = usable.flatMap(point => keys.map(([key]) => point[key]).filter(value => value !== null && value !== undefined));
            const min = Math.min(...values), max = Math.max(...values), span = max - min || 1, width = 640, height = 180, pad = 10;
            const xy = (value, index) => `${(pad + (width - 2 * pad) * index / (usable.length - 1)).toFixed(1)},${(pad + (height - 2 * pad) * (1 - (value - min) / span)).toFixed(1)}`;
            const lines = keys.map(([key, color, dashed]) => {
                const pts = usable.map((point, index) => point[key] === null || point[key] === undefined ? null : xy(point[key], index)).filter(Boolean);
                return pts.length > 1 ? `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" ${dashed ? 'stroke-dasharray="5 4"' : ""} vector-effect="non-scaling-stroke"></polyline>` : "";
            }).join("");
            return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="자산 흐름" style="height:180px">${lines}</svg>
                <div class="code" style="display:flex;justify-content:space-between"><span>${escapeHtml(usable[0].date)}</span><span>${keys.map(([, color, dashed, label]) => `<span style="color:${color}">${dashed ? "┅" : "━"} ${escapeHtml(label)}</span>`).join(" · ")}</span><span>${escapeHtml(usable.at(-1).date)}</span></div>`;
        }
        async function loadVirtual() {
            virtualLoaded = true;
            const form = document.getElementById("vp-form"), list = document.getElementById("vp-list");
            try {
                const [me, defaults] = await Promise.all([fetchJson("/api/v1/auth/me"), fetchJson("/api/v1/virtual/defaults")]);
                if (!me.logged_in) {
                    form.innerHTML = '<p class="analysis-meta" style="text-align:left">가상 포트폴리오는 계정에 저장되므로 로그인이 필요합니다. <button type="button" class="ghost" data-virtual-action="go-login">내 계좌 탭에서 로그인</button></p>';
                    list.innerHTML = "";
                    return;
                }
                form.innerHTML = `<div class="order-form"><select id="vp-market" aria-label="시장"><option value="kr">국장</option><option value="us">미장</option></select><input id="vp-capital" type="number" min="100000" step="100000" value="10000000" aria-label="투자 금액(원)" style="width:150px"><span class="code">원</span><select id="vp-source" aria-label="따라 살 추천"><option value="all">통합 추천 (AI + 테마)</option><option value="ai">AI 추천 종목만</option><option value="theme">추천 테마 종목만(근거 점수 순)</option><option value="compare">세 가지 모두 만들어 비교</option></select><label class="code"><input id="vp-autosell" type="checkbox" checked> 매도 신호가 나면 판 것으로 처리</label><label class="code" title="종료일 없이 계속: 매도 신호로 판 돈으로 다음 거래일 시가에 최신 AI분석 추천 종목을 삽니다"><input id="vp-managed" type="checkbox"> 자동 운용(팔면 최신 추천으로 다시 사기)</label><label class="code" title="새 AI분석이 나오면 추천에서 빠진 보유 종목을 팔고 새 추천으로 바꿉니다"><input id="vp-rotate" type="checkbox"> 새 추천으로 교체</label><input id="vp-name" maxlength="40" placeholder="이름 (비워 두면 자동)" aria-label="포트폴리오 이름" style="width:180px"><label class="code">종료일 <input id="vp-end" type="date" value="${escapeHtml(defaults.next_tuesday)}" min="${escapeHtml(defaults.today)}" aria-label="종료일"></label><button type="button" data-virtual-action="create">가상 포트폴리오 만들기</button></div>
                    <div class="code" style="margin-top:6px">통합 추천은 AI와 테마 양쪽에서 모두 추천된 종목 → 나머지 AI 추천 → 테마 종목(근거 점수 순)으로 최대 5종목을 고릅니다. 종료일 기본값은 다음 화요일(${escapeHtml(defaults.next_tuesday)})이고, 미장은 만들 때 환율로 달러로 바꿉니다.</div><div id="vp-message"></div>`;
                await loadPortfolios();
            } catch (error) { form.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
        }
        async function loadPortfolios() {
            const list = document.getElementById("vp-list");
            list.innerHTML = '<p class="analysis-meta" style="text-align:left">가상 포트폴리오를 평가하는 중입니다.</p>';
            try {
                const data = await fetchJson("/api/v1/virtual/portfolios");
                if (!data.portfolios.length) { list.innerHTML = '<p class="analysis-meta" style="text-align:left">아직 만든 가상 포트폴리오가 없습니다.</p>'; return; }
                // "세 가지 모두 비교"로 함께 만든 포트폴리오는 비교 표를 먼저 보여 줍니다.
                const groups = new Map();
                data.portfolios.forEach(portfolio => { const key = portfolio.group || portfolio.id; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(portfolio); });
                list.innerHTML = portfolioSummary(data.portfolios) + [...groups.values()].map(items => (items.length > 1 ? compareTable(items, data.fx_now) : "") + items.map(portfolio => portfolioCard(portfolio, data.fx_now)).join("")).join("");
            } catch (error) { list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
        }
        // 내 가상 포트폴리오 전체를 한 표로. 이름을 누르면 그 카드로 이동합니다.
        function portfolioSummary(items) {
            const label = { all: "통합", ai: "AI만", theme: "테마만" };
            const state = { pending: "체결 대기", active: "진행 중", finished: "종료" };
            const stateText = item => item.status === "active" && item.live ? "진행 중 · 장중" : (state[item.status] || item.status);
            const rows = items.map(item => {
                const beat = item.status === "pending" || item.index_return === null || item.index_return === undefined ? null : item.return - item.index_return;
                return `<tr><td><button type="button" class="ghost" data-virtual-action="jump" data-id="${escapeHtml(item.id)}" style="padding:2px 8px">${escapeHtml(item.name || "")}</button></td><td>${item.market === "KR" ? "국장" : "미장"}</td><td>${escapeHtml(label[item.source] || item.source)}</td><td>${escapeHtml(item.created_date)} → ${escapeHtml(item.end_date)}</td><td>${stateText(item)}</td><td class="${pctClass(item.return)}">${item.status === "pending" ? "-" : pct(item.return)}</td><td class="${pctClass(beat)}">${beat === null ? "-" : `${beat > 0 ? "+" : ""}${(beat * 100).toFixed(2)}%p`}</td></tr>`;
            }).join("");
            return `<div class="briefing-card" style="margin-top:10px"><h4>내 가상 포트폴리오 ${items.length}개 <span class="code">최대 50개 · 이름을 누르면 아래 카드로 이동</span></h4>
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>이름</th><th>시장</th><th>방식</th><th>기간</th><th>상태</th><th>수익률</th><th>지수 대비</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
        }
        function compareTable(items, fxNow) {
            const first = items[0], usd = first.currency === "USD";
            const money = value => value === null || value === undefined ? "-" : usd ? moneyText(value, "USD") : manwon(value);
            const label = { all: "통합 추천", ai: "AI 추천 종목만", theme: "추천 테마 종목만" };
            const ready = items.filter(item => item.status !== "pending");
            const best = ready.length ? ready.reduce((top, item) => item.return > top.return ? item : top) : null;
            const rows = items.map(item => `<tr><td>${escapeHtml(label[item.source] || item.source)}${best && best.id === item.id ? ' <span class="chip pos">가장 좋음</span>' : ""}</td><td>${item.picks.length}종목</td><td>${item.status === "pending" ? "체결 대기" : money(item.value)}</td><td class="${pctClass(item.return)}">${item.status === "pending" ? "-" : pct(item.return)}</td><td class="${pctClass(item.return - (item.index_return ?? 0))}">${item.status === "pending" || item.index_return === null || item.index_return === undefined ? "-" : `${item.return - item.index_return > 0 ? "+" : ""}${((item.return - item.index_return) * 100).toFixed(2)}%p`}</td></tr>`).join("");
            const index = ready[0] ? `<tr><td>같은 돈을 ${escapeHtml(ready[0].index_name)}에</td><td>-</td><td>${money(ready[0].index_value)}</td><td class="${pctClass(ready[0].index_return)}">${pct(ready[0].index_return)}</td><td>-</td></tr>` : "";
            return `<div class="briefing-card" style="margin-top:10px"><h4>세 가지 비교 · ${escapeHtml(first.market === "KR" ? "국장" : "미장")} · ${escapeHtml(first.created_at)} 만듦 · ${first.open_ended ? "계속 운용 중" : `${escapeHtml(first.end_date)} 종가까지`} · 각 ${money(first.capital)}</h4>
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>추천 방식</th><th>종목 수</th><th>평가액</th><th>수익률</th><th>지수 대비</th></tr></thead><tbody>${rows}${index}</tbody></table></div></div>`;
        }
        function clip(text, size = 40) {
            const value = String(text || "");
            return value.length > size ? `${value.slice(0, size)}…` : value;
        }
        function portfolioCard(portfolio, fxNow) {
            const usd = portfolio.currency === "USD";
            const money = value => value === null || value === undefined ? "-" : usd ? `${moneyText(value, "USD")}${fxNow ? ` (약 ${manwon(value * fxNow)})` : ""}` : manwon(value);
            const status = { pending: ["체결 대기", ""], active: [portfolio.live ? "진행 중 · 장중" : "진행 중", "pos"], finished: ["종료", "neg"] }[portfolio.status] || [portfolio.status, ""];
            const sourceLabel = { all: "통합 추천", ai: "AI 추천 종목만", theme: "추천 테마 종목만" }[portfolio.source] || portfolio.source;
            const head = `<div class="chip-row"><strong style="font-size:15px">${escapeHtml(portfolio.name || sourceLabel)}</strong><span class="chip ${status[1]}">${status[0]}</span><span class="code">${escapeHtml(portfolio.market === "KR" ? "국장" : "미장")} · ${escapeHtml(sourceLabel)} · ${escapeHtml(portfolio.created_at)} 만듦 · ${portfolio.open_ended ? "종료일 없이 계속 운용 중" : `${escapeHtml(portfolio.end_date)} 종가까지`}${portfolio.original_end_date && (portfolio.open_ended || portfolio.original_end_date !== portfolio.end_date) ? ` (처음 종료일 ${escapeHtml(portfolio.original_end_date)})` : ""} · ${money(portfolio.capital)}</span>${portfolio.managed ? `<span class="chip pos">자동 운용${portfolio.managed.rotate ? " · 새 추천 교체" : ""}</span>` : ""}</div>
                <div class="chip-row" style="margin:6px 0"><span class="rename-box" data-id="${escapeHtml(portfolio.id)}" hidden><input maxlength="40" value="${escapeHtml(portfolio.name || "")}" aria-label="새 이름" style="width:180px"><button type="button" class="ghost" data-virtual-action="rename-save" data-id="${escapeHtml(portfolio.id)}">저장</button></span>
                <button type="button" class="ghost" data-virtual-action="rename" data-id="${escapeHtml(portfolio.id)}">이름 바꾸기</button>
                <span class="end-box" data-id="${escapeHtml(portfolio.id)}" hidden><input type="date" value="${escapeHtml(portfolio.end_date)}" min="${escapeHtml(portfolio.created_date)}" aria-label="새 종료일"><button type="button" class="ghost" data-virtual-action="end-today" data-id="${escapeHtml(portfolio.id)}" title="오늘 종가(장중이면 지금 가격)까지 들고 있었다면">오늘까지</button><button type="button" class="ghost" data-virtual-action="end-save" data-id="${escapeHtml(portfolio.id)}">저장</button>${portfolio.group ? '<span class="code">세 가지 비교 묶음은 함께 바뀝니다</span>' : ""}</span>
                <button type="button" class="ghost" data-virtual-action="end" data-id="${escapeHtml(portfolio.id)}" title="만든 뒤에 종료일을 늘리거나 줄여, 그때까지 들고 있었다면 어땠는지 봅니다">${portfolio.open_ended ? "운용 끝내기(종료일 정하기)" : "종료일 바꾸기"}</button>
                ${portfolio.managed
                    ? `<button type="button" class="ghost" data-virtual-action="managed-off" data-id="${escapeHtml(portfolio.id)}" title="처음 산 종목 그대로, 정한 종료일까지 보는 원래 방식으로 돌아갑니다">자동 운용 끄기</button>`
                    : `<span class="managed-box" data-id="${escapeHtml(portfolio.id)}" hidden><label class="code"><input type="checkbox" class="managed-rotate"> 새 추천이 나오면 빠진 종목 교체</label><button type="button" class="ghost" data-virtual-action="managed-on" data-id="${escapeHtml(portfolio.id)}">켜기</button>${portfolio.group ? '<span class="code">세 가지 비교 묶음은 함께 켜집니다</span>' : ""}</span><button type="button" class="ghost" data-virtual-action="managed" data-id="${escapeHtml(portfolio.id)}" title="만든 날부터 이 규칙이었다면으로 다시 계산하고, 종료일 없이 계속 이어 갑니다">자동 운용 켜기</button>`}
                <button type="button" class="ghost" data-virtual-action="duplicate" data-id="${escapeHtml(portfolio.id)}" data-market="${escapeHtml(portfolio.market.toLowerCase())}" data-capital="${escapeHtml(String(portfolio.capital_krw))}" data-source="${escapeHtml(portfolio.source)}" data-end="${escapeHtml(portfolio.end_date)}" data-name="${escapeHtml(portfolio.name || "")}" data-managed="${portfolio.managed ? "1" : ""}" data-rotate="${portfolio.managed?.rotate ? "1" : ""}" title="시장·금액·방식·종료일이 같은 포트폴리오를 오늘 추천대로 하나 더 만듭니다">같은 설정으로 하나 더</button>
                <button type="button" class="ghost" data-virtual-action="delete" data-id="${escapeHtml(portfolio.id)}">삭제</button></div>`;
            const viaText = pick => (pick.via || []).map(via => via === "ai" ? "AI" : "테마").join("·");
            const rules = portfolio.rules;
            const managedLine = portfolio.managed ? `<p class="code" style="margin:4px 0">자동 운용(판단 모듈): 서버가 장중 5분마다 확인합니다.
                <br>· 팔기: 고점 대비 −${Math.round(((rules && rules.trail) || 0.1) * 100)}%(산 뒤 +5% 넘게 오른 뒤, 약세장 −7%) · 위험 종목 지정 · 하루 한 번 AI 점검의 매도 의견은 장중 언제든, 추천에서 빠지고 20일선 아래로 무너짐 · 테마 흐름 하락 전환 · 이익 실현(+25%·RSI 80)은 마감 무렵에만
                <br>· 사기: 판 돈으로 다음 확인 때 가장 최근 AI분석 추천(${escapeHtml(sourceLabel)}) 중에서 위험 지정·단기 과열·근거 50점 미만(약세장 60점)·하락 흐름 테마는 빼고 근거 점수·AI 추천·20일선·RSI로 순서를 매겨 삽니다. 약세장(지수 60일선 아래)이면 절반만 사고 나머지는 시장이 회복될 때까지 남겨 둡니다(방금 판 종목은 다음 추천까지 제외)${portfolio.reserve_cash ? ` · 약세장이라 남겨 둔 돈 ${money(portfolio.reserve_cash)}` : ""}${portfolio.intraday ? `${portfolio.ledger_updated_at ? ` · 마지막 확인 ${escapeHtml(portfolio.ledger_updated_at)}` : ""}` : " · 자동 운용을 켜기 전 기간은 일봉으로 계산(시가 매수·종가 매도)"}${portfolio.managed.rotate ? (portfolio.requested_count ? " · 새 추천이 나오면 추천 상위에서 빠진 종목은 시가에 팔고 새 추천으로 교체" : " · 새 추천이 나오면 추천에서 빠진 종목은 시가에 팔고, 그 돈을 새 추천 종목(개수는 AI가 고른 대로)에 똑같이 나눠 삼") : ""} · 판 돈만 다시 투자하고 처음 남긴 현금은 그대로${portfolio.used_snapshot ? ` · 지금 쓰는 추천: ${escapeHtml(portfolio.used_snapshot)}` : ""}${portfolio.waiting_cash ? ` · 살 추천 종목이 없어 기다리는 돈 ${money(portfolio.waiting_cash)}` : ""}</p>` : "";
            const ruleLine = managedLine + (rules ?`<p class="code" style="margin:4px 0">적용 규칙: 변동성 반비례 비중(한 종목 최대 25%)${rules.filtered ? " · 5거래일 이하 보유라 단기 과열·근거 약한 종목 제외" : ""}${rules.weak_market ? ` · 만든 날 지수가 60일 평균선 아래(약세장)라 ${Math.round((rules.invest_fraction || 0.5) * 100)}%만 투자하고 나머지는 현금` : ""} · ${rules.auto_sell ? "매도 신호(고점 대비 −10%)가 나면 판 것으로 처리" : "매도 신호는 표시만"}${(portfolio.excluded || []).length ? ` · 제외: ${portfolio.excluded.map(item => `${escapeHtml(item.name)}(${escapeHtml(item.reason)})`).join(", ")}` : ""}</p>`
                : `<p class="code" style="margin:4px 0">예전 방식으로 만든 포트폴리오(처음 종목은 같은 금액${portfolio.managed ? ")" : ", 자동 매도 없음) · 매도 신호는 표시만 합니다"}.</p>`);
            const actionLabel = trade => trade.action === "buy" ? '<span class="chip pos">매수</span>' : '<span class="chip neg">매도</span>';
            const tradeRows = (portfolio.trades || []).slice().reverse().map(trade => `<tr><td>${escapeHtml(trade.time || trade.date)}</td><td>${actionLabel(trade)}</td><td>${escapeHtml(trade.name)} <span class="code">${escapeHtml(trade.symbol)}</span></td><td>${trade.shares.toLocaleString("ko-KR")}주</td><td>${moneyText(trade.price, portfolio.currency)}</td><td class="code">${escapeHtml(trade.reason)}</td><td class="${pctClass(trade.return)}">${trade.return === undefined ? "-" : pct(trade.return)}</td></tr>`).join("");
            const plan = portfolio.managed ? portfolio.plan : null;
            const planRows = plan ? (plan.orders || []).map(order => `<tr><td>${order.action === "buy" ? '<span class="chip pos">매수</span>' : '<span class="chip neg">매도</span>'}</td><td>${escapeHtml(order.name)} <span class="code">${escapeHtml(order.symbol)}</span></td><td>${Number(order.shares).toLocaleString("ko-KR")}주(예상)</td><td>${moneyText(order.ref_price, portfolio.currency)}</td><td class="code">${order.action === "buy" ? `시가 ${moneyText(order.limit, portfolio.currency)} 이하면 매수` : "시가에 매도"}</td><td class="code">${escapeHtml(order.reason)}</td></tr>`).join("") : "";
            const planBox = plan ? `<div class="briefing-card" style="margin:8px 0;padding:10px"><strong>${plan.executed ? `예약 주문 · ${escapeHtml(plan.for_day)} 시가 · ${escapeHtml(plan.executed_at || "")} 체결` : `다음 장 예약 주문 · ${escapeHtml(plan.for_day)} 시가`}</strong>
                <span class="code"> · ${escapeHtml(plan.created_at || "")}에 ${plan.based_on ? `추천(${escapeHtml(plan.based_on)})과 ` : ""}AI 점검·시장 상황으로 정함 · 매수는 시가가 전날 종가보다 +3% 넘게 높으면 건너뜀</span>
                ${planRows ? `<div class="table-wrap"><table class="sim-table"><thead><tr><th>구분</th><th>종목</th><th>수량</th><th>전날 종가</th><th>조건</th><th>이유</th></tr></thead><tbody>${planRows}</tbody></table></div>` : '<p class="code" style="margin:4px 0">바꿀 종목이 없어 예약 주문이 없습니다(지금 보유 그대로).</p>'}
                ${(plan.skipped || []).length ? `<p class="code" style="margin:4px 0">건너뜀: ${plan.skipped.map(item => `${escapeHtml(item.name)}(${escapeHtml(item.reason)})`).join(", ")}</p>` : ""}</div>` : "";
            const tradeTable = portfolio.managed && tradeRows ? `<details style="margin-top:8px"><summary style="cursor:pointer">매매 내역 ${portfolio.trades.length}건 <span class="code">언제·무엇을·왜 사고팔았는지(최근 순)</span></summary><div class="table-wrap"><table class="sim-table"><thead><tr><th>날짜·시각</th><th>구분</th><th>종목</th><th>수량</th><th>가격</th><th>이유</th><th>수익률</th></tr></thead><tbody>${tradeRows}</tbody></table></div></details>` : "";
            if (portfolio.status === "pending") {
                return `<div class="briefing-card" id="vp-${escapeHtml(portfolio.id)}" style="margin-top:10px">${head}${ruleLine}<p class="briefing-summary">투자 금액 ${money(portfolio.capital)} · ${escapeHtml(portfolio.message || "")}</p><div class="chip-row">${portfolio.picks.map(pick => `<span class="chip">${escapeHtml(pick.name)}${pick.via?.length ? ` <span class="code">${viaText(pick)}</span>` : ""}</span>`).join("")}</div></div>`;
            }
            const beat = portfolio.index_return !== null && portfolio.index_return !== undefined ? portfolio.return - portfolio.index_return : null;
            const holdings = portfolio.holdings.map(holding => ["bought", "sold"].includes(holding.status)
                ? `<tr><td>${escapeHtml(holding.name)} <span class="code">${escapeHtml(holding.symbol)}${holding.via?.length ? ` · ${viaText(holding)}` : ""}${holding.weight !== undefined ? ` · 비중 ${Math.round(holding.weight * 100)}%` : ""}</span>${holding.buy_reason && holding.buy_reason !== "처음 매수" ? `<span class="reason-line" title="${escapeHtml(holding.buy_reason)}">산 이유: ${escapeHtml(clip(holding.buy_reason, 60))}</span>` : ""}${signalList(holding.signal)}</td><td>${holding.shares.toLocaleString("ko-KR")}주</td><td>${escapeHtml(holding.buy_time || holding.buy_date)} ${moneyText(holding.buy_price, portfolio.currency)}</td><td>${moneyText(holding.last_price, portfolio.currency)}${holding.status === "sold" ? ` <span class="chip neg">${escapeHtml((holding.sold_time || holding.sold_date).slice(5))} 매도</span><span class="reason-line" title="${escapeHtml(holding.exit_reason || "")}" style="margin-left:auto">${escapeHtml(clip(holding.exit_reason || "", 60))}</span>` : ""}</td><td class="${pctClass(holding.return)}">${pct(holding.return)}</td><td class="${pctClass(holding.profit)}">${usd ? moneyText(holding.profit, "USD") : signedManwon(holding.profit)}</td><td>${holding.status === "sold" ? '<span class="chip">판매 완료</span>' : signalChip(holding.signal)}</td></tr>`
                : `<tr><td>${escapeHtml(holding.name)}</td><td colspan="6" class="code">${holding.status === "too_expensive" ? "1주 값이 배정 금액보다 비싸 사지 못함" : "체결 대기"}</td></tr>`).join("");
            const signalNote = portfolio.signal_counts && (portfolio.signal_counts.sell || portfolio.signal_counts.watch) && portfolio.status !== "finished"
                ? `<div class="sim-verdict ${portfolio.signal_counts.sell ? "bad" : "mixed"}">매도 신호: 매도 고려 ${portfolio.signal_counts.sell}종목 · 주의 ${portfolio.signal_counts.watch}종목 — 아래 종목 표의 근거를 확인하세요.</div>` : "";
            return `<div class="briefing-card" id="vp-${escapeHtml(portfolio.id)}" style="margin-top:10px">${head}${ruleLine}${signalNote}
                <div class="sim-verdict ${portfolio.profit >= 0 ? "good" : "bad"}"><strong>${money(portfolio.capital)} → ${money(portfolio.value)}</strong> (${pct(portfolio.return)}, ${usd ? moneyText(portfolio.profit, "USD") : signedManwon(portfolio.profit)}) · ${escapeHtml(portfolio.as_of)} ${portfolio.live ? "장중 가격 기준(약 5분마다 갱신)" : "종가 기준"}${portfolio.status === "finished" ? " · 최종" : ""}</div>
                <div class="stat-grid">
                    <div class="stat"><small>같은 돈을 ${escapeHtml(portfolio.index_name)}에 넣었다면</small><span class="sim-big">${money(portfolio.index_value)}</span><small class="${pctClass(portfolio.index_return)}">${pct(portfolio.index_return)}</small></div>
                    <div class="stat"><small>지수 대비</small><span class="sim-big ${pctClass(beat)}">${beat === null ? "-" : `${beat > 0 ? "+" : ""}${(beat * 100).toFixed(2)}%p`}</span><small>${beat === null ? "" : beat > 0 ? "지수보다 나음" : "지수보다 못함"}</small></div>
                    <div class="stat"><small>남은 현금(정수 주로 사고 남은 돈)</small><span class="sim-big">${money(portfolio.cash_left)}</span></div>
                </div>
                ${equityChart(portfolio.series, [["value", "#d33d4d", false, "내 가상 포트폴리오"], ["index_value", "#2672c8", true, portfolio.index_name]])}
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>종목</th><th>수량</th><th>매수(시가)</th><th>현재가</th><th>수익률</th><th>손익</th><th>매도 신호</th></tr></thead><tbody>${holdings}</tbody></table></div>
                ${planBox}
                ${tradeTable}
                <p class="code" style="margin-top:6px">평가액은 지금 팔면 받을 금액(매도 비용 ${(0.15).toFixed(2)}% 차감)입니다.</p></div>`;
        }
        function renderVirtualBacktest(root, data) {
            const usd = data.currency === "USD";
            const krw = value => value === null || value === undefined ? null : usd ? value * data.fx : value;
            const won = value => manwon(krw(value));
            const profit = krw(data.profit);
            const verdictClass = data.profit > 0 ? (data.return >= (data.index_return ?? -1) ? "good" : "mixed") : "bad";
            const periodRows = data.periods.slice().reverse().map(period => `<details style="border-bottom:1px solid #edf0f4;padding:6px 0"><summary style="cursor:pointer;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><span>${escapeHtml(period.date)} → ${escapeHtml(period.end)}</span><span>${won(period.start_value)} → <strong>${won(period.end_value)}</strong> <span class="${pctClass(period.return)}">${pct(period.return)}</span></span></summary>
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>종목</th><th>근거</th><th>비중</th><th>주수</th><th>매수가</th><th>매도가</th><th>손익</th></tr></thead><tbody>${period.trades.map(trade => `<tr><td>${escapeHtml(trade.name)}</td><td>${trade.score}점</td><td>${trade.weight !== undefined ? `${Math.round(trade.weight * 100)}%` : "-"}</td><td>${trade.shares.toLocaleString("ko-KR")}주</td><td>${moneyText(trade.buy_price, data.currency)}</td><td>${moneyText(trade.sell_price, data.currency)}${trade.exit ? ` <span class="chip neg" title="매도 신호가 나서 보유 기간 전에 팜">${escapeHtml(trade.sell_date)} 고점 대비 매도</span>` : ""}</td><td class="${pctClass(trade.profit)}">${signedManwon(krw(trade.profit))} (${pct(trade.return)})</td></tr>`).join("")}</tbody></table></div><div class="code">사고 남은 현금 ${won(period.cash_left)}</div></details>`).join("");
            const rules = data.rules || {};
            const ruleText = ["변동성이 작은 종목에 더 많이(한 종목 최대 25%)", rules.filtered ? "단기 과열·근거 약한 종목 제외" : "", rules.regime ? `약세장(지수가 60일 평균선 아래)엔 절반만 투자 · ${data.weak_periods}번 해당` : "", rules.trail ? `매도 신호(고점 대비 −${Math.round(rules.trail * 100)}%) 따름 · 중도 매도 ${data.early_exits}번` : "매도 신호 안 따름"].filter(Boolean).join(" · ");
            root.innerHTML = `<p class="code">${escapeHtml(data.start_date || "")} ~ ${escapeHtml(data.end_date || "")} · ${data.horizon}거래일마다 근거 상위 ${data.top}종목으로 교체 · ${data.period_count}번 · 표본 ${data.universe_size}종목 · 거래 비용 왕복 ${(data.cost * 100).toFixed(1)}%${usd ? ` · 1달러 = ${Number(data.fx).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원으로 환산` : ""}</p>
                <p class="code">적용 규칙: ${escapeHtml(ruleText)}</p>
                <div class="sim-verdict ${verdictClass}"><strong>${won(data.capital)} → ${won(data.final_value)}</strong> (${pct(data.return)}, ${signedManwon(profit)})</div>
                <div class="stat-grid">
                    <div class="stat"><small>같은 돈을 ${escapeHtml(data.index_name)}에 넣었다면</small><span class="sim-big">${won(data.index_final)}</span><small class="${pctClass(data.index_return)}">${pct(data.index_return)}</small></div>
                    <div class="stat"><small>표본 종목을 모두 같은 비중으로 샀다면</small><span class="sim-big">${won(data.universe_final)}</span><small class="${pctClass(data.universe_return)}">${pct(data.universe_return)}</small></div>
                    <div class="stat"><small>돈을 번 기간</small><span class="sim-big">${data.winning_periods} / ${data.period_count}</span></div>
                </div>
                ${equityChart(data.equity.map(point => ({ date: point.date, value: krw(point.value), index_value: krw(point.index_value), universe_value: krw(point.universe_value) })), [["value", "#d33d4d", false, "추천대로 투자"], ["index_value", "#2672c8", true, data.index_name], ["universe_value", "#98a2b3", true, "표본 평균"]])}
                <h4 style="margin:12px 0 4px">기간별 매매 내역 <span class="code">누르면 종목별 주수·매수가·매도가·손익</span></h4>${periodRows}
                <p class="analysis-meta" style="text-align:left;margin-top:8px">과거 결과이며 앞날을 보장하지 않습니다. 과거의 기사·수급 자료는 없어 기술 지표 근거만 썼고, 지금 상장된 종목만 표본에 들어 있습니다.</p>`;
        }
        document.getElementById("virtual-body").addEventListener("click", async event => {
            const button = event.target.closest("[data-virtual-action]");
            if (!button) return;
            const action = button.dataset.virtualAction;
            if (action === "go-login") { setView("account"); return; }
            if (action === "backtest") {
                const root = document.getElementById("vb-result");
                root.innerHTML = '<p class="analysis-meta" style="text-align:left">과거 주가를 모아 가상 투자를 계산하는 중입니다. 처음에는 30초쯤 걸립니다.</p>';
                button.disabled = true;
                try {
                    renderVirtualBacktest(root, await sendJson("/api/v1/virtual/backtest", {
                        market: document.getElementById("vb-market").value, capital_krw: Number(document.getElementById("vb-capital").value),
                        horizon: Number(document.getElementById("vb-horizon").value), top: Number(document.getElementById("vb-top").value),
                        months: Number(document.getElementById("vb-months").value),
                        follow_signals: document.getElementById("vb-signals").checked,
                    }));
                } catch (error) { root.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
                button.disabled = false;
            } else if (action === "create") {
                const message = document.getElementById("vp-message");
                button.disabled = true;
                message.innerHTML = '<p class="analysis-meta" style="text-align:left">가상 포트폴리오를 만드는 중입니다. 오늘 이 시장의 추천이 아직 없으면 지금 계산하느라 1~2분 걸립니다.</p>';
                try {
                    await sendJson("/api/v1/virtual/portfolios", {
                        market: document.getElementById("vp-market").value, capital_krw: Number(document.getElementById("vp-capital").value),
                        source: document.getElementById("vp-source").value, end_date: document.getElementById("vp-end").value || null,
                        name: document.getElementById("vp-name").value.trim() || null,
                        auto_sell: document.getElementById("vp-autosell").checked,
                        managed: document.getElementById("vp-managed").checked,
                        rotate: document.getElementById("vp-managed").checked && document.getElementById("vp-rotate").checked,
                    });
                    message.innerHTML = "";
                    document.getElementById("vp-name").value = "";
                    await loadPortfolios();
                } catch (error) { message.innerHTML = `<p class="error" style="padding:6px 0">${escapeHtml(error.message)}</p>`; }
                button.disabled = false;
            } else if (action === "jump") {
                document.getElementById(`vp-${button.dataset.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
            } else if (action === "rename") {
                const box = document.querySelector(`.rename-box[data-id="${button.dataset.id}"]`);
                box.hidden = !box.hidden;
                if (!box.hidden) box.querySelector("input").focus();
            } else if (action === "rename-save") {
                const box = document.querySelector(`.rename-box[data-id="${button.dataset.id}"]`);
                try {
                    await sendJson(`/api/v1/virtual/portfolios/${encodeURIComponent(button.dataset.id)}`, { name: box.querySelector("input").value.trim() }, "PATCH");
                    await loadPortfolios();
                } catch (error) { box.insertAdjacentHTML("beforeend", ` <span class="error">${escapeHtml(error.message)}</span>`); }
            } else if (action === "managed") {
                const box = document.querySelector(`.managed-box[data-id="${button.dataset.id}"]`);
                box.hidden = !box.hidden;
            } else if (action === "managed-on" || action === "managed-off") {
                const box = document.querySelector(`.managed-box[data-id="${button.dataset.id}"]`);
                button.disabled = true;
                try {
                    await sendJson(`/api/v1/virtual/portfolios/${encodeURIComponent(button.dataset.id)}`,
                        { managed: action === "managed-on", rotate: Boolean(box?.querySelector(".managed-rotate")?.checked) }, "PATCH");
                    await loadPortfolios();
                } catch (error) {
                    button.disabled = false;
                    button.insertAdjacentHTML("afterend", ` <span class="error">${escapeHtml(error.message)}</span>`);
                }
            } else if (action === "end") {
                const box = document.querySelector(`.end-box[data-id="${button.dataset.id}"]`);
                box.hidden = !box.hidden;
            } else if (action === "end-today" || action === "end-save") {
                const box = document.querySelector(`.end-box[data-id="${button.dataset.id}"]`);
                const input = box.querySelector("input");
                if (action === "end-today") {
                    const now = new Date(Date.now() + 9 * 3600 * 1000);  // 한국 날짜
                    input.value = now.toISOString().slice(0, 10);
                }
                box.querySelector(".error")?.remove();
                try {
                    await sendJson(`/api/v1/virtual/portfolios/${encodeURIComponent(button.dataset.id)}`, { end_date: input.value }, "PATCH");
                    await loadPortfolios();
                } catch (error) { box.insertAdjacentHTML("beforeend", ` <span class="error">${escapeHtml(error.message)}</span>`); }
            } else if (action === "duplicate") {
                button.disabled = true;
                button.textContent = "만드는 중…";
                try {
                    await sendJson("/api/v1/virtual/portfolios", {
                        market: button.dataset.market, capital_krw: Number(button.dataset.capital), source: button.dataset.source,
                        end_date: button.dataset.end, name: button.dataset.name || null,
                        managed: button.dataset.managed === "1", rotate: button.dataset.rotate === "1",
                    });
                    await loadPortfolios();
                } catch (error) {
                    button.disabled = false;
                    button.textContent = "같은 설정으로 하나 더";
                    button.insertAdjacentHTML("afterend", ` <span class="error">${escapeHtml(error.message)}</span>`);
                }
            } else if (action === "delete") {
                if (button.dataset.armed !== "1") { button.dataset.armed = "1"; button.textContent = "정말 삭제?"; return; }
                await sendJson(`/api/v1/virtual/portfolios/${encodeURIComponent(button.dataset.id)}`, undefined, "DELETE");
                await loadPortfolios();
            }
        });
        async function loadRecommendations(refresh = false) {
            const body = document.getElementById("recommend-body"), market = document.getElementById("recommend-market").value;
            const request = ++recommendRequest;
            recommendLoaded = true;
            body.innerHTML = '<p class="empty">분야 흐름·기사·종목 주가·수급을 모아 근거를 따지고 AI가 추천·위험 종목을 분석하는 중입니다. 처음에는 1~2분쯤 걸리고, 그 뒤 30분 동안은 바로 나옵니다.</p>';
            try {
                const data = await fetchJson(`/api/v1/recommendations?market=${market}${refresh ? "&refresh=true" : ""}`);
                if (request === recommendRequest) renderRecommendations(data);
            } catch (error) {
                if (request === recommendRequest) body.innerHTML = `<p class="error">AI 분석을 하지 못했습니다. (${escapeHtml(error.message)})</p>`;
            }
        }
        // 추천 성과 검증: ① 과거 데이터 시뮬레이션(백테스트) ② 실제 추천 기록 추적.
        let simulationTimer = null, simulationRequest = 0;
        const pct = value => value === null || value === undefined ? "-" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
        const pctClass = value => value > 0 ? "positive" : value < 0 ? "negative" : "";
        function validationShell() {
            return `<h3>추천 성과 검증</h3>
                <div class="briefing-card"><h4>① 시뮬레이션 (과거 약 1년 데이터)</h4>
                <p class="analysis-meta" style="text-align:left;margin:0 0 8px">과거 날짜마다 그날까지의 자료만으로 근거 점수를 매겨 상위 종목을 "추천", 하위 종목을 "위험"으로 골랐다면 그 뒤 수익률이 어땠는지 계산합니다(1~3분).</p>
                <div class="form-row"><select id="sim-horizon" aria-label="보유 기간"><option value="20">20거래일 보유</option><option value="5">5거래일 보유</option></select><select id="sim-top" aria-label="추천 종목 수"><option value="5">상위 5개</option><option value="3">상위 3개</option><option value="10">상위 10개</option></select><button type="button" data-sim-action="run">시뮬레이션 실행</button></div>
                <div id="sim-result"></div></div>
                <div class="briefing-card" style="margin-top:12px"><h4>② 실제 추천 이후 성과 (기록)</h4><div id="track-result"><p class="analysis-meta" style="text-align:left">불러오는 중입니다.</p></div></div>`;
        }
        function simParams() {
            return `market=${document.getElementById("recommend-market").value}&horizon=${document.getElementById("sim-horizon")?.value || 20}&top=${document.getElementById("sim-top")?.value || 5}`;
        }
        async function loadSimulation(start) {
            const root = document.getElementById("sim-result");
            if (!root) return;
            clearTimeout(simulationTimer);
            const request = ++simulationRequest;
            try {
                const data = start
                    ? await sendJson(`/api/v1/simulation?${simParams()}`, {})
                    : await fetchJson(`/api/v1/simulation?${simParams()}`);
                if (request !== simulationRequest) return;
                if (data.status === "none") { root.innerHTML = '<p class="code">아직 오늘 돌린 시뮬레이션이 없습니다. "시뮬레이션 실행"을 누르세요.</p>'; return; }
                if (data.status === "running") {
                    root.innerHTML = `<p class="analysis-meta" style="text-align:left">계산 중입니다: ${escapeHtml(data.progress || "")}</p>`;
                    simulationTimer = setTimeout(() => loadSimulation(false), 3000);
                    return;
                }
                if (data.status === "error") { root.innerHTML = `<p class="error">${escapeHtml(data.progress)}</p>`; return; }
                renderSimulation(root, data);
            } catch (error) { if (request === simulationRequest) root.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
        }
        function simulationChart(periods, indexName) {
            if (periods.length < 2) return "";
            const lines = { pick: [1], universe: [1], index: [1] };
            periods.forEach(period => {
                lines.pick.push(lines.pick.at(-1) * (1 + period.pick_return));
                lines.universe.push(lines.universe.at(-1) * (1 + period.universe_return));
                lines.index.push(lines.index.at(-1) * (1 + (period.index_return ?? 0)));
            });
            const all = [...lines.pick, ...lines.universe, ...lines.index], min = Math.min(...all), max = Math.max(...all), span = max - min || 1;
            const width = 640, height = 180, pad = 10;
            const point = (value, index, count) => `${(pad + (width - 2 * pad) * index / (count - 1)).toFixed(1)},${(pad + (height - 2 * pad) * (1 - (value - min) / span)).toFixed(1)}`;
            const path = (values, color, dash) => `<polyline points="${values.map((value, index) => point(value, index, values.length)).join(" ")}" fill="none" stroke="${color}" stroke-width="2" ${dash ? 'stroke-dasharray="5 4"' : ""} vector-effect="non-scaling-stroke"></polyline>`;
            return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="누적 수익률 비교" style="height:180px">${path(lines.universe, "#98a2b3", true)}${path(lines.index, "#2672c8", true)}${path(lines.pick, "#d33d4d", false)}</svg>
                <div class="code"><span style="color:#d33d4d">━ 추천(근거 상위)</span> · <span style="color:#98a2b3">┅ 표본 평균</span> · <span style="color:#2672c8">┅ ${escapeHtml(indexName)}</span> (누적, 시작=1)</div>`;
        }
        // 시뮬레이션 결과를 숫자로 바로 읽히게: 한 줄 결론 → 100만원 예시 → 큰 숫자 → 막대 비교 → 기간별 표(이김/짐).
        function simBars(items) {
            const values = items.map(item => item.value).filter(value => value !== null && value !== undefined);
            if (!values.length) return "";
            const low = Math.min(0, ...values), high = Math.max(0, ...values), span = high - low || 1;
            const zero = (0 - low) / span * 100;
            return `<div class="sim-bars">${items.map(item => {
                if (item.value === null || item.value === undefined) return "";
                const left = Math.min(zero, (item.value - low) / span * 100), width = Math.abs(item.value) / span * 100;
                const color = item.color || (item.value >= 0 ? "#d33d4d" : "#2672c8");
                return `<div class="sim-bar"><span>${item.label}</span><div class="track"><span style="left:${left}%;width:${Math.max(width, 0.5)}%;background:${color}"></span><i class="zero" style="left:${zero}%"></i></div><span class="value ${pctClass(item.value)}">${pct(item.value)}</span></div>`;
            }).join("")}</div>`;
        }
        function wonText(compound) {
            if (compound === null || compound === undefined) return "-";
            return `${(100 * (1 + compound)).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만원`;
        }
        function renderSimulation(root, data) {
            const summary = data.summary, buckets = summary.score_buckets, periods = data.periods;
            const total = periods.length, wins = periods.filter(period => period.pick_return > period.universe_return).length;
            const avoidWins = periods.filter(period => period.avoid_return < period.universe_return).length;
            const excess = summary.excess_mean, excessPoint = excess === null ? "-" : `${excess > 0 ? "+" : ""}${(excess * 100).toFixed(2)}%p`;
            const verdictClass = excess > 0 && wins > total / 2 ? "good" : excess < 0 && wins < total / 2 ? "bad" : "mixed";
            const verdict = verdictClass === "good"
                ? `<strong>추천 종목이 표본보다 나았습니다.</strong> ${total}번 중 <strong>${wins}번</strong> 앞섰고, 기간당 평균 <strong>${excessPoint}</strong> 더 벌었습니다.`
                : verdictClass === "bad"
                ? `<strong>추천 종목이 표본보다 못했습니다.</strong> ${total}번 중 ${wins}번만 앞섰고, 기간당 평균 <strong>${excessPoint}</strong> 차이가 났습니다. 이 시장·기간에는 지금 근거 점수가 잘 맞지 않았습니다.`
                : `<strong>결과가 엇갈립니다.</strong> ${total}번 중 ${wins}번 앞섰고, 기간당 평균 차이는 ${excessPoint}입니다.`;
            const pp = value => pct(value).replace("%", "%p");  // 두 수익률의 차이는 %p로 씁니다
            const sorted = periods.slice().sort((left, right) => (right.pick_return - right.universe_return) - (left.pick_return - left.universe_return));
            const best = sorted[0], worst = sorted.at(-1);
            const bigStat = (label, value, cls = "", note = "") => `<div class="stat"><small>${label}</small><span class="sim-big ${cls}">${value}</span>${note ? `<small>${note}</small>` : ""}</div>`;
            const periodRows = periods.slice().reverse().map(period => {
                const win = period.pick_return > period.universe_return;
                return `<tr><td>${escapeHtml(period.date)} → ${escapeHtml(period.end)}</td><td><span class="win-badge ${win ? "win" : "lose"}">${win ? "✓ 이김" : "✕ 짐"}</span></td><td class="${pctClass(period.pick_return)}">${pct(period.pick_return)}</td><td class="${pctClass(period.universe_return)}">${pct(period.universe_return)}</td><td class="${pctClass(period.pick_return - period.universe_return)}">${pp(period.pick_return - period.universe_return)}</td><td class="${pctClass(period.index_return)}">${pct(period.index_return)}</td><td style="text-align:left">${period.picks.map(pick => escapeHtml(pick.name)).join(", ")}</td></tr>`;
            }).join("");
            root.innerHTML = `<p class="code">${escapeHtml(data.finished_at || "")} 계산 · 표본 ${data.universe_size}개 종목 · ${summary.horizon}거래일마다 ${total}번 · 근거 상위 ${summary.top}개를 추천으로</p>
                <div class="sim-verdict ${verdictClass}">${verdict}</div>
                <div class="briefing-card"><h4>100만원으로 시작했다면 (${total}번 이어서 투자)</h4>
                    <div class="stat-grid" style="margin:6px 0 0">
                        ${bigStat("추천 종목", wonText(summary.pick_compound), pctClass(summary.pick_compound), `누적 ${pct(summary.pick_compound)} · 비용 ${(summary.cost_per_period * 100).toFixed(1)}%씩 차감`)}
                        ${bigStat("표본 전체 평균", wonText(summary.universe_compound), pctClass(summary.universe_compound), `누적 ${pct(summary.universe_compound)}`)}
                        ${bigStat(escapeHtml(data.index_name), wonText(summary.index_compound), pctClass(summary.index_compound), `누적 ${pct(summary.index_compound)}`)}
                    </div></div>
                <div class="stat-grid">
                    ${bigStat("추천이 표본을 이긴 횟수", `${wins} / ${total}`, "", `이긴 비율 ${total ? Math.round(wins / total * 100) : 0}%`)}
                    ${bigStat("기간당 평균 초과 수익", excessPoint, pctClass(excess), "추천 평균 − 표본 평균")}
                    ${bigStat("위험 종목이 표본보다 못한 횟수", `${avoidWins} / ${total}`, "", "많을수록 위험 표시가 잘 맞음")}
                    ${best ? bigStat("가장 잘 맞은 기간", pp(best.pick_return - best.universe_return), pctClass(best.pick_return - best.universe_return), `${escapeHtml(best.date)} 시작`) : ""}
                    ${worst ? bigStat("가장 빗나간 기간", pp(worst.pick_return - worst.universe_return), pctClass(worst.pick_return - worst.universe_return), `${escapeHtml(worst.date)} 시작`) : ""}
                </div>
                ${(data.variants || []).length ? `<h4 style="margin:12px 0 4px">규칙 비교 <span class="code">같은 데이터로 추천 규칙만 바꿔 계산 · ★ = 지금 쓰는 규칙</span></h4>
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>규칙</th><th>기간당 평균</th><th>누적</th><th>표본을 이긴 비율</th><th>최대 낙폭</th><th>최악 기간</th><th>중도 매도</th></tr></thead><tbody>${data.variants.map(variant => `<tr><td>${variant.current ? "★ " : ""}${escapeHtml(variant.label)}</td><td class="${pctClass(variant.pick_mean)}">${pct(variant.pick_mean)}</td><td class="${pctClass(variant.pick_compound)}">${pct(variant.pick_compound)}</td><td>${variant.hit_rate === null ? "-" : `${Math.round(variant.hit_rate * 100)}%`}</td><td class="negative">${pct(variant.pick_max_drawdown)}</td><td class="${pctClass(variant.worst_period)}">${pct(variant.worst_period)}</td><td>${variant.early_exit_count || 0}</td></tr>`).join("")}</tbody></table></div>` : ""}
                <h4 style="margin:12px 0 4px">기간당 평균 수익률 비교</h4>
                ${simBars([
                    { label: `추천 (근거 상위 ${summary.top}개)`, value: summary.pick_mean, color: "#d33d4d" },
                    { label: "표본 전체 평균", value: summary.universe_mean, color: "#98a2b3" },
                    { label: escapeHtml(data.index_name), value: summary.index_mean, color: "#2672c8" },
                    { label: `위험 (근거 하위 ${summary.top}개)`, value: summary.avoid_mean, color: "#f79009" },
                ])}
                <h4 style="margin:12px 0 4px">근거 점수 구간별 평균 수익률 <span class="code">위로 갈수록 높아야 점수가 앞날 수익과 맞는 것</span></h4>
                ${simBars([
                    { label: "점수 상위 1/3", value: buckets.high },
                    { label: "점수 중간 1/3", value: buckets.middle },
                    { label: "점수 하위 1/3", value: buckets.low },
                ])}
                <h4 style="margin:12px 0 4px">누적 수익률 흐름</h4>
                ${simulationChart(periods, data.index_name)}
                <h4 style="margin:12px 0 4px">기간별 결과 <span class="code">최근 순 · 이김 = 추천이 표본 평균보다 높음</span></h4>
                <div class="table-wrap"><table class="sim-table"><thead><tr><th>기간</th><th>결과</th><th>추천</th><th>표본</th><th>차이(%p)</th><th>지수</th><th>추천 종목</th></tr></thead><tbody>${periodRows}</tbody></table></div>
                <details style="margin-top:8px"><summary>계산 방법과 한계</summary><p class="analysis-meta" style="text-align:left;margin-top:8px">${escapeHtml(data.method_note)}</p>
                <ul class="briefing-points" style="font-size:13px">${data.limits.map(limit => `<li>${escapeHtml(limit)}</li>`).join("")}</ul></details>`;
        }
        async function loadTrack() {
            const root = document.getElementById("track-result");
            if (!root) return;
            try {
                const data = await fetchJson(`/api/v1/recommendations/track?market=${document.getElementById("recommend-market").value}`);
                if (!data.summary.length) { root.innerHTML = `<p class="analysis-meta" style="text-align:left">아직 하루 이상 지난 추천 기록이 없습니다. ${escapeHtml(data.note)}</p>`; return; }
                const summary = data.summary.map(item => `<tr><td>${escapeHtml(item.label)}</td><td>${item.count}</td><td class="${pctClass(item.mean)}">${pct(item.mean)}</td><td>${Math.round(item.win_rate * 100)}%</td></tr>`).join("");
                const rows = data.rows.slice(0, 40).map(row => `<tr><td>${escapeHtml(row.date)}</td><td style="text-align:left">${escapeHtml(row.name)} <span class="code">${escapeHtml(row.symbol)}</span></td><td style="text-align:left">${escapeHtml({ combined: "통합 추천", pick: "테마 추천", ai_pick: "AI 추천", risk: "위험", ai_risk: "AI 위험" }[row.kind] || row.kind)}</td><td class="${pctClass(row.return)}">${pct(row.return)}</td><td class="${pctClass(row.index_return)}">${pct(row.index_return)}</td></tr>`).join("");
                root.innerHTML = `<p class="code">기록 ${data.snapshots}일치 · 같은 기간 ${escapeHtml(data.index_name)} 평균 ${pct(data.index_mean)}</p>
                    <div class="table-wrap"><table class="sim-table"><thead><tr><th>구분</th><th>종목 수</th><th>평균 수익률</th><th>오른 비율</th></tr></thead><tbody>${summary}</tbody></table></div>
                    <details style="margin-top:8px"><summary>종목별 기록</summary><div class="table-wrap"><table class="sim-table"><thead><tr><th>추천일</th><th>종목</th><th>구분</th><th>그 뒤 수익률</th><th>같은 기간 지수</th></tr></thead><tbody>${rows}</tbody></table></div></details>
                    <p class="analysis-meta" style="text-align:left;margin-top:8px">${escapeHtml(data.note)}</p>`;
            } catch (error) { root.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`; }
        }
        document.getElementById("recommend-market").addEventListener("change", () => loadRecommendations());
        document.getElementById("recommend-refresh").addEventListener("click", () => loadRecommendations(true));
        document.getElementById("recommend-body").addEventListener("change", event => { if (["sim-horizon", "sim-top"].includes(event.target.id)) loadSimulation(false); });
        document.getElementById("recommend-body").addEventListener("click", event => {
            const simAction = event.target.closest("[data-sim-action]");
            if (simAction) { loadSimulation(true); return; }
            if (event.target.closest("summary")) return;
            const stock = event.target.closest("[data-symbol]"), sector = event.target.closest("[data-sector]");
            if (stock) showHistory(stock.dataset.symbol);
            else if (sector) openSector(sector.dataset.sector, sector.dataset.market);
        });
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
            if (view === "recommend" && !recommendLoaded) loadRecommendations();
            if (view === "account" && !accountLoaded) loadAccount();
            if (view === "virtual") loadVirtual();  // 다른 탭에서 로그인했을 수 있어 열 때마다 확인
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
            const aiSummaryButton = event.target.closest(".ai-summary-button");
            if (aiSummaryButton) { loadSummary(aiSummaryButton.closest("li"), false, true, true); return; }
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
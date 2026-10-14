from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.toss_api import TossApiClient

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    api = TossApiClient(settings)
    app.state.toss_api = api
    try:
        yield
    finally:
        await api.close()


app = FastAPI(title="Toss Raw Ranking Viewer", lifespan=lifespan)

PAGE = """<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Toss Raw Ranking Data</title>
    <style>
        html, body { min-height: 100%; margin: 0; background: #000; color: #ddd; }
        pre { margin: 0; padding: 8px; overflow: auto; font: 13px/1.45 Consolas, monospace; white-space: pre; }
    </style>
</head>
<body>
    <pre id="output"></pre>
    <script>
        const output = document.getElementById("output");
        const params = new URLSearchParams(window.location.search);
        const markets = ["KR", "US"];
        const kinds = ["TOP_GAINERS", "TOP_LOSERS", "MARKET_TRADING_AMOUNT"];
        const market = markets.includes(params.get("market")) ? params.get("market") : "KR";
        const kind = kinds.includes(params.get("kind")) ? params.get("kind") : "TOP_GAINERS";
        fetch(`/api/raw?${new URLSearchParams({ market, kind })}`)
            .then(async response => {
                const text = await response.text();
                try {
                    output.textContent = JSON.stringify(JSON.parse(text), null, 2);
                } catch {
                    output.textContent = text;
                }
            })
            .catch(error => { output.textContent = JSON.stringify({ error: error.message }, null, 2); });
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def raw_data_page() -> HTMLResponse:
    return HTMLResponse(PAGE)


@app.get("/api/raw")
async def raw_ranking(
    request: Request,
    market: Literal["KR", "US"] = Query("KR"),
    kind: Literal["TOP_GAINERS", "TOP_LOSERS", "MARKET_TRADING_AMOUNT"] = Query("TOP_GAINERS"),
):
    duration = (
        settings.ranking_realtime_duration
        if kind == "MARKET_TRADING_AMOUNT"
        else settings.ranking_duration
    )
    return await request.app.state.toss_api.fetch_ranking(kind, market, duration)

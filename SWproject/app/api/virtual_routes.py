"""가상 투자 API: 과거로 가상 투자해 보기, 오늘부터 추천대로 가상 투자(로그인 사용자별 저장)."""
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from ..account import usd_krw_rate
from ..briefing import KST
from ..rate_limit import enforce
from ..redis_client import get_json, set_json
from ..toss_api import TossApiError
from ..virtual import (
    NAME_MAX,
    SOURCE_LABELS,
    create_portfolio,
    delete_portfolio,
    evaluate_portfolios,
    latest_recommendations,
    next_tuesday,
    rename_portfolio,
    virtual_backtest,
)
from .account_routes import check_request, current_user, require_write_header

router = APIRouter(prefix="/api/v1/virtual", tags=["virtual"])
checked = [Depends(check_request)]
checked_write = [Depends(check_request), Depends(require_write_header)]

MIN_CAPITAL, MAX_CAPITAL = 100_000, 10_000_000_000  # 10만원 ~ 100억원(가상)


@router.get("/defaults", dependencies=checked)
async def defaults():
    today = datetime.now(KST).date()
    return {"today": today.isoformat(), "next_tuesday": next_tuesday(today).isoformat(), "min_capital": MIN_CAPITAL}


class BacktestInput(BaseModel):
    market: Literal["kr", "us"] = "kr"
    capital_krw: float = Field(10_000_000, ge=MIN_CAPITAL, le=MAX_CAPITAL)
    horizon: Literal[5, 20] = 20
    top: int = Field(5, ge=3, le=10)
    months: Literal[3, 6, 12] = 12


@router.post("/backtest", dependencies=checked_write)
async def backtest(request: Request, payload: BacktestInput):
    """정한 금액으로 과거에 추천 규칙대로 투자했다면(근거 점수 상위 N개, N거래일마다 교체). 미국 주식은 지금 환율로 달러로 바꿔 계산."""
    key = f"vp:backtest:v1:{payload.market}:{round(payload.capital_krw)}:{payload.horizon}:{payload.top}:{payload.months}:{date.today().isoformat()}"
    cached = await get_json(key)
    if cached is not None:
        return cached
    await enforce(request, "virtual")
    api = request.app.state.toss_api
    fx = None
    capital = payload.capital_krw
    if payload.market == "us":
        rate = await usd_krw_rate(api)
        if not rate:
            raise HTTPException(status_code=503, detail="환율을 불러오지 못해 원화 금액을 달러로 바꿀 수 없습니다.")
        fx = rate["mid"]
        capital = payload.capital_krw / fx
    try:
        result = await virtual_backtest(api, payload.market, capital, payload.horizon, payload.top, payload.months)
    except TossApiError as error:
        raise HTTPException(status_code=502, detail="토스 API 호출에 실패했습니다.") from error
    result = {**result, "capital_krw": round(payload.capital_krw), "fx": fx}
    await set_json(key, result, 6 * 60 * 60)
    return result


@router.get("/portfolios", dependencies=checked)
async def portfolios(request: Request, user: dict = Depends(current_user)):
    """내 가상 포트폴리오와 지금까지의 성과(종가 기준). 미국은 지금 환율로 원화도 함께."""
    try:
        items = await evaluate_portfolios(request.app.state.toss_api, user["username"])
    except TossApiError as error:
        raise HTTPException(status_code=502, detail="토스 API 호출에 실패했습니다.") from error
    rate = await usd_krw_rate(request.app.state.toss_api) if any(item["currency"] == "USD" for item in items) else None
    return {"portfolios": items, "fx_now": rate["mid"] if rate else None}


class PortfolioInput(BaseModel):
    market: Literal["kr", "us"] = "kr"
    capital_krw: float = Field(10_000_000, ge=MIN_CAPITAL, le=MAX_CAPITAL)
    # all=통합, ai=AI 추천만, theme=추천 테마만, compare=세 가지를 각각 만들어 비교
    source: Literal["all", "ai", "theme", "compare"] = "all"
    end_date: date | None = None
    # 비워 두면 "국장 통합 추천 11-15"처럼 자동으로 붙이고, 같은 이름이 있으면 (2)…를 붙입니다.
    name: str | None = Field(None, max_length=NAME_MAX)


@router.post("/portfolios", dependencies=checked_write)
async def new_portfolio(request: Request, payload: PortfolioInput, user: dict = Depends(current_user)):
    """AI분석 탭의 지금 추천대로 가상 포트폴리오를 만듭니다(다음 거래일 시가 매수, 종료일 종가까지 평가)."""
    today = datetime.now(KST).date()
    end_date = payload.end_date or next_tuesday(today)
    if end_date <= today:
        raise HTTPException(status_code=422, detail="종료일은 오늘 이후로 정하세요.")
    if (end_date - today).days > 180:
        raise HTTPException(status_code=422, detail="종료일은 180일 안으로 정하세요.")
    recommendations = await latest_recommendations(payload.market)
    if not recommendations:
        # 오늘 이 시장 추천이 아직 없으면 AI분석 탭과 같은 방식으로 지금 계산합니다(1~2분, 결과는 AI분석 탭에도 저장).
        from .insight_routes import recommendations as compute_recommendations

        try:
            recommendations = await compute_recommendations(request, market=payload.market, refresh=False)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=502, detail="추천을 계산하지 못했습니다. 잠시 뒤 다시 시도하세요.") from error
    if not recommendations:
        raise HTTPException(status_code=409, detail="추천을 계산하지 못했습니다. AI분석 탭에서 이 시장의 추천을 불러온 뒤 다시 시도하세요.")
    fx = None
    if payload.market == "us":
        rate = await usd_krw_rate(request.app.state.toss_api)
        fx = rate["mid"] if rate else None
    sources = ["all", "ai", "theme"] if payload.source == "compare" else [payload.source]
    created, group = [], uuid.uuid4().hex[:8] if payload.source == "compare" else None
    for source in sources:
        try:
            # 세 가지 비교는 이름 뒤에 방식을 붙여 구분합니다.
            name = f"{payload.name} · {SOURCE_LABELS[source]}" if payload.name and len(sources) > 1 else payload.name
            created.append(await create_portfolio(user["username"], payload.market, payload.capital_krw, source, end_date,
                                                  recommendations, fx, group, name))
        except ValueError as error:
            if len(sources) == 1:
                raise HTTPException(status_code=409, detail=str(error)) from error
    if not created:
        raise HTTPException(status_code=409, detail="따라 살 추천 종목이 없습니다. AI분석 탭에서 추천을 먼저 계산하세요.")
    return {"portfolio": created[0], "portfolios": created}


@router.delete("/portfolios/{portfolio_id}", dependencies=checked_write)
async def remove_portfolio(portfolio_id: str = Path(pattern=r"^[0-9a-f]{12}$"), user: dict = Depends(current_user)):
    await delete_portfolio(user["username"], portfolio_id)
    return {"deleted": portfolio_id}


class RenameInput(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)


@router.patch("/portfolios/{portfolio_id}", dependencies=checked_write)
async def rename(payload: RenameInput, portfolio_id: str = Path(pattern=r"^[0-9a-f]{12}$"), user: dict = Depends(current_user)):
    try:
        return {"portfolio": await rename_portfolio(user["username"], portfolio_id, payload.name)}
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

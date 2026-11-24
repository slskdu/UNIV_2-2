import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.account_routes import router as account_router
from .api.insight_routes import daily_record_loop, intraday_recommend_loop
from .api.insight_routes import router as insight_router
from .api.routes import page_router, router
from .api.virtual_routes import router as virtual_router
from .collector import collector_loop
from .config import get_settings
from .managed_live import managed_live_loop
from .redis_client import close_redis, redis
from .sectors import sector_flow_loop
from .toss_api import TossApiClient
from .stock_master import load_master
from .users import set_shared_api

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.ping()
    toss_api = TossApiClient(settings)
    app.state.toss_api = toss_api
    set_shared_api(toss_api)  # 사용자가 서버와 같은 토스 키를 등록하면 이 연결을 함께 씁니다(토큰 충돌 방지)
    collector_task = asyncio.create_task(collector_loop(toss_api, settings))
    sector_flow_task = asyncio.create_task(sector_flow_loop(toss_api))
    master_task = asyncio.create_task(load_master(toss_api))  # 종목 이름 검색용 전체 종목 목록을 미리 받아 둡니다
    record_task = asyncio.create_task(daily_record_loop(toss_api))  # 장 마감 뒤 추천을 하루 한 번 자동 기록
    live_task = asyncio.create_task(managed_live_loop(toss_api))  # 자동 운용 가상 포트폴리오 장중 5분마다 매매
    intraday_task = asyncio.create_task(intraday_recommend_loop(toss_api))  # 장중 30분마다 추천 새로 계산
    try:
        yield
    finally:
        for task in (collector_task, sector_flow_task, master_task, record_task, live_task, intraday_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await toss_api.close()
        await close_redis()


app = FastAPI(title="Toss Ranking Backend", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(insight_router)
app.include_router(account_router)
app.include_router(virtual_router)
app.include_router(page_router)


@app.get("/health")
async def health():
    await redis.ping()
    return {"status": "ok"}
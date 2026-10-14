import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.insight_routes import router as insight_router
from .api.routes import page_router, router
from .collector import collector_loop
from .config import get_settings
from .redis_client import close_redis, redis
from .sectors import sector_flow_loop
from .toss_api import TossApiClient

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.ping()
    toss_api = TossApiClient(settings)
    app.state.toss_api = toss_api
    collector_task = asyncio.create_task(collector_loop(toss_api, settings))
    sector_flow_task = asyncio.create_task(sector_flow_loop(toss_api))
    try:
        yield
    finally:
        for task in (collector_task, sector_flow_task):
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
app.include_router(page_router)


@app.get("/health")
async def health():
    await redis.ping()
    return {"status": "ok"}
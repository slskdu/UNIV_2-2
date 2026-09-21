import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .collector import collector_loop
from .config import get_settings
from .redis_client import close_redis, redis
from .toss_api import TossApiClient

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.ping()
    toss_api = TossApiClient(settings)
    collector_task = asyncio.create_task(collector_loop(toss_api, settings))
    try:
        yield
    finally:
        collector_task.cancel()
        with suppress(asyncio.CancelledError):
            await collector_task
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


@app.get("/health")
async def health():
    await redis.ping()
    return {"status": "ok"}
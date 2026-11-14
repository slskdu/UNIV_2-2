"""무거운 작업(기사 수집·요약·AI 분석) 남용 제한.

캐시에 있는 결과는 누가 몇 번을 봐도 제한하지 않고, 서버가 실제로 새로 만들어야 하는 요청(캐시 미스·다시 분석)만 셉니다.
- 로그인한 사용자는 사용자별, 로그인하지 않은 방문자는 IP별로 10분 창(window)마다 셉니다. 로그인하면 한도가 더 큽니다.
- 로그인하지 않은 방문자 전체가 함께 쓰는 한도도 둬서, IP를 바꿔 가며 보내도 토스·AI 한도를 다 쓰지 못하게 합니다.
- 넘으면 429와 Retry-After(남은 초)를 돌려줍니다.
"""
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import get_settings
from .redis_client import redis

WINDOW_SECONDS = 600


@dataclass(frozen=True)
class Limit:
    anonymous: int
    user: int
    anonymous_total: int  # 로그인하지 않은 방문자 전체 합계


# 10분 동안 새로 만들 수 있는 횟수
LIMITS = {
    "summary": Limit(anonymous=40, user=200, anonymous_total=400),  # 기사 본문 요약(규칙)
    "ai_summary": Limit(anonymous=5, user=60, anonymous_total=40),  # AI 요약 버튼
    "news": Limit(anonymous=10, user=40, anonymous_total=60),  # 종목·분야 기사 수집·분류
    "analysis": Limit(anonymous=3, user=15, anonymous_total=15),  # 시장 분석·AI분석(추천) 새로 계산
    "simulation": Limit(anonymous=1, user=5, anonymous_total=3),  # 추천 시뮬레이션(백테스트)
    "track": Limit(anonymous=5, user=20, anonymous_total=30),  # 실제 추천 기록 성과(30분 캐시)
    "virtual": Limit(anonymous=3, user=20, anonymous_total=20),  # 과거로 가상 투자해 보기
    "browser": Limit(anonymous=60, user=200, anonymous_total=300),  # 확장 프로그램이 보낸 페이지 요약
}
LABELS = {
    "summary": "기사 요약", "ai_summary": "AI 요약", "news": "기사 수집", "analysis": "AI 분석",
    "simulation": "추천 시뮬레이션", "track": "추천 성과", "virtual": "가상 투자", "browser": "브라우저 요약",
}


async def _identity(request: Request) -> tuple[str, bool]:
    from .users import SESSION_COOKIE, session_user  # 순환 import 방지

    user = await session_user(request.cookies.get(SESSION_COOKIE))
    if user:
        return f"user:{user['username']}", True
    return f"ip:{request.client.host if request.client else 'unknown'}", False


async def _hit(key: str) -> int:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, WINDOW_SECONDS + 5)
    return int(count)


async def enforce(request: Request, bucket: str) -> None:
    """한도를 넘으면 HTTPException(429). 새로 만드는 작업 직전에 부릅니다."""
    if not get_settings().rate_limit_enabled:
        return
    limit = LIMITS[bucket]
    identity, logged_in = await _identity(request)
    window = int(time.time() // WINDOW_SECONDS)
    retry_after = WINDOW_SECONDS - int(time.time() % WINDOW_SECONDS)
    minutes = max(1, round(retry_after / 60))
    if await _hit(f"rl:{bucket}:{identity}:{window}") > (limit.user if logged_in else limit.anonymous):
        hint = "" if logged_in else " 로그인하면 더 많이 쓸 수 있습니다."
        raise HTTPException(
            status_code=429,
            detail=f"{LABELS[bucket]} 요청이 많아 잠시 제한했습니다. 약 {minutes}분 뒤 다시 시도하세요.{hint}",
            headers={"Retry-After": str(retry_after)},
        )
    if not logged_in and await _hit(f"rl:{bucket}:anonymous-total:{window}") > limit.anonymous_total:
        raise HTTPException(
            status_code=429,
            detail=f"지금 로그인하지 않은 방문자의 {LABELS[bucket]} 요청이 몰려 잠시 제한했습니다. 약 {minutes}분 뒤 다시 시도하거나 로그인하세요.",
            headers={"Retry-After": str(retry_after)},
        )

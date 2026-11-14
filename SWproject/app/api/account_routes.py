"""로그인과 사용자별 토스증권 계좌 API.

- 로그인: 아이디·비밀번호 → HttpOnly·SameSite=Strict 세션 쿠키. 계좌 기능은 모두 로그인한 사용자 본인의 토스 키로만 동작합니다.
- 요청 검사: Host가 ALLOWED_HOSTS 안이어야 하고(DNS 리바인딩 방지), 다른 사이트에서 보낸 요청(Origin·Sec-Fetch-Site)은 거절합니다.
- 상태를 바꾸는 요청(로그인·주문·취소·관심 종목·설정)은 X-Toss-Order: 1 헤더가 있어야 합니다(다른 사이트는 이 헤더를 붙일 수 없음).
"""
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from ..account import (
    STATUS_KEY,
    AccountClient,
    AccountError,
    account_summary,
    add_watch,
    confirm_order,
    get_watchlist,
    normalize_order,
    preview_order,
    remove_watch,
    stock_quote,
    stock_status,
)
from ..auth import TossAuthError
from ..config import get_settings
from ..redis_client import redis
from ..toss_api import TossApiClient, TossApiError
from ..users import (
    SESSION_COOKIE,
    SESSION_TTL,
    UserError,
    build_user_api,
    delete_user,
    drop_client,
    encrypt_secret,
    get_user,
    is_shared_api,
    login,
    logout,
    public_user,
    save_user,
    session_user,
    shared_api,
    signup,
    user_api,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["account"])


def check_request(request: Request) -> None:
    host_header = request.headers.get("host") or ""
    host = urlsplit(f"//{host_header}").hostname or ""
    if host not in get_settings().allowed_host_list:
        raise HTTPException(status_code=403, detail="허용되지 않은 주소로 들어온 요청입니다(ALLOWED_HOSTS).")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc.lower() != host_header.lower():
        raise HTTPException(status_code=403, detail="다른 사이트에서 보낸 요청은 받지 않습니다.")
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(status_code=403, detail="다른 사이트에서 보낸 요청은 받지 않습니다.")


def require_write_header(x_toss_order: str = Header("")) -> None:
    if x_toss_order != "1":
        raise HTTPException(status_code=403, detail="이 화면에서 보낸 요청만 받습니다.")


async def current_user(request: Request) -> dict[str, Any]:
    user = await session_user(request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


checked = [Depends(check_request)]
checked_write = [Depends(check_request), Depends(require_write_header)]

_account_clients: dict[str, tuple[TossApiClient, AccountClient]] = {}


async def _user_clients(user: dict[str, Any]) -> tuple[TossApiClient, AccountClient]:
    api = await user_api(user)
    cached = _account_clients.get(user["username"])
    if cached and cached[0] is api:
        return cached
    entry = (api, AccountClient(api))
    _account_clients[user["username"]] = entry
    return entry


async def _forget_clients(username: str) -> None:
    _account_clients.pop(username, None)
    await drop_client(username)


def _error(error: Exception) -> HTTPException:
    if isinstance(error, (AccountError, UserError)):
        status = error.status_code if 400 <= error.status_code < 500 or error.status_code == 503 else 502
        return HTTPException(status_code=status, detail=str(error))
    return HTTPException(status_code=502, detail="토스 API 호출에 실패했습니다.")


KNOWN_ERRORS = (AccountError, UserError, TossApiError)


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="strict",
        secure=get_settings().session_cookie_secure, path="/",
    )


# --- 로그인 -------------------------------------------------------------------------------

class Credentials(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=128)
    invite_code: str = Field("", max_length=100)


@router.get("/auth/me", dependencies=checked)
async def me(request: Request):
    user = await session_user(request.cookies.get(SESSION_COOKIE))
    settings = get_settings()
    base = {"signup_enabled": settings.signup_enabled, "invite_required": bool(settings.signup_invite_code)}
    return {**base, "logged_in": True, "user": public_user(user)} if user else {**base, "logged_in": False}


@router.post("/auth/signup", dependencies=checked_write)
async def auth_signup(payload: Credentials, request: Request, response: Response):
    try:
        await signup(payload.username, payload.password, payload.invite_code)
        token, user = await login(payload.username, payload.password, request.client.host if request.client else "")
    except UserError as error:
        raise _error(error) from error
    _set_session(response, token)
    return {"user": public_user(user)}


@router.post("/auth/login", dependencies=checked_write)
async def auth_login(payload: Credentials, request: Request, response: Response):
    try:
        token, user = await login(payload.username, payload.password, request.client.host if request.client else "")
    except UserError as error:
        raise _error(error) from error
    _set_session(response, token)
    return {"user": public_user(user)}


@router.post("/auth/logout", dependencies=checked_write)
async def auth_logout(request: Request, response: Response):
    await logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"logged_in": False}


class TossKey(BaseModel):
    client_id: str = Field(min_length=4, max_length=200)
    client_secret: str = Field(min_length=4, max_length=500)


@router.post("/auth/toss-key", dependencies=checked_write)
async def register_toss_key(payload: TossKey, user: dict = Depends(current_user)):
    """토스 키를 실제로 써 보고(토큰 발급 + 계좌 조회) 통과하면 client_secret을 암호화해 저장합니다."""
    client_id, client_secret = payload.client_id.strip(), payload.client_secret.strip()
    try:
        encrypted = encrypt_secret(client_secret)  # APP_SECRET_KEY가 없으면 여기서 막아, 키를 써 보기 전에 알립니다.
    except UserError as error:
        raise _error(error) from error
    api = build_user_api(client_id, client_secret)
    try:
        accounts = await AccountClient(api).accounts()
    except TossAuthError as error:
        raise HTTPException(status_code=400, detail="토스 키로 로그인하지 못했습니다. client_id·client_secret을 확인하세요.") from error
    except AccountError as error:
        detail = str(error)
        if error.code == "ip-not-allowed":
            detail = "토스 앱의 Open API 허용 IP에 이 서버의 공인 IP가 없습니다. 토스 앱 설정 > Open API > 허용 IP 관리에서 추가하세요."
        raise HTTPException(status_code=400, detail=detail) from error
    finally:
        if not is_shared_api(api):
            await api.close()
    if not accounts:
        raise HTTPException(status_code=400, detail="이 키로 연결된 토스증권 위탁 계좌가 없습니다.")
    fresh = await get_user(user["username"]) or user
    fresh["toss"] = {"client_id": client_id, "client_secret": encrypted}
    await save_user(fresh)
    await _forget_clients(user["username"])
    await redis.delete(STATUS_KEY.format(username=user["username"]))
    return {"user": public_user(fresh)}


@router.delete("/auth/toss-key", dependencies=checked_write)
async def remove_toss_key(user: dict = Depends(current_user)):
    fresh = await get_user(user["username"]) or user
    fresh["toss"], fresh["trading_enabled"] = None, False
    await save_user(fresh)
    await _forget_clients(user["username"])
    return {"user": public_user(fresh)}


class TradingSwitch(BaseModel):
    enabled: bool
    password: str = Field("", max_length=128)


@router.post("/auth/trading", dependencies=checked_write)
async def set_trading(payload: TradingSwitch, user: dict = Depends(current_user)):
    """실제 주문 허용 켜기/끄기. 켤 때는 비밀번호를 다시 확인합니다."""
    fresh = await get_user(user["username"]) or user
    if payload.enabled and not verify_password(payload.password, fresh["password"]):
        raise HTTPException(status_code=401, detail="비밀번호가 맞지 않습니다.")
    fresh["trading_enabled"] = payload.enabled
    await save_user(fresh)
    return {"user": public_user(fresh)}


class PasswordOnly(BaseModel):
    password: str = Field(max_length=128)


@router.post("/auth/delete-account", dependencies=checked_write)
async def remove_account(payload: PasswordOnly, request: Request, response: Response, user: dict = Depends(current_user)):
    if not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=401, detail="비밀번호가 맞지 않습니다.")
    _account_clients.pop(user["username"], None)
    await delete_user(user["username"])
    await logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}


# --- 계좌 -------------------------------------------------------------------------------

@router.get("/account/summary", dependencies=checked)
async def summary(user: dict = Depends(current_user)):
    """계좌(번호 가림), 보유 종목·평가 손익, 주문 가능 금액(원·달러), 미체결 주문, 주문 허용 여부."""
    try:
        api, client = await _user_clients(user)
        return await account_summary(api, client, bool(user.get("trading_enabled")))
    except KNOWN_ERRORS as error:
        raise _error(error) from error


@router.get("/account/orders", dependencies=checked)
async def orders(status: Literal["OPEN", "CLOSED"] = "OPEN", user: dict = Depends(current_user)):
    """미체결(OPEN) 또는 최근 7일 체결·취소(CLOSED) 주문."""
    try:
        api, client = await _user_clients(user)
        raw = await client.orders(status)
        names = await api.fetch_stock_names([order.get("symbol") for order in raw if order.get("symbol")]) if raw else {}
    except KNOWN_ERRORS as error:
        raise _error(error) from error
    return {"status": status, "orders": [normalize_order(order, names) for order in raw]}


class OrderInput(BaseModel):
    symbol: str = Field(max_length=15)
    side: str = Field(max_length=8)
    order_type: str = Field(max_length=8)
    quantity: float
    price: float | None = None
    # 가격을 입력한 통화. 미국 주식을 KRW로 입력하면 환율로 달러로 바꿔 주문합니다(국내 주식은 KRW만).
    price_currency: str | None = Field(None, max_length=3)


@router.get("/account/quote", dependencies=checked)
async def quote(request: Request, symbol: str, user: dict = Depends(current_user)):
    """주문 칸용 종목 정보: 이름, 거래 통화, 최근 종가, 환율(미국 주식)."""
    try:
        return await stock_quote(request.app.state.toss_api, symbol)
    except KNOWN_ERRORS as error:
        raise _error(error) from error


@router.post("/account/orders/preview", dependencies=checked_write)
async def order_preview(order: OrderInput, user: dict = Depends(current_user)):
    """주문 미리보기: 예상 금액, 주문 가능 금액·매도 가능 수량, 문제점. 주문이 허용되고 문제가 없으면 확인 토큰(2분)을 줍니다."""
    try:
        api, client = await _user_clients(user)
        return await preview_order(api, client, order.model_dump(), user["username"], bool(user.get("trading_enabled")))
    except KNOWN_ERRORS as error:
        raise _error(error) from error


class OrderConfirm(BaseModel):
    confirm_token: str = Field(max_length=40)


@router.post("/account/orders/confirm", dependencies=checked_write)
async def order_confirm(payload: OrderConfirm, user: dict = Depends(current_user)):
    """실제 주문 전송. 서버 TRADING_ENABLED, 사용자 '실제 주문 허용', 본인이 만든 유효한 미리보기 토큰이 모두 있어야 보냅니다."""
    try:
        _, client = await _user_clients(user)
        result = await confirm_order(client, payload.confirm_token, user["username"], bool(user.get("trading_enabled")))
    except KNOWN_ERRORS as error:
        raise _error(error) from error
    await redis.delete(STATUS_KEY.format(username=user["username"]))
    return result


@router.post("/account/orders/{order_id}/cancel", dependencies=checked_write)
async def order_cancel(order_id: str = Path(max_length=200, pattern=r"^[A-Za-z0-9_\-.=]+$"), user: dict = Depends(current_user)):
    if not (get_settings().trading_enabled and user.get("trading_enabled")):
        raise HTTPException(status_code=403, detail="실제 주문이 꺼져 있습니다(서버 TRADING_ENABLED와 계정의 '실제 주문 허용'이 모두 필요).")
    try:
        _, client = await _user_clients(user)
        result = await client.cancel_order(order_id)
    except KNOWN_ERRORS as error:
        raise _error(error) from error
    await redis.delete(STATUS_KEY.format(username=user["username"]))
    return {"order_id": (result or {}).get("orderId", order_id), "canceled": True}


@router.get("/watchlist", dependencies=checked)
async def watchlist(user: dict = Depends(current_user)):
    return {"items": await get_watchlist(user["username"])}


class WatchInput(BaseModel):
    symbol: str = Field(max_length=15)


@router.post("/watchlist", dependencies=checked_write)
async def watch_add(request: Request, payload: WatchInput, user: dict = Depends(current_user)):
    # 종목 확인은 시세용 서버 클라이언트로 합니다(토스 키를 아직 등록하지 않은 사용자도 관심 종목을 담을 수 있게).
    try:
        return {"items": await add_watch(request.app.state.toss_api, user["username"], payload.symbol)}
    except KNOWN_ERRORS as error:
        raise _error(error) from error


@router.delete("/watchlist/{symbol}", dependencies=checked_write)
async def watch_remove(symbol: str = Path(max_length=15, pattern=r"^[A-Za-z0-9.\-]+$"), user: dict = Depends(current_user)):
    return {"items": await remove_watch(user["username"], symbol)}


@router.get("/account/stock-status", dependencies=checked)
async def status(user: dict = Depends(current_user)) -> dict[str, Any]:
    """보유 종목과 관심 종목의 현황(1분 캐시). 토스 키가 없으면 관심 종목만(시세용 서버 연결로) 보여 줍니다."""
    try:
        if (user.get("toss") or {}).get("client_id"):
            api, client = await _user_clients(user)
        else:
            api, client = shared_api(), None
        return await stock_status(api, client, user["username"])
    except KNOWN_ERRORS as error:
        raise _error(error) from error

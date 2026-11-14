"""여러 사용자 로그인과 사용자별 토스증권 계좌 연결.

토스증권 Open API에는 "토스로 로그인"(OAuth 동의) 방식이 없어, 각 사용자가 자기 토스 앱(WTS 설정 > Open API)에서
발급한 client_id·client_secret을 이 서비스에 등록하고, 그 키의 허용 IP에 이 서버의 공인 IP를 넣어야 합니다.

- 비밀번호: scrypt 해시(평문 저장 안 함). 로그인 실패가 반복되면 잠시 잠급니다.
- 세션: 무작위 토큰을 HttpOnly·SameSite=Strict 쿠키로. Redis에 12시간 보관.
- 토스 키: client_secret은 APP_SECRET_KEY로 암호화(Fernet)해 저장하고, 화면·로그에는 내보내지 않습니다.
- 실제 주문: 서버 전체 TRADING_ENABLED와 사용자 본인의 "실제 주문 허용"이 모두 켜져야 합니다.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .briefing import KST
from .config import get_settings
from .redis_client import redis
from .toss_api import TossApiClient

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
MIN_PASSWORD = 8
SESSION_TTL = 12 * 60 * 60
SESSION_COOKIE = "msid"
LOGIN_FAIL_TTL = 15 * 60
LOGIN_FAIL_LIMIT_USER = 5
LOGIN_FAIL_LIMIT_IP = 20
MAX_CLIENTS = 50


class UserError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _user_key(username: str) -> str:
    return f"user:v1:{username}"


# --- 비밀번호 -------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- 토스 키 암호화 -----------------------------------------------------------------------------

def _fernet() -> Fernet:
    secret = get_settings().app_secret_key
    if not secret or len(secret) < 32:
        raise UserError("서버에 APP_SECRET_KEY(32자 이상)가 설정되지 않아 토스 키를 안전하게 저장할 수 없습니다. 서버 관리자에게 알려 주세요.", 503)
    key = base64.urlsafe_b64encode(hashlib.sha256(f"toss-credentials:{secret}".encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as error:
        raise UserError("저장된 토스 키를 풀 수 없습니다(서버 APP_SECRET_KEY가 바뀌었을 수 있음). 키를 다시 등록하세요.", 409) from error


def mask_client_id(client_id: str) -> str:
    return client_id[:6] + "…" + client_id[-4:] if len(client_id) > 12 else client_id[:3] + "…"


# --- 사용자·세션 -----------------------------------------------------------------------------

async def get_user(username: str) -> dict[str, Any] | None:
    raw = await redis.get(_user_key(username))
    return json.loads(raw) if raw else None


async def save_user(user: dict[str, Any]) -> None:
    await redis.set(_user_key(user["username"]), json.dumps(user, ensure_ascii=False))


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    toss = user.get("toss") or {}
    return {
        "username": user["username"],
        "created_at": user.get("created_at"),
        "trading_enabled": bool(user.get("trading_enabled")),
        "toss_connected": bool(toss.get("client_id")),
        "toss_client_id": mask_client_id(toss["client_id"]) if toss.get("client_id") else None,
        "server_trading_enabled": get_settings().trading_enabled,
    }


async def signup(username: str, password: str, invite_code: str = "") -> dict[str, Any]:
    settings = get_settings()
    if not settings.signup_enabled:
        raise UserError("지금은 새 가입을 받지 않습니다.", 403)
    if settings.signup_invite_code and not hmac.compare_digest(invite_code.strip(), settings.signup_invite_code):
        raise UserError("초대 코드가 맞지 않습니다.", 403)
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise UserError("아이디는 영문 소문자·숫자·밑줄 3~20자로 정하세요.")
    if len(password) < MIN_PASSWORD or len(password) > 128:
        raise UserError(f"비밀번호는 {MIN_PASSWORD}~128자로 정하세요.")
    user = {"username": username, "password": hash_password(password), "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "trading_enabled": False, "toss": None}
    # SET NX로 같은 아이디가 동시에 두 번 만들어지지 않게 합니다.
    if not await redis.set(_user_key(username), json.dumps(user, ensure_ascii=False), nx=True):
        raise UserError("이미 쓰는 아이디입니다.", 409)
    return user


async def _fail_count(key: str) -> int:
    return int(await redis.get(key) or 0)


async def login(username: str, password: str, client_ip: str) -> tuple[str, dict[str, Any]]:
    username = username.strip().lower()
    user_fail, ip_fail = f"login-fail:user:{username}", f"login-fail:ip:{client_ip}"
    if await _fail_count(user_fail) >= LOGIN_FAIL_LIMIT_USER or await _fail_count(ip_fail) >= LOGIN_FAIL_LIMIT_IP:
        raise UserError("로그인 실패가 많아 15분 동안 잠겼습니다. 잠시 뒤 다시 시도하세요.", 429)
    user = await get_user(username) if USERNAME_RE.fullmatch(username) else None
    if not user or not verify_password(password, user["password"]):
        for key in (user_fail, ip_fail):
            await redis.incr(key)
            await redis.expire(key, LOGIN_FAIL_TTL)
        raise UserError("아이디 또는 비밀번호가 맞지 않습니다.", 401)
    await redis.delete(user_fail)
    token = secrets.token_urlsafe(32)
    await redis.set(f"session:v1:{_session_hash(token)}", username, ex=SESSION_TTL)
    return token, user


def _session_hash(token: str) -> str:
    # 세션 토큰 원문은 Redis에도 남기지 않습니다(Redis가 새도 세션을 훔쳐 쓸 수 없게).
    return hashlib.sha256(token.encode()).hexdigest()


async def session_user(token: str | None) -> dict[str, Any] | None:
    if not token or len(token) > 100:
        return None
    username = await redis.get(f"session:v1:{_session_hash(token)}")
    if not username:
        return None
    username = username.decode() if isinstance(username, bytes) else username
    return await get_user(username)


async def logout(token: str | None) -> None:
    if token:
        await redis.delete(f"session:v1:{_session_hash(token)}")


async def delete_user(username: str) -> None:
    await redis.delete(_user_key(username), f"account:watchlist:v2:{username}", f"account:stock-status:v2:{username}", f"vp:v1:{username}")
    await drop_client(username)


# --- 사용자별 토스 연결 -------------------------------------------------------------------------

_clients: dict[str, tuple[str, TossApiClient]] = {}


async def drop_client(username: str) -> None:
    entry = _clients.pop(username, None)
    if entry and entry[1] is not _shared_api:
        await entry[1].close()


_shared_api: TossApiClient | None = None


def set_shared_api(api: TossApiClient) -> None:
    """서버 .env 키로 만든 시세용 클라이언트. 사용자가 같은 키를 등록하면 이것을 함께 씁니다.

    토스는 키 하나에 유효한 토큰을 하나만 두므로, 같은 키로 클라이언트를 따로 만들면 서로의 토큰을 끊어 버립니다.
    """
    global _shared_api
    _shared_api = api


def is_shared_api(api: TossApiClient) -> bool:
    return api is _shared_api


def shared_api() -> TossApiClient:
    if _shared_api is None:
        raise UserError("서버 시세 연결이 아직 준비되지 않았습니다.", 503)
    return _shared_api


def build_user_api(client_id: str, client_secret: str) -> TossApiClient:
    settings = get_settings()
    if _shared_api is not None and client_id == settings.toss_client_id:
        return _shared_api
    return TossApiClient(settings.model_copy(update={"toss_client_id": client_id, "toss_client_secret": client_secret}))


async def user_api(user: dict[str, Any]) -> TossApiClient:
    toss = user.get("toss") or {}
    if not toss.get("client_id"):
        raise UserError("먼저 토스증권 API 키를 등록하세요.", 409)
    cached = _clients.get(user["username"])
    if cached and cached[0] == toss["client_id"]:
        return cached[1]
    await drop_client(user["username"])
    if len(_clients) >= MAX_CLIENTS:
        oldest = next(iter(_clients))
        await drop_client(oldest)
    api = build_user_api(toss["client_id"], decrypt_secret(toss["client_secret"]))
    _clients[user["username"]] = (toss["client_id"], api)
    return api

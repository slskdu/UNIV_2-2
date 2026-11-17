"""토스증권 계좌 연결: 보유 종목, 주문 가능 금액, 주문(실제 매수·매도), 관심 종목과 종목 현황.

여러 사용자가 각자 자기 토스 키로 로그인해 씁니다(app/users.py). 관심 종목·현황·주문 확인 토큰은 사용자별로 나뉩니다.

주의: 토스증권 Open API에는 모의투자 환경이 없어 주문 API는 실제 계좌에서 실제로 체결됩니다.
그래서 주문은 다음 장치를 거칩니다.
- 서버 전체 `.env`의 TRADING_ENABLED=true이고, 그 사용자가 "실제 주문 허용"을 켰을 때만 주문·취소를 보냅니다.
- 한 번에 주문할 수 있는 예상 금액 상한(MAX_ORDER_AMOUNT_KRW / MAX_ORDER_AMOUNT_USD).
- 미리보기 → 확인 2단계. 미리보기가 만든 확인 토큰(2분 유효, 한 번만 사용)으로만 주문하며,
  같은 토큰을 토스 clientOrderId(중복 주문 방지 키)로 씁니다.
- 1억원 이상 주문에 필요한 confirmHighValueOrder는 보내지 않습니다(그런 주문은 토스가 거절).
"""
import asyncio
import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .briefing import KST
from .config import get_settings
from .history import describe_warnings, fetch_daily_candles, summarize_trend
from .redis_client import get_json, redis, set_json
from .toss_api import TossApiClient, TossApiError

logger = logging.getLogger(__name__)

SIDES = {"BUY": "매수", "SELL": "매도"}
ORDER_TYPES = {"LIMIT": "지정가", "MARKET": "시장가"}
ORDER_STATUS_LABELS = {
    "PENDING": "접수", "PARTIAL_FILLED": "일부 체결", "PENDING_CANCEL": "취소 중", "PENDING_REPLACE": "정정 중",
    "FILLED": "체결", "CANCELED": "취소됨", "REJECTED": "거부됨", "REPLACED": "정정됨",
    "CANCEL_REJECTED": "취소 거부", "REPLACE_REJECTED": "정정 거부",
}
CONFIRM_TTL_SECONDS = 120
WATCHLIST_KEY = "account:watchlist:v2:{username}"
WATCHLIST_MAX = 30
STATUS_TTL_SECONDS = 60
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")

# 토스 오류 코드 → 화면 문구(자주 나오는 것만). 나머지는 토스 메시지를 그대로 보여 줍니다.
ERROR_MESSAGES = {
    "order-hours-closed": "지금은 주문을 받는 시간이 아닙니다.",
    "insufficient-buying-power": "주문 가능 금액이 부족합니다.",
    "insufficient-sellable-quantity": "매도 가능 수량이 부족합니다.",
    "price-out-of-range": "주문 가격이 허용 범위(상·하한가)를 벗어났습니다.",
    "invalid-tick-size": "주문 가격이 호가 단위에 맞지 않습니다.",
    "stock-restricted": "이 종목은 지금 주문할 수 없습니다.",
    "account-restricted": "계좌가 주문 제한 상태입니다.",
    "opposite-pending-order-exists": "같은 종목에 반대 방향 미체결 주문이 있습니다.",
    "confirm-high-value-required": "1억원 이상 주문은 이 화면에서 보낼 수 없습니다.",
    "max-order-amount-exceeded": "토스 주문 한도(30억원)를 넘었습니다.",
    "already-filled": "이미 체결된 주문입니다.",
    "already-canceled": "이미 취소된 주문입니다.",
    "order-not-found": "주문을 찾을 수 없습니다.",
    "request-in-progress": "같은 주문을 처리하는 중입니다.",
    "ip-not-allowed": "토스 Open API 허용 IP에 이 컴퓨터의 IP가 등록되어 있지 않습니다.",
}


class AccountError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None, data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.data = data


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AccountClient:
    """계좌·자산·주문 API. TossApiClient의 HTTP 연결과 토큰을 함께 씁니다."""

    def __init__(self, api: TossApiClient):
        self.api = api
        self._account_seq: str | None = None
        self._account_lock = asyncio.Lock()

    async def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None, account: bool = True) -> Any:
        token = await self.api.token_manager.get_access_token()
        seq = await self.account_seq() if account else None

        async def send(access_token: str) -> httpx.Response:
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            if seq:
                headers["X-Tossinvest-Account"] = seq
            return await self.api.client.request(method, path, params=params, json=body, headers=headers)

        response = await send(token)
        if response.status_code == 401:
            self.api.token_manager._access_token = None
            token = await self.api.token_manager.get_access_token()
            response = await send(token)
        if response.status_code == 429:
            await asyncio.sleep(max(0.0, float(response.headers.get("Retry-After", "1"))))
            response = await send(token)
        if response.is_error:
            try:
                error = response.json().get("error") or {}
            except ValueError:
                error = {}
            code = error.get("code")
            message = ERROR_MESSAGES.get(code or "", error.get("message") or f"토스 API 오류({response.status_code})")
            raise AccountError(message, response.status_code, code, error.get("data"))
        return response.json().get("result")

    async def account_seq(self) -> str:
        """X-Tossinvest-Account 값. .env의 TOSS_ACCOUNT_SEQ가 실제 계좌와 맞으면 그것을, 아니면 첫 위탁(BROKERAGE) 계좌를 씁니다."""
        if self._account_seq:
            return self._account_seq
        async with self._account_lock:
            if not self._account_seq:
                accounts = await self._request("GET", "/api/v1/accounts", account=False) or []
                seqs = [str(item["accountSeq"]) for item in accounts if item.get("accountSeq") is not None]
                configured = get_settings().toss_account_seq
                if configured and str(configured) in seqs:
                    self._account_seq = str(configured)
                else:
                    if configured:
                        logger.warning("TOSS_ACCOUNT_SEQ does not match any account; using the first brokerage account")
                    brokerage = [item for item in accounts if item.get("accountType") == "BROKERAGE"] or accounts
                    if not brokerage:
                        raise AccountError("연결된 토스증권 위탁 계좌가 없습니다.", 404, "account-not-found")
                    self._account_seq = str(brokerage[0]["accountSeq"])
        return self._account_seq

    async def accounts(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v1/accounts", account=False) or []

    async def holdings(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/holdings") or {}

    async def buying_power(self, currency: str) -> float | None:
        result = await self._request("GET", "/api/v1/buying-power", params={"currency": currency}) or {}
        return _num(result.get("cashBuyingPower"))

    async def sellable_quantity(self, symbol: str) -> float:
        result = await self._request("GET", "/api/v1/sellable-quantity", params={"symbol": symbol}) or {}
        return _num(result.get("sellableQuantity")) or 0.0

    async def orders(self, status: str = "OPEN", limit: int = 20) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": status}
        if status == "CLOSED":
            params.update({"limit": limit, "from": (date.today() - timedelta(days=7)).isoformat(), "to": date.today().isoformat()})
        result = await self._request("GET", "/api/v1/orders", params=params) or {}
        return result.get("orders", [])

    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/orders", body=body) or {}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/v1/orders/{order_id}/cancel", body={}) or {}


def mask_account_no(account_no: str) -> str:
    text = str(account_no or "")
    return text[:3] + "*" * max(0, len(text) - 5) + text[-2:] if len(text) > 5 else "*" * len(text)


def _percent(value: Any) -> float | None:
    """토스 수익률(rate)은 비율(-0.9132 = -91.32%)이라 %로 바꿉니다."""
    ratio = _num(value)
    return None if ratio is None else round(ratio * 100, 2)


def normalize_holdings(raw: dict[str, Any]) -> dict[str, Any]:
    """보유 종목 응답을 화면용 숫자로 바꿉니다. 수익률은 % 단위로 바꿔 둡니다."""
    def money(node: Any) -> dict[str, float | None]:
        node = node or {}
        return {"krw": _num(node.get("krw")), "usd": _num(node.get("usd"))}

    items = []
    for item in raw.get("items", []):
        items.append({
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "market": item.get("marketCountry"),
            "currency": item.get("currency"),
            "quantity": _num(item.get("quantity")),
            "last_price": _num(item.get("lastPrice")),
            "average_price": _num(item.get("averagePurchasePrice")),
            "purchase_amount": _num((item.get("marketValue") or {}).get("purchaseAmount")),
            "market_value": _num((item.get("marketValue") or {}).get("amount")),
            "profit_loss": _num((item.get("profitLoss") or {}).get("amount")),
            "profit_rate": _percent((item.get("profitLoss") or {}).get("rate")),
            "daily_profit_loss": _num((item.get("dailyProfitLoss") or {}).get("amount")),
            "daily_rate": _percent((item.get("dailyProfitLoss") or {}).get("rate")),
        })
    items.sort(key=lambda item: -(item["market_value"] or 0))
    return {
        "total_purchase": money(raw.get("totalPurchaseAmount")),
        "market_value": money((raw.get("marketValue") or {}).get("amount")),
        "profit_loss": money((raw.get("profitLoss") or {}).get("amount")),
        "profit_rate": _percent((raw.get("profitLoss") or {}).get("rate")),
        "daily_profit_loss": money((raw.get("dailyProfitLoss") or {}).get("amount")),
        "daily_rate": _percent((raw.get("dailyProfitLoss") or {}).get("rate")),
        "items": items,
    }


def normalize_order(order: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    execution = order.get("execution") or {}
    return {
        "order_id": order.get("orderId"),
        "symbol": order.get("symbol"),
        "name": (names or {}).get(order.get("symbol") or "", order.get("symbol")),
        "side": order.get("side"),
        "side_label": SIDES.get(order.get("side") or "", order.get("side")),
        "order_type": order.get("orderType"),
        "order_type_label": ORDER_TYPES.get(order.get("orderType") or "", order.get("orderType")),
        "status": order.get("status"),
        "status_label": ORDER_STATUS_LABELS.get(order.get("status") or "", order.get("status")),
        "price": _num(order.get("price")),
        "quantity": _num(order.get("quantity")),
        "filled_quantity": _num(execution.get("filledQuantity")) or 0.0,
        "average_filled_price": _num(execution.get("averageFilledPrice")),
        "currency": order.get("currency"),
        "ordered_at": order.get("orderedAt"),
        "cancelable": order.get("status") in {"PENDING", "PARTIAL_FILLED"},
    }


async def account_summary(api: TossApiClient, client: AccountClient, user_trading: bool = False) -> dict[str, Any]:
    accounts = await client.accounts()
    seq = await client.account_seq()
    holdings = normalize_holdings(await client.holdings())
    krw, usd = await asyncio.gather(client.buying_power("KRW"), client.buying_power("USD"))
    open_orders = await client.orders("OPEN")
    names = {item["symbol"]: item["name"] for item in holdings["items"]}
    missing = [order.get("symbol") for order in open_orders if order.get("symbol") not in names]
    if missing:
        try:
            names.update(await api.fetch_stock_names(missing))
        except TossApiError:
            pass
    settings = get_settings()
    account = next((item for item in accounts if str(item.get("accountSeq")) == seq), accounts[0] if accounts else {})
    return {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "account": {"number": mask_account_no(account.get("accountNo", "")), "type": account.get("accountType")},
        "holdings": holdings,
        "buying_power": {"KRW": krw, "USD": usd},
        "open_orders": [normalize_order(order, names) for order in open_orders],
        "trading": {
            "enabled": settings.trading_enabled and user_trading,
            "server_enabled": settings.trading_enabled,
            "user_enabled": user_trading,
            "max_amount": {"KRW": settings.max_order_amount_krw, "USD": settings.max_order_amount_usd},
        },
    }


# --- 환율·종목 시세 -------------------------------------------------------------------------

FX_KEY = "account:fx:usd-krw:v1"
FX_TTL_SECONDS = 60  # 토스 환율은 1분마다 갱신되는 참고용 값입니다


async def usd_krw_rate(api: TossApiClient) -> dict[str, Any] | None:
    """1달러 = ?원. mid는 매매기준율, buy는 매수 환율(참고용, 실제 체결 환율과 다를 수 있음)."""
    cached = await get_json(FX_KEY)
    if cached:
        return cached
    try:
        result = await api._get_result("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"}) or {}
    except TossApiError:
        logger.exception("exchange rate failed")
        return None
    mid, buy = _num(result.get("midRate")), _num(result.get("rate"))
    if not mid:
        return None
    fx = {"mid": mid, "buy": buy, "valid_from": result.get("validFrom")}
    await set_json(FX_KEY, fx, FX_TTL_SECONDS)
    return fx


async def stock_quote(api: TossApiClient, symbol: str) -> dict[str, Any]:
    """주문 입력 칸용: 종목 이름, 거래 통화, 최근 종가, (미국 주식이면) 환율."""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise AccountError("종목코드를 확인하세요(예: 005930, AAPL).")
    stock = (await api.fetch_stock_details([symbol])).get(symbol)
    if not stock or not stock.get("name"):
        raise AccountError("토스증권에서 찾을 수 없는 종목입니다.", 404)
    currency = stock.get("currency") or ("USD" if re.search(r"[A-Z]", symbol) else "KRW")
    candles = await fetch_daily_candles(api, symbol, date.today() - timedelta(days=10), date.today())
    return {
        "symbol": symbol, "name": stock["name"], "currency": currency,
        "last_price": candles[-1]["close"] if candles else None,
        "fx": await usd_krw_rate(api) if currency == "USD" else None,
    }


def price_tick(value: float) -> float:
    """미국 주식 가격 자릿수: $1 이상은 소수 둘째, 미만은 넷째 자리까지."""
    return round(value, 2 if value >= 1 else 4)


def convert_krw_price(price_krw: float, fx: dict[str, Any] | None) -> float:
    """원으로 입력한 미국 주식 가격을 달러로 바꿉니다(매매기준율). 토스 주문은 미국 주식을 달러로만 받습니다."""
    if not fx or not fx.get("mid"):
        raise AccountError("환율을 불러오지 못해 원 가격을 달러로 바꿀 수 없습니다. 달러로 입력해 주세요.", 503)
    converted = price_tick(price_krw / fx["mid"])
    if converted <= 0:
        raise AccountError("원 가격이 너무 작아 달러로 바꿀 수 없습니다.")
    return converted


# --- 주문 미리보기·확인 ---------------------------------------------------------------------

def validate_order_input(order: dict[str, Any], currency: str) -> dict[str, Any]:
    """화면 입력을 토스 주문 본문으로 바꿉니다. 형식이 틀리면 AccountError."""
    side = str(order.get("side") or "").upper()
    order_type = str(order.get("order_type") or "").upper()
    if side not in SIDES:
        raise AccountError("매수 또는 매도를 고르세요.")
    if order_type not in ORDER_TYPES:
        raise AccountError("지정가 또는 시장가를 고르세요.")
    quantity = _num(order.get("quantity"))
    if not quantity or quantity <= 0 or not math.isfinite(quantity):
        raise AccountError("수량을 0보다 크게 입력하세요.")
    # 소수 수량은 미국 주식 시장가 매도만 됩니다.
    if quantity != int(quantity) and not (currency == "USD" and order_type == "MARKET" and side == "SELL"):
        raise AccountError("수량은 정수로 입력하세요(소수 수량은 미국 주식 시장가 매도만 가능).")
    body: dict[str, Any] = {
        "symbol": order["symbol"], "side": side, "orderType": order_type, "timeInForce": "DAY",
        "quantity": int(quantity) if quantity == int(quantity) else round(quantity, 6),
    }
    if order_type == "LIMIT":
        price = _num(order.get("price"))
        if not price or price <= 0 or not math.isfinite(price):
            raise AccountError("지정가 주문은 가격을 입력하세요.")
        if currency == "KRW":
            if price != int(price):
                raise AccountError("국내 주식 가격은 원 단위 정수로 입력하세요.")
            body["price"] = int(price)
        else:
            body["price"] = round(price, 2 if price >= 1 else 4)
    return body


def order_limit_problem(estimated_amount: float | None, currency: str) -> str | None:
    settings = get_settings()
    limit = settings.max_order_amount_krw if currency == "KRW" else settings.max_order_amount_usd
    if estimated_amount is None:
        return "예상 주문 금액을 계산하지 못해 주문할 수 없습니다."
    if estimated_amount > limit:
        unit = "원" if currency == "KRW" else "달러"
        return f"예상 주문 금액이 이 화면의 1회 주문 상한({limit:,.0f}{unit})을 넘습니다. .env의 MAX_ORDER_AMOUNT_{currency}로 조정할 수 있습니다."
    return None


async def preview_order(api: TossApiClient, client: AccountClient, order: dict[str, Any], username: str = "", user_trading: bool = False) -> dict[str, Any]:
    symbol = str(order.get("symbol") or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise AccountError("종목코드를 확인하세요(예: 005930, AAPL).")
    stock = (await api.fetch_stock_details([symbol])).get(symbol)
    if not stock or not stock.get("name"):
        raise AccountError("토스증권에서 찾을 수 없는 종목입니다.", 404)
    currency = stock.get("currency") or ("USD" if re.search(r"[A-Z]", symbol) else "KRW")
    # 토스 주문 가격은 종목의 거래 통화(국내 원, 미국 달러)로만 보냅니다. 미국 주식을 원으로 입력하면 환율로 바꿉니다.
    price_currency = str(order.get("price_currency") or currency).upper()
    fx = await usd_krw_rate(api) if currency == "USD" else None
    input_price_krw = None
    if price_currency not in {"KRW", "USD"}:
        raise AccountError("가격 단위는 원 또는 달러로 고르세요.")
    if currency == "KRW" and price_currency == "USD":
        raise AccountError("국내 주식은 원으로만 주문할 수 있습니다(호가 단위가 원이라 달러 가격을 바꿔 넣을 수 없음).")
    if currency == "USD" and price_currency == "KRW" and str(order.get("order_type") or "").upper() == "LIMIT" and _num(order.get("price")):
        input_price_krw = _num(order.get("price"))
        order = {**order, "price": convert_krw_price(input_price_krw, fx)}
    body = validate_order_input({**order, "symbol": symbol}, currency)

    candles = await fetch_daily_candles(api, symbol, date.today() - timedelta(days=10), date.today())
    last_price = candles[-1]["close"] if candles else None
    reference_price = body.get("price") or last_price
    estimated = reference_price * body["quantity"] if reference_price else None

    problems: list[str] = []
    notes: list[str] = []
    limit_problem = order_limit_problem(estimated, currency)
    if limit_problem:
        problems.append(limit_problem)
    if body["side"] == "BUY":
        power = await client.buying_power(currency)
        if power is not None and estimated is not None and estimated > power:
            problems.append(f"주문 가능 금액({power:,.2f} {currency})보다 예상 금액이 큽니다.")
        available = {"buying_power": power}
    else:
        sellable = await client.sellable_quantity(symbol)
        if body["quantity"] > sellable:
            problems.append(f"매도 가능 수량({sellable:g}주)보다 많습니다.")
        available = {"sellable_quantity": sellable}
    if body["orderType"] == "MARKET":
        notes.append("시장가 주문은 체결 순간 가격으로 체결되어 예상 금액과 다를 수 있습니다.")
    if input_price_krw is not None:
        notes.append(
            f"입력한 {input_price_krw:,.0f}원을 매매기준율 1달러 = {fx['mid']:,.2f}원으로 바꿔 ${body['price']:,.{2 if body['price'] >= 1 else 4}f}에 주문합니다. "
            "환율은 1분마다 바뀌는 참고값이라 원화 기준 금액은 실제와 조금 다를 수 있습니다."
        )
    if last_price and body.get("price") and abs(body["price"] / last_price - 1) > 0.1:
        notes.append(f"주문 가격이 최근 종가({last_price:,.2f})와 10% 넘게 차이 납니다.")
    warnings = describe_warnings(await api.fetch_stock_warnings(symbol))
    notes.extend(f"매수 유의사항: {warning['label']}" for warning in warnings)

    settings = get_settings()
    trading = settings.trading_enabled and user_trading
    preview = {
        "symbol": symbol, "name": stock["name"], "currency": currency,
        "side": body["side"], "side_label": SIDES[body["side"]],
        "order_type": body["orderType"], "order_type_label": ORDER_TYPES[body["orderType"]],
        "quantity": body["quantity"], "price": body.get("price"), "last_price": last_price,
        "estimated_amount": round(estimated, 2) if estimated is not None else None,
        "input_price_krw": input_price_krw,
        "fx_rate": fx["mid"] if fx else None,
        "estimated_amount_krw": round(estimated * fx["mid"]) if fx and estimated is not None else None,
        **available, "problems": problems, "notes": notes,
        "trading_enabled": trading, "server_trading_enabled": settings.trading_enabled, "user_trading_enabled": user_trading,
        "confirm_token": None,
    }
    if not problems and trading:
        token = str(uuid.uuid4())
        await set_json(f"account:order-confirm:{token}", {"body": body, "currency": currency, "estimated": estimated, "name": stock["name"], "username": username}, CONFIRM_TTL_SECONDS)
        preview["confirm_token"] = token
        preview["confirm_seconds"] = CONFIRM_TTL_SECONDS
    return preview


async def confirm_order(client: AccountClient, token: str, username: str = "", user_trading: bool = False) -> dict[str, Any]:
    """미리보기 토큰으로 실제 주문을 보냅니다. 토큰은 한 번만, 그 토큰을 만든 사용자만 쓸 수 있습니다."""
    if not get_settings().trading_enabled:
        raise AccountError("서버에서 실제 주문이 꺼져 있습니다(.env의 TRADING_ENABLED).", 403, "trading-disabled")
    if not user_trading:
        raise AccountError("계정 설정에서 '실제 주문 허용'을 켜야 주문할 수 있습니다.", 403, "user-trading-disabled")
    if not re.fullmatch(r"[0-9a-f-]{36}", token or ""):
        raise AccountError("확인 정보가 올바르지 않습니다. 다시 미리보기를 해 주세요.")
    raw = await redis.getdel(f"account:order-confirm:{token}")
    if not raw:
        raise AccountError("확인 시간이 지났거나 이미 처리된 주문입니다. 다시 미리보기를 해 주세요.", 410, "confirm-expired")
    saved = json.loads(raw)
    if saved.get("username", "") != username:
        raise AccountError("다른 사용자가 만든 주문 확인 정보입니다.", 403, "confirm-owner-mismatch")
    problem = order_limit_problem(saved["estimated"], saved["currency"])
    if problem:
        raise AccountError(problem)
    body = {**saved["body"], "clientOrderId": token}  # 같은 주문이 두 번 들어가지 않게 하는 토스 중복 방지 키
    result = await client.create_order(body)
    logger.info("order placed: %s %s %s x%s (orderId=%s)", body["side"], body["symbol"], body["orderType"], body["quantity"], result.get("orderId"))
    return {"order_id": result.get("orderId"), "name": saved["name"], **{key: body[key] for key in ("symbol", "side", "orderType", "quantity")}}


# --- 관심 종목과 종목 현황 -------------------------------------------------------------------

async def get_watchlist(username: str) -> list[dict[str, Any]]:
    return await get_json(WATCHLIST_KEY.format(username=username)) or []


async def add_watch(api: TossApiClient, username: str, symbol: str) -> list[dict[str, Any]]:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise AccountError("종목코드를 확인하세요(예: 005930, AAPL).")
    items = await get_watchlist(username)
    if any(item["symbol"] == symbol for item in items):
        return items
    if len(items) >= WATCHLIST_MAX:
        raise AccountError(f"관심 종목은 {WATCHLIST_MAX}개까지 담을 수 있습니다.")
    stock = (await api.fetch_stock_details([symbol])).get(symbol)
    if not stock or not stock.get("name"):
        raise AccountError("토스증권에서 찾을 수 없는 종목입니다.", 404)
    items.append({"symbol": symbol, "name": stock["name"], "added_at": datetime.now(KST).strftime("%Y-%m-%d")})
    await redis.set(WATCHLIST_KEY.format(username=username), json.dumps(items, ensure_ascii=False))
    await redis.delete(STATUS_KEY.format(username=username))
    return items


async def remove_watch(username: str, symbol: str) -> list[dict[str, Any]]:
    items = [item for item in await get_watchlist(username) if item["symbol"] != symbol.strip().upper()]
    await redis.set(WATCHLIST_KEY.format(username=username), json.dumps(items, ensure_ascii=False))
    await redis.delete(STATUS_KEY.format(username=username))
    return items


STATUS_KEY = "account:stock-status:v3:{username}"


def status_lines(snapshot: dict[str, Any], holding: dict[str, Any] | None, news: dict[str, Any] | None) -> list[dict[str, str]]:
    """종목 현황을 한 줄씩(tone: pos/neg/warn/info) 정리합니다."""
    lines: list[dict[str, str]] = []
    day = snapshot.get("day_change")
    if day is not None:
        tone = "pos" if day > 0 else "neg" if day < 0 else "info"
        lines.append({"tone": tone, "text": f"최근 거래일 {day:+.2f}% (종가 {snapshot['close']:,.2f})"})
    rates = snapshot["rates"]
    if rates.get("20d") is not None:
        lines.append({"tone": "pos" if rates["20d"] > 0 else "neg", "text": f"20일 {rates['20d']:+.2f}% · 60일 {rates['60d']:+.2f}%" if rates.get("60d") is not None else f"20일 {rates['20d']:+.2f}%"})
    lines.append({"tone": "info", "text": "주가가 20일 평균선 위" if snapshot["above_ma20"] else "주가가 20일 평균선 아래"})
    if (snapshot.get("volatility") or 0) > 5:
        lines.append({"tone": "warn", "text": f"하루 평균 변동폭 {snapshot['volatility']:.1f}%로 큼"})
    if snapshot.get("max_drawdown") is not None and snapshot["max_drawdown"] <= -20:
        lines.append({"tone": "warn", "text": f"최근 60일 고점 대비 최대 {snapshot['max_drawdown']:.1f}%"})
    for warning in snapshot["warnings"]:
        lines.append({"tone": "warn", "text": f"매수 유의사항: {warning['label']}"})
    event = snapshot.get("price_event")
    if event:
        lines.append({"tone": "warn", "text": f"{event['date']} 하루 {event['change_rate']:+.1f}% 급변: 병합·분할 같은 권리 변동이면 위 수익률은 실제 손익과 다를 수 있습니다."})
    if holding and holding.get("last_price") and snapshot.get("close") and abs(holding["last_price"] / snapshot["close"] - 1) > 0.3:
        lines.append({"tone": "warn", "text": (
            f"시세 자료 종가({snapshot['close']:,.2f})와 토스 보유 현재가({holding['last_price']:,.2f})가 크게 달라, "
            "병합·분할 직후라 시세 자료가 아직 반영되지 않았을 수 있습니다. 보유 손익은 토스 기준이 정확합니다.")})
    if holding:
        rate = holding.get("profit_rate")
        lines.append({
            "tone": "pos" if (rate or 0) > 0 else "neg" if (rate or 0) < 0 else "info",
            "text": f"보유 {holding['quantity']:g}주 · 평균가 {holding['average_price']:,.2f} · 수익률 {rate:+.2f}%" if rate is not None else f"보유 {holding['quantity']:g}주",
        })
    if news:
        lines.append({"tone": "pos" if news["positive"] > news["negative"] else "neg" if news["negative"] > news["positive"] else "info",
                      "text": f"최근 기사 호재 {news['positive']}·악재 {news['negative']}건 (논조 '{news['tone']}')"})
    return lines


async def stock_status(api: TossApiClient, client: AccountClient | None, username: str) -> dict[str, Any]:
    """보유 종목과 관심 종목 각각의 현황(최근 등락, 20·60일 흐름, 평균선, 변동성, 유의사항, 보유 손익, 기사 논조)."""
    cached = await get_json(STATUS_KEY.format(username=username))
    if cached is not None:
        return cached
    from .recommendations import stock_snapshot  # 순환 import를 피하려 함수 안에서 불러옵니다

    holdings = normalize_holdings(await client.holdings()) if client else {"items": []}
    held = {item["symbol"]: item for item in holdings["items"]}
    watch = await get_watchlist(username)
    entries = [{"symbol": symbol, "name": item["name"], "held": True, "watched": False} for symbol, item in held.items()]
    for item in watch:
        existing = next((entry for entry in entries if entry["symbol"] == item["symbol"]), None)
        if existing:
            existing["watched"] = True
        else:
            entries.append({"symbol": item["symbol"], "name": item["name"], "held": False, "watched": True})

    semaphore = asyncio.Semaphore(2)

    async def one(entry: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                snapshot = await stock_snapshot(api, {"symbol": entry["symbol"], "name": entry["name"]})
            except Exception:
                logger.exception("status snapshot failed: %s", entry["symbol"])
                snapshot = None
        if not snapshot:
            return {**entry, "lines": [{"tone": "info", "text": "주가 자료를 불러오지 못했습니다."}]}
        news_cache = await get_json(f"insight:stock-news:v10:{entry['symbol']}:7")
        news = news_cache.get("news_digest") if news_cache and news_cache.get("found") else None
        holding = held.get(entry["symbol"])
        exit_signal = None
        if holding and holding.get("average_price"):
            # 실제 계좌는 산 날을 몰라 평균가와 최근 60일 최고 종가로 봅니다(app/exit_signals.py).
            from .exit_signals import exit_signals
            from .signals import compute_signals

            evidence = compute_signals(snapshot["_candles"], snapshot["warnings"])
            exit_signal = exit_signals(snapshot["_candles"], holding["average_price"], None, evidence["score"])
        return {
            **entry, "close": snapshot["close"], "currency": snapshot["currency"], "day_change": snapshot.get("day_change"),
            "rates": snapshot["rates"], "lines": status_lines(snapshot, holding, news),
            "news_loaded": bool(news), "exit_signal": exit_signal,
        }

    result = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": list(await asyncio.gather(*(one(entry) for entry in entries))),
    }
    await set_json(STATUS_KEY.format(username=username), result, STATUS_TTL_SECONDS)
    return result

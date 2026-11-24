"""자동 운용 가상 포트폴리오: 매도 신호가 나면 팔고, 그 돈으로 최신 AI분석 추천 종목을 다시 삽니다(계산만, 실제 주문 없음).

같은 매매 규칙(Book)을 두 곳에서 씁니다.
- 지난 기간 다시 계산(replay_book): 저장된 추천 기록(rec:history)과 일봉으로, 시가에 사고 종가에 매도 신호를 봅니다.
  자동 운용을 켠 순간까지를 이렇게 계산해 장부(ledger)의 출발점으로 삼습니다.
- 장중(live_tick): 서버가 장중 5분마다(app/managed_live.py) 현재가로 매도 신호를 보고 팔고, 다음 확인 때 그때 가격으로 삽니다.
  장중 매매는 다시 계산할 수 없으므로 Redis 장부에 남깁니다.

규칙
- 매도 신호: 산 뒤 +5%(arm) 넘게 오른 적이 있고 최고가 대비 −10%(trail)면 그 가격에 팝니다. 그 종목은 다음 새 추천까지 다시 사지 않습니다.
- 다시 사기: 그 시점까지 나와 있던 가장 최근 추천에서, 안 가진 종목을 판 돈 그대로 하나씩 삽니다.
- rotate(새 추천으로 교체): 새 추천이 나오면 추천에서 빠진 보유 종목을 팝니다.
  종목 수를 정하지 않았으면(requested_count 없음) 판 돈을 새 추천 종목 전부에 똑같이 나눠 사서 종목 수가 추천을 따릅니다.
- 판 돈만 다시 투자하고, 처음에 남겨 둔 현금(약세장 절반 투자, 정수 주로 사고 남은 돈)은 그대로 둡니다.
- 거래 비용은 왕복 cost(살 때·팔 때 절반씩), 주식은 정수 주.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from .decision import WEAK_INVEST_FRACTION, Context, exit_reason, rank_buys, snapshot_info

KR_OPEN, US_OPEN = "09:00", "22:30"  # 다시 계산할 때: 이 시각 전에 만든 추천만 그날 시가 매수에 씁니다(미장은 서머타임 개장 시각으로 보수적으로)
SOURCE_KINDS = {"ai": ["ai_pick"], "theme": ["pick"], "all": ["combined", "ai_pick", "pick"]}
CORE_KINDS = {"ai": ["ai_pick"], "theme": ["pick"], "all": ["combined", "ai_pick"]}

Price = Callable[[str], "float | None"]


def _candidates(snapshot: dict[str, Any], kinds: list[str]) -> list[tuple[str, str]]:
    seen, out = set(), []
    for kind in kinds:
        items = [item for item in snapshot.get("items", []) if item.get("kind") == kind]
        if kind == "pick":
            items.sort(key=lambda item: -(item.get("evidence") or 0))
        for item in items:
            if item["symbol"] not in seen:
                seen.add(item["symbol"])
                out.append((item["symbol"], item["name"]))
    return out


def snapshot_candidates(snapshot: dict[str, Any], source: str) -> list[tuple[str, str]]:
    """추천 기록 하나에서 살 후보(순서대로). ai = AI 추천, theme = 테마 종목(근거 점수 순), all = 통합 → AI → 테마."""
    return _candidates(snapshot, SOURCE_KINDS.get(source, SOURCE_KINDS["all"]))


def core_candidates(snapshot: dict[str, Any], source: str) -> list[tuple[str, str]]:
    """추천이 고른 종목 그 자체(채우기용 테마 후보 없이): ai = AI 추천, theme = 테마 종목, all = 통합 추천 + AI 추천."""
    return _candidates(snapshot, CORE_KINDS.get(source, CORE_KINDS["all"]))


def usable_snapshots(snapshots: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """이 방식(source)으로 살 종목이 있는 기록만, 만든 시각 순. 빈 기록(계산 실패 등)은 없는 것으로 칩니다(교체 모드에서 전부 팔지 않게)."""
    return sorted((item for item in snapshots if snapshot_candidates(item, source)),
                  key=lambda item: item.get("generated_at") or item["date"])


def snapshot_id(snapshot: dict[str, Any]) -> str:
    """추천 기록 구분: 장중에 30분마다 다시 계산하면 같은 날짜라도 만든 시각이 달라 새 추천으로 봅니다."""
    return snapshot.get("generated_at") or snapshot["date"]


def available(snapshot: dict[str, Any], day: str, market: str) -> bool:
    """그날 장이 열리기 전에 나와 있던 추천인지."""
    generated = snapshot.get("generated_at") or f"{snapshot['date']} 23:59"
    return generated < f"{day} {KR_OPEN if market.upper() == 'KR' else US_OPEN}"


class Book:
    """자동 운용 장부. state()는 JSON으로 저장해 이어 쓸 수 있습니다."""

    def __init__(self, portfolio: dict[str, Any], cost: float, trail: float, arm: float, rotate: bool,
                 state: dict[str, Any] | None = None):
        self.source = portfolio.get("source", "all")
        self.picks = portfolio["picks"]
        self.slots = len(self.picks)
        # 종목 수를 정하지 않았으면(None) 추천이 고른 수를 따릅니다.
        self.follow_count = portfolio.get("requested_count") is None
        self.capital = float(portfolio["capital"])
        self.cost, self.trail, self.arm, self.rotate = cost, trail, arm, rotate
        state = state or {}
        self.started = bool(state.get("started"))
        self.cash = float(state.get("cash", self.capital))
        self.held: dict[str, dict[str, Any]] = {symbol: dict(item) for symbol, item in (state.get("held") or {}).items()}
        self.pending: list[float] = list(state.get("pending") or [])  # 다시 살 돈(판 금액 하나당 한 자리)
        self.banned: set[str] = set(state.get("banned") or [])
        self.used_snapshot: str | None = state.get("used_snapshot")
        self.closed: list[dict[str, Any]] = list(state.get("closed") or [])
        self.trades: list[dict[str, Any]] = list(state.get("trades") or [])
        self.series: list[dict[str, Any]] = list(state.get("series") or [])
        self.reserve = float(state.get("reserve") or 0)  # 약세장이라 사지 않고 남겨 둔 돈(시장이 회복되면 다시 투자)
        self.missing: set[str] = set()

    def state(self) -> dict[str, Any]:
        return {"started": self.started, "cash": round(self.cash, 4), "held": self.held, "pending": [round(value, 4) for value in self.pending],
                "banned": sorted(self.banned), "used_snapshot": self.used_snapshot, "closed": self.closed,
                "trades": self.trades, "series": self.series, "reserve": round(self.reserve, 4)}

    def _order(self, options: list[tuple[str, str]], snapshot: dict[str, Any], ctx: Context | None) -> list[tuple[str, str, str]]:
        """판단 모듈이 있으면 걸러서 점수 순으로, 없으면 추천 순서 그대로."""
        if ctx is None:
            return [(symbol, name, "") for symbol, name in options]
        return rank_buys(options, snapshot, ctx)

    def _budget(self, amount: float, ctx: Context | None) -> float:
        """약세장이면 절반만 쓰고 나머지는 남겨 둡니다(reserve)."""
        if ctx is not None and ctx.weak_market:
            self.reserve += amount * (1 - WEAK_INVEST_FRACTION)
            return amount * WEAK_INVEST_FRACTION
        return amount

    # --- 매매 ---
    def buy(self, symbol: str, name: str, budget: float, price: float | None, day: str, reason: str,
            when: str | None = None, weight: float | None = None, theme: str = "") -> bool:
        if not price or budget <= 0:
            return False
        shares = math.floor(budget / (price * (1 + self.cost / 2)))
        if shares <= 0:
            return False
        paid = shares * price * (1 + self.cost / 2)
        self.cash -= paid
        self.held[symbol] = {"symbol": symbol, "name": name, "shares": shares, "buy_price": price, "buy_date": day,
                             "cost": paid, "peak": price, "last_price": price, **({"weight": weight} if weight is not None else {}),
                             **({"buy_time": when} if when else {}), **({"theme": theme} if theme else {}), "buy_reason": reason}
        self.trades.append({"date": day, **({"time": when} if when else {}), "action": "buy", "symbol": symbol, "name": name,
                            "shares": shares, "price": price, "reason": reason})
        return True

    def sell(self, symbol: str, price: float, day: str, reason: str, when: str | None = None) -> float:
        position = self.held.pop(symbol)
        received = position["shares"] * price * (1 - self.cost / 2)
        self.cash += received
        result = received / position["cost"] - 1
        self.closed.append({**position, "status": "sold", "sold_date": day, "sold_price": price, "last_price": price, "last_date": day,
                            "value": round(received, 2), "profit": round(received - position["cost"], 2), "return": round(result, 4),
                            "exit_reason": reason, **({"sold_time": when} if when else {})})
        self.trades.append({"date": day, **({"time": when} if when else {}), "action": "sell", "symbol": symbol, "name": position["name"],
                            "shares": position["shares"], "price": price, "reason": reason, "return": round(result, 4)})
        return received

    # --- 하루(또는 한 번 확인)의 단계 ---
    def start(self, price_of: Price, day: str, snapshot: dict[str, Any] | None, when: str | None = None) -> None:
        """처음 매수: 만들 때 고른 종목을 정한 비중대로. 못 산 자리는 추천 종목으로 채웁니다."""
        for pick in self.picks:
            budget = self.capital * pick.get("weight", 1 / len(self.picks))
            if not self.buy(pick["symbol"], pick["name"], budget, price_of(pick["symbol"]), day, "처음 매수", when, pick.get("weight")) and budget > 0:
                self.pending.append(budget)
        self.used_snapshot = snapshot_id(snapshot) if snapshot else None
        self.started = True

    def rebalance(self, snapshot: dict[str, Any] | None, price_of: Price, day: str, known: Callable[[str], bool],
                  when: str | None = None, ctx: Context | None = None, buy_price_of: Price | None = None,
                  exclude: set[str] | frozenset[str] = frozenset()) -> bool:
        """새 추천 반영(교체) + 빈 자리 채우기. 아직 가격을 모르는 종목이 필요하면 self.missing에 넣고 False.
        ctx(판단 자료)가 있으면 살 종목을 판단 모듈(app/decision.py)로 거르고 순서를 정하며, 약세장이면 절반만 삽니다.
        buy_price_of: 살 때만 쓰는 가격(예약 체결의 갭 상승 제한). 없으면 price_of.
        exclude: 이번에 사지 않을 종목(방금 판 종목: 새 날 추천으로 제외 목록이 초기화돼도 바로 다시 사지 않게)."""
        sell_price_of, price_of = price_of, buy_price_of or price_of
        if ctx is not None and not ctx.weak_market and self.reserve > 0:
            self.pending.append(self.reserve)  # 시장이 회복되면 남겨 둔 돈도 다시 투자
            self.reserve = 0.0
        themes = {symbol: row["theme"] for symbol, row in snapshot_info(snapshot).items()}
        regroup = False
        if snapshot and self.used_snapshot == snapshot["date"]:
            self.used_snapshot = snapshot_id(snapshot)  # 예전 장부는 날짜만 남겼음: 같은 기록이면 새 추천으로 보지 않음
        if snapshot and snapshot_id(snapshot) != self.used_snapshot:
            previous_day = (self.used_snapshot or "")[:10]
            self.used_snapshot = snapshot_id(snapshot)
            same_day = previous_day == snapshot["date"]
            if not same_day:
                self.banned = set()  # 판 종목을 다시 살 수 있는 건 다음 날 추천부터(장중 갱신마다 다시 사지 않게)
            if self.rotate:
                regroup = self.follow_count
                if same_day:
                    # 장중 갱신: 추천 후보에서 완전히 빠진 종목만 팝니다(순위가 조금 바뀐 것으로 사고팔지 않게).
                    keep_list = snapshot_candidates(snapshot, self.source)
                else:
                    keep_list = core_candidates(snapshot, self.source) if self.follow_count else snapshot_candidates(snapshot, self.source)[: self.slots]
                keep = {symbol for symbol, _ in keep_list}
                for symbol in [symbol for symbol in self.held if symbol not in keep]:
                    price = sell_price_of(symbol)
                    if price:
                        self.pending.append(self.sell(symbol, price, day, f"{snapshot['date']} 새 추천에서 빠짐", when))
        if self.pending and snapshot and regroup:
            # 새 추천 종목 중 안 가진 것을 모두, 판 돈을 똑같이 나눠 삽니다(종목 수는 추천이 고른 대로).
            targets = [(symbol, name) for symbol, name in core_candidates(snapshot, self.source)
                       if symbol not in self.held and symbol not in self.banned and symbol not in exclude]
            need = [symbol for symbol, _ in targets if not known(symbol)]
            if need:
                self.missing.update(need)
                return False
            chosen = self._order(targets, snapshot, ctx)
            if chosen:
                each = self._budget(sum(self.pending), ctx) / len(chosen)
                self.pending = [each for symbol, name, why in chosen
                                if not self.buy(symbol, name, each, price_of(symbol), day, " · ".join(filter(None, [f"{snapshot['date']} 추천", why])),
                                                when, theme=themes.get(symbol, ""))]
        if self.pending and snapshot:
            core = core_candidates(snapshot, self.source)
            # 추천 수를 따르면 추천이 고른 종목 안에서만 삽니다(없으면 현금으로 기다림). 수를 정했으면 테마 후보로도 채웁니다.
            ordered = core if self.follow_count else core + [item for item in snapshot_candidates(snapshot, self.source) if item not in core]
            options = [(symbol, name) for symbol, name in ordered
                       if symbol not in self.held and symbol not in self.banned and symbol not in exclude]
            need = [symbol for symbol, _ in options[: len(self.pending) * 2 + 2] if not known(symbol)]
            if need:
                self.missing.update(need)
                return False
            chosen = self._order([option for option in options if known(option[0])], snapshot, ctx)
            remaining = []
            for budget in self.pending:
                while chosen:
                    symbol, name, why = chosen.pop(0)
                    spend = self._budget(budget, ctx)
                    if self.buy(symbol, name, spend, price_of(symbol), day, " · ".join(filter(None, [f"{snapshot['date']} 추천", why])),
                                when, theme=themes.get(symbol, "")):
                        break
                    if ctx is not None and ctx.weak_market:
                        self.reserve -= budget - spend  # 못 샀으면 남겨 둔 몫을 되돌림
                else:
                    remaining.append(budget)  # 살 추천 종목이 모자라면(또는 판단 모듈이 모두 걸렀으면) 현금으로 두고 기다림
            self.pending = remaining
        return True

    def check_exits(self, price_of: Price, day: str, when: str | None = None, ctx: Context | None = None,
                    snapshot: dict[str, Any] | None = None) -> None:
        """매도 판단. ctx가 없으면 고점 대비 하락만, 있으면 판단 모듈(app/decision.exit_reason)의 기준 전체로 봅니다."""
        for symbol in list(self.held):
            price = price_of(symbol)
            if not price:
                continue
            item = self.held[symbol]
            item["last_price"] = price
            item["peak"] = max(item["peak"], price)
            if ctx is None:
                hit = item["peak"] >= item["buy_price"] * (1 + self.arm) and price / item["peak"] - 1 <= -self.trail
                reason = f"고점 대비 −{round(self.trail * 100)}%" if hit else None
            else:
                reason = exit_reason(item, price, snapshot, ctx, self.trail, self.arm)
            if reason:
                self.banned.add(symbol)
                self.pending.append(self.sell(symbol, price, day, reason, when))

    def value(self) -> float:
        return self.cash + sum(item["shares"] * item.get("last_price", item["buy_price"]) * (1 - self.cost / 2) for item in self.held.values())

    def mark(self, day: str) -> None:
        """그날 평가액(같은 날이면 덮어씀)."""
        point = {"date": day, "value": round(self.value(), 2)}
        if self.series and self.series[-1]["date"] == day:
            self.series[-1] = point
        else:
            self.series.append(point)


def history_context(prices: dict[str, list[dict[str, Any]]], index_candles: list[dict[str, Any]], day: str,
                    include_day: bool, reviews: dict[str, dict[str, dict[str, str]]] | None = None) -> Context:
    """일봉으로 다시 계산할 때 그 시점에 알 수 있던 자료로 만든 판단 자료(시가 판단은 전날까지, 종가 판단은 그날까지).
    분야 흐름은 지난 기록이 없어 쓰지 않고, AI 점검은 그날 남긴 것이 있으면 씁니다."""
    from .decision import metrics
    from .simulation import market_is_weak

    def upto(rows: list[dict[str, Any]]) -> list[float]:
        return [row["close"] for row in rows if (row["date"] <= day if include_day else row["date"] < day)]

    weak = market_is_weak(upto(index_candles))
    return Context(weak_market=bool(weak), late=include_day, review=(reviews or {}).get(day, {}),
                   tech=lambda symbol: metrics(upto(prices.get(symbol, []))))


def replay_book(portfolio: dict[str, Any], prices: dict[str, list[dict[str, Any]]], index_candles: list[dict[str, Any]],
                snapshots: list[dict[str, Any]], end: str, cost: float, trail: float, arm: float, rotate: bool = False,
                decide: bool = False, reviews: dict[str, dict[str, dict[str, str]]] | None = None) -> Book | dict[str, Any]:
    """만든 날 다음 거래일부터 end까지 일봉으로 다시 계산(시가에 사고, 종가에 매도 판단).
    decide=True면 판단 모듈(app/decision.py)로 살 종목을 거르고 매도 기준 전체를 봅니다(시장 약세·추천 근거·위험 지정 등).
    prices에 없는 종목을 사야 하면 {"missing": [...]}를 돌려줍니다(받아서 다시 부르면 됨)."""
    created, market = portfolio["created_date"], portfolio["market"]
    book = Book(portfolio, cost, trail, arm, rotate)
    bars = {symbol: {row["date"]: row for row in rows} for symbol, rows in prices.items()}
    calendar = sorted({row["date"] for row in index_candles if created < row["date"] <= end}
                      or {row["date"] for pick in book.picks for row in prices.get(pick["symbol"], []) if created < row["date"] <= end})
    ready_all = usable_snapshots(snapshots, book.source)
    for day in calendar:
        ready = [snapshot for snapshot in ready_all if available(snapshot, day, market)]
        snapshot = ready[-1] if ready else None

        def opening(symbol: str, day: str = day) -> float | None:
            bar = bars.get(symbol, {}).get(day)
            return (bar.get("open") or bar["close"]) if bar else None

        def closing(symbol: str, day: str = day) -> float | None:
            bar = bars.get(symbol, {}).get(day)
            return bar["close"] if bar else None

        open_ctx = history_context(prices, index_candles, day, False, reviews) if decide else None
        close_ctx = history_context(prices, index_candles, day, True, reviews) if decide else None
        if not book.started:
            book.start(opening, day, snapshot)
        if not book.rebalance(snapshot, opening, day, known=lambda symbol: symbol in prices, ctx=open_ctx):
            return {"missing": sorted(book.missing)}
        book.check_exits(closing, day, ctx=close_ctx, snapshot=snapshot)
        book.mark(day)
    return book


def live_tick(state: dict[str, Any], portfolio: dict[str, Any], quotes: dict[str, float], snapshots: list[dict[str, Any]],
              day: str, now_text: str, cost: float, trail: float, arm: float, rotate: bool,
              ctx: Context | None = None) -> dict[str, Any]:
    """장중 한 번 확인: quotes(오늘 현재가)로 새 추천 반영·빈 자리 채우기 → 매도 판단 → 오늘 평가액 갱신.
    판 자리는 다음 확인 때 채웁니다. now_text = 'YYYY-MM-DD HH:MM'(한국 시간), 그 전에 만든 추천만 씁니다.
    ctx(시장 약세·분야 흐름·AI 점검·종목 흐름)가 있으면 판단 모듈로 사고팝니다."""
    book = Book(portfolio, cost, trail, arm, rotate, state)
    ready = [snapshot for snapshot in usable_snapshots(snapshots, book.source)
             if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
    snapshot = ready[-1] if ready else None
    price_of = quotes.get
    if not book.started:
        book.start(price_of, day, snapshot, now_text)
    book.rebalance(snapshot, price_of, day, known=lambda symbol: True, when=now_text, ctx=ctx)
    book.check_exits(price_of, day, now_text, ctx=ctx, snapshot=snapshot)
    book.mark(day)
    return book.state()


GAP_LIMIT = 0.03  # 예약 매수: 시가가 전날 종가보다 이만큼 넘게 높게 시작하면(갭 상승) 사지 않고 그날은 쫓아 사지 않음


def plan_orders(state: dict[str, Any], portfolio: dict[str, Any], closes: dict[str, float], snapshots: list[dict[str, Any]],
                for_day: str, now_text: str, cost: float, trail: float, arm: float, rotate: bool, ctx: Context) -> dict[str, Any]:
    """장 마감 뒤 예약 주문: 마지막 종가와 그때까지의 추천·AI 점검으로 다음 장 시가에 팔고 살 것을 미리 정합니다(장부는 바꾸지 않음)."""
    ready = [snapshot for snapshot in usable_snapshots(snapshots, portfolio.get("source", "all"))
             if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
    snapshot = ready[-1] if ready else None
    book = Book(portfolio, cost, trail, arm, rotate, state)
    start = len(book.trades)
    book.check_exits(closes.get, for_day, ctx=ctx, snapshot=snapshot)
    sold = {trade["symbol"] for trade in book.trades[start:] if trade["action"] == "sell"}
    book.rebalance(snapshot, closes.get, for_day, known=lambda symbol: symbol in closes, ctx=ctx, exclude=sold)
    orders = []
    for trade in book.trades[start:]:
        order = {"action": trade["action"], "symbol": trade["symbol"], "name": trade["name"], "shares": trade["shares"],
                 "ref_price": trade["price"], "reason": trade["reason"]}
        if trade["action"] == "buy":
            order["limit"] = round(trade["price"] * (1 + GAP_LIMIT), 4)
        orders.append(order)
    return {"for_day": for_day, "based_on": snapshot_id(snapshot) if snapshot else None, "created_at": now_text,
            "orders": orders, "executed": False}


def execute_plan(state: dict[str, Any], portfolio: dict[str, Any], opens: dict[str, dict[str, float]], snapshots: list[dict[str, Any]],
                 day: str, now_text: str, cost: float, trail: float, arm: float, rotate: bool, ctx: Context) -> dict[str, Any]:
    """다음 장 첫 확인 때 예약 주문을 그날 시가로 체결합니다. opens = {종목: {"open", "prev_close"}}.
    예약 매도는 시가에 팔고, 매수는 판단 모듈로 다시 고르되 시가가 전날 종가보다 +3% 넘게 높으면 사지 않고 그날은 쫓아 사지 않습니다."""
    plan = dict(state.get("plan") or {})
    book = Book(portfolio, cost, trail, arm, rotate, state)
    start = len(book.trades)
    for order in plan.get("orders", []):
        bar = opens.get(order["symbol"])
        if order["action"] == "sell" and order["symbol"] in book.held and bar and bar.get("open"):
            book.banned.add(order["symbol"])
            book.pending.append(book.sell(order["symbol"], bar["open"], day, order["reason"], now_text))
    gapped: set[str] = set()

    def opening(symbol: str) -> float | None:
        bar = opens.get(symbol)
        return bar.get("open") if bar else None

    def opening_buy(symbol: str) -> float | None:
        bar = opens.get(symbol)
        if not bar or not bar.get("open"):
            return None
        if bar.get("prev_close") and bar["open"] > bar["prev_close"] * (1 + GAP_LIMIT):
            gapped.add(symbol)
            return None
        return bar["open"]

    ready = [snapshot for snapshot in usable_snapshots(snapshots, book.source)
             if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
    sold = {trade["symbol"] for trade in book.trades[start:] if trade["action"] == "sell"}
    book.rebalance(ready[-1] if ready else None, opening, day, known=lambda symbol: True, when=now_text, ctx=ctx,
                   buy_price_of=opening_buy, exclude=sold)
    book.banned |= {symbol for symbol in gapped if symbol not in book.held} | {symbol for symbol in sold if symbol not in book.held}
    for trade in book.trades[start:]:
        trade["reason"] = f"예약({'시가 매도' if trade['action'] == 'sell' else '시가 매수'}) · {trade['reason']}"
        if trade["action"] == "buy" and trade["symbol"] in book.held:
            book.held[trade["symbol"]]["buy_reason"] = trade["reason"]
    plan.update(executed=True, executed_at=now_text,
                skipped=[{"symbol": symbol, "name": next((order["name"] for order in plan.get("orders", []) if order["symbol"] == symbol), symbol),
                          "reason": f"갭 상승(시가가 전날 종가보다 +{round(GAP_LIMIT * 100)}% 넘게 높음)"} for symbol in sorted(gapped)])
    return {**book.state(), "plan": plan}


def next_session_day(market: str, local_now: Any, opens_at: Any) -> str:
    """다음 정규장 날짜(그 시장 기준). 오늘 장 시작 전이면 오늘. 공휴일은 따지지 않습니다."""
    from datetime import timedelta

    day = local_now.date()
    if not (day.weekday() < 5 and local_now.time() < opens_at):
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def needed_symbols(state: dict[str, Any], portfolio: dict[str, Any], snapshots: list[dict[str, Any]], now_text: str) -> list[str]:
    """장중 확인에 필요한 종목: 보유 종목 + (처음이면) 처음 종목 + 가장 최근 추천 후보."""
    source = portfolio.get("source", "all")
    symbols = list((state.get("held") or {}).keys())
    if not state.get("started"):
        symbols += [pick["symbol"] for pick in portfolio["picks"]]
    ready = [snapshot for snapshot in usable_snapshots(snapshots, source)
             if (snapshot.get("generated_at") or f"{snapshot['date']} 23:59") <= now_text]
    if ready:
        symbols += [symbol for symbol, _ in snapshot_candidates(ready[-1], source)]
    return list(dict.fromkeys(symbols))


def book_view(state: dict[str, Any], cost: float, prices: dict[str, list[dict[str, Any]]], end: str) -> dict[str, Any]:
    """장부를 화면용으로: 보유 종목은 end까지의 최신 가격(장중이면 현재가)으로 평가합니다."""
    holdings, total = [], float(state["cash"])
    for symbol, item in (state.get("held") or {}).items():
        rows = [row for row in prices.get(symbol, []) if row["date"] <= end]
        last = rows[-1]["close"] if rows else item.get("last_price", item["buy_price"])
        last_date = rows[-1]["date"] if rows else item["buy_date"]
        value = item["shares"] * last * (1 - cost / 2)
        total += value
        holdings.append({**item, "status": "bought", "last_price": last, "last_date": last_date, "value": round(value, 2),
                         "cost": round(item["cost"], 2), "profit": round(value - item["cost"], 2), "return": round(value / item["cost"] - 1, 4)})
    series = list(state.get("series") or [])
    days = sorted({row["date"] for symbol in (state.get("held") or {}) for row in prices.get(symbol, []) if row["date"] <= end})
    if days and holdings:
        point = {"date": days[-1], "value": round(total, 2)}
        series = [item for item in series if item["date"] < point["date"]] + [point]
    return {"holdings": holdings, "closed": list(reversed(state.get("closed") or [])), "trades": state.get("trades") or [],
            "series": series, "cash": round(state["cash"], 2), "pending_cash": round(sum(state.get("pending") or []), 2),
            "reserve_cash": round(state.get("reserve") or 0, 2), "used_snapshot": state.get("used_snapshot"),
            "plan": state.get("plan")}


def simulate_managed(portfolio: dict[str, Any], prices: dict[str, list[dict[str, Any]]], index_candles: list[dict[str, Any]],
                     snapshots: list[dict[str, Any]], end: str, cost: float, trail: float, arm: float,
                     rotate: bool = False, decide: bool = False) -> dict[str, Any]:
    """일봉으로 다시 계산한 결과(화면용). prices에 없는 종목을 사야 하면 {"missing": [...]}."""
    book = replay_book(portfolio, prices, index_candles, snapshots, end, cost, trail, arm, rotate, decide)
    if isinstance(book, dict):
        return book
    return {**book_view(book.state(), cost, prices, end), "state": book.state()}

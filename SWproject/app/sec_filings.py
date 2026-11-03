"""미국 SEC EDGAR 공시 조회.

"Form 8K MERCER INTERNATIONAL INC. For: 3 November"처럼 공시를 알리는 기사는 매체(Investing.com 등)가 막혀도
원문인 SEC 공시를 직접 읽으면 내용을 알 수 있습니다. EDGAR는 무료·무인증이지만, 요청에 연락처가 담긴
User-Agent를 붙이고 초당 10건을 넘기지 않도록 요구합니다(SEC_USER_AGENT 설정).
"""
import logging
import re
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .config import get_settings
from .redis_client import cached_json

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"
COMPANY_TTL = 24 * 60 * 60
SUBMISSIONS_TTL = 30 * 60
MAX_TEXT_CHARS = 12000
MATCH_WINDOW_DAYS = (-4, 2)  # 기사 날짜 기준 4일 전 ~ 2일 뒤에 낸 공시를 찾습니다(시차·주말 감안).

FILING_TITLE = re.compile(
    r"\bForm\s*(8-?K|6-?K|10-?Q|10-?K|20-?F|S-1|424B\d|SC\s*13[DG]|4)\b[\s:/-]*(.+?)"
    r"(?:\s+(?:For|Date|Dated|Filed|Period)\b.*)?$",
    re.IGNORECASE,
)
FORM_NAMES = {
    "8-K": "수시 공시", "6-K": "외국 기업 수시 공시", "10-Q": "분기 보고서", "10-K": "연간 보고서",
    "20-F": "외국 기업 연간 보고서", "S-1": "증권 신고서", "4": "내부자 주식 거래 보고", "SC 13D": "5% 이상 지분 보유 보고",
    "SC 13G": "5% 이상 지분 보유 보고",
}
TEXT_FORMS = {"8-K", "6-K"}  # 본문이 짧아 요약할 만한 공시. 정기 보고서는 수백 쪽이라 항목만 알립니다.
ITEM_LABELS = {
    "1.01": "중요 계약 체결", "1.02": "중요 계약 해지", "1.03": "파산·회생 절차", "1.04": "광산 안전 위반",
    "1.05": "사이버보안 사고", "2.01": "자산 인수·처분 완료", "2.02": "실적 발표", "2.03": "채무 발생",
    "2.04": "채무 조기 상환 사유 발생", "2.05": "구조조정 비용", "2.06": "자산 손상", "3.01": "상장 유지 요건 미달·상장폐지 통지",
    "3.02": "비공개 주식 발행", "3.03": "주주 권리 변경", "4.01": "회계감사인 변경", "4.02": "과거 재무제표 신뢰 불가",
    "5.01": "경영권 변동", "5.02": "임원·이사 변동", "5.03": "정관·회계연도 변경", "5.07": "주주총회 표결 결과",
    "5.08": "이사 후보 추천 일정", "7.01": "투자자 대상 자료 공개(공정공시)", "8.01": "기타 중요 사항", "9.01": "재무제표·첨부 자료",
}
# 성격이 분명한 항목만 논조를 정합니다. 나머지는 본문(AI)으로 판단합니다.
ITEM_TONES = {
    "1.03": "강한 악재", "3.01": "강한 악재", "4.02": "강한 악재",
    "1.02": "악재", "1.05": "악재", "2.04": "악재", "2.05": "악재", "2.06": "악재",
}
NAME_SUFFIX = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|SA|NV|AG|HOLDINGS?)\b")


def _headers() -> dict[str, str]:
    return {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def normalize_form(form: str) -> str:
    compact = re.sub(r"[\s-]", "", form.upper())
    if compact.startswith("SC13"):
        return f"SC {compact[2:]}"
    if compact == "S1":
        return "S-1"
    match = re.fullmatch(r"(\d+)([A-Z])", compact)
    return f"{match.group(1)}-{match.group(2)}" if match else compact


def normalize_company(name: str) -> str:
    text = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND "))
    return " ".join(NAME_SUFFIX.sub(" ", text).split())


def parse_filing_title(title: str) -> tuple[str, str] | None:
    """공시 알림 기사 제목에서 (공시 종류, 회사명)을 꺼냅니다. 공시 알림이 아니면 None."""
    match = FILING_TITLE.search(title.strip())
    if not match:
        return None
    company = match.group(2).strip(" .,-:")
    return (normalize_form(match.group(1)), company) if company else None


async def company_index() -> dict[str, dict[str, int]]:
    async def load() -> dict[str, dict[str, int]]:
        async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
            response = await client.get(COMPANY_TICKERS_URL)
            response.raise_for_status()
        by_ticker, by_name = {}, {}
        for row in response.json().values():
            by_ticker[row["ticker"].upper()] = row["cik_str"]
            by_name.setdefault(normalize_company(row["title"]), row["cik_str"])
        return {"by_ticker": by_ticker, "by_name": by_name}

    return await cached_json("sec:company-tickers:v1", COMPANY_TTL, load)


async def _submissions(cik: int) -> dict[str, Any]:
    async def load() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
            response = await client.get(SUBMISSIONS_URL.format(cik=cik))
            response.raise_for_status()
        recent = response.json()["filings"]["recent"]
        keys = ("form", "filingDate", "accessionNumber", "primaryDocument", "items")
        return {key: recent.get(key, [])[:200] for key in keys}

    return await cached_json(f"sec:submissions:v1:{cik}", SUBMISSIONS_TTL, load)


def pick_filing(recent: dict[str, list], form: str, around: date) -> dict[str, Any] | None:
    """기사 날짜에 가장 가까운 같은 종류 공시를 고릅니다(정정 공시 8-K/A 포함)."""
    best, best_gap = None, None
    for index, filed_form in enumerate(recent["form"]):
        if filed_form != form and not filed_form.startswith(f"{form}/"):
            continue
        filed = date.fromisoformat(recent["filingDate"][index])
        gap = (filed - around).days
        if not MATCH_WINDOW_DAYS[0] <= gap <= MATCH_WINDOW_DAYS[1]:
            continue
        if best_gap is None or abs(gap) < abs(best_gap):
            best_gap = gap
            best = {
                "form": filed_form,
                "date": recent["filingDate"][index],
                "accession": recent["accessionNumber"][index],
                "primary": recent["primaryDocument"][index],
                "items": [item for item in (recent["items"][index] or "").split(",") if item],
            }
    return best


def _html_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _filing_body(text: str) -> str:
    # 8-K 앞부분의 표지(주소·체크박스)와 끝의 서명란을 떼고 항목(Item x.xx)부터 씁니다.
    start = re.search(r"Item\s+\d\.\d\d", text)
    end = re.search(r"\bSIGNATURES?\b", text[start.start() if start else 0:])
    body = text[start.start() if start else 0:]
    return body[:end.start()] if end else body


async def filing_text(cik: int, filing: dict[str, Any]) -> str:
    """공시 본문과 첨부 보도자료(Exhibit 99)를 이어 붙인 글."""
    accession = filing["accession"].replace("-", "")
    async with httpx.AsyncClient(timeout=20.0, headers=_headers()) as client:
        primary = await client.get(ARCHIVE_URL.format(cik=cik, accession=accession, name=filing["primary"]))
        parts = [_filing_body(_html_text(primary.content))] if primary.status_code == 200 else []
        index = await client.get(ARCHIVE_URL.format(cik=cik, accession=accession, name="index.json"))
        if index.status_code == 200:
            names = [item["name"] for item in index.json().get("directory", {}).get("item", [])]
            exhibits = [name for name in names if re.search(r"ex-?99", name, re.IGNORECASE) and name.endswith((".htm", ".html", ".txt"))]
            for name in exhibits[:2]:
                response = await client.get(ARCHIVE_URL.format(cik=cik, accession=accession, name=name))
                if response.status_code == 200:
                    parts.append(_html_text(response.content))
    return "\n\n".join(part for part in parts if part)[:MAX_TEXT_CHARS]


def describe_filing(filing: dict[str, Any]) -> str:
    base = filing["form"].split("/")[0]
    labels = [ITEM_LABELS.get(item, f"항목 {item}") for item in filing["items"] if item != "9.01"]
    name = FORM_NAMES.get(base, "공시")
    return f"SEC {filing['form']} {name}({filing['date']})" + (f": {' · '.join(labels)}" if labels else "")


def filing_tone(filing: dict[str, Any]) -> str | None:
    tones = [ITEM_TONES[item] for item in filing["items"] if item in ITEM_TONES]
    return "강한 악재" if "강한 악재" in tones else tones[0] if tones else None


async def lookup_filing(title: str, published: date, symbol: str = "") -> dict[str, Any] | None:
    """공시 알림 기사 제목으로 실제 SEC 공시를 찾아 설명·본문·논조를 돌려줍니다. 찾지 못하면 None."""
    parsed = parse_filing_title(title)
    if not parsed:
        return None
    form, company = parsed
    try:
        index = await company_index()
        cik = index["by_name"].get(normalize_company(company)) or index["by_ticker"].get(symbol.upper())
        if not cik:
            return None
        filing = pick_filing(await _submissions(cik), form, published)
        if not filing:
            return None
        text = await filing_text(cik, filing) if form in TEXT_FORMS else ""
    except (httpx.HTTPError, KeyError, ValueError):
        logger.exception("SEC filing lookup failed: %s", title)
        return None
    accession = filing["accession"].replace("-", "")
    return {
        **filing,
        "cik": cik,
        "description": describe_filing(filing),
        "tone": filing_tone(filing),
        "text": text,
        "url": ARCHIVE_URL.format(cik=cik, accession=accession, name=filing["primary"]),
    }

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings

PUBLIC_SECTOR_LABELS = {
    "Technology": "정보기술",
    "Industrials": "산업재",
    "Healthcare": "헬스케어",
    "Financial Services": "금융",
    "Communication Services": "커뮤니케이션",
    "Consumer Cyclical": "경기소비재",
    "Consumer Defensive": "필수소비재",
    "Utilities": "유틸리티",
    "Basic Materials": "소재",
    "Real Estate": "부동산",
    "Energy": "에너지",
}
US_EXCHANGES = {"AMEX", "NASDAQ", "NYSE", "US_ETC"}
KR_EXCHANGES = {"KOSPI", "KOSDAQ", "KR_ETC"}


def load_sector_map() -> dict[str, Any]:
    path = Path(get_settings().sector_map_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    us_map_path = Path.cwd() / "us_sector_map.json"
    path_mtime = path.stat().st_mtime_ns if path.exists() else 0
    us_map_mtime = us_map_path.stat().st_mtime_ns if us_map_path.exists() else 0
    return _load_sector_map_cached(str(path), path_mtime, str(us_map_path), us_map_mtime)


@lru_cache(maxsize=8)
def _load_sector_map_cached(
    path: str,
    path_mtime: int,
    us_map_path: str,
    us_map_mtime: int,
) -> dict[str, Any]:
    sector_map: dict[str, Any] = {}
    for map_path in (Path(path), Path(us_map_path)):
        if not map_path.exists():
            continue
        try:
            data = json.loads(map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if map_path.name == "us_sector_map.json":
            for sector, symbols in data.get("sectors", {}).items():
                if isinstance(symbols, list):
                    for symbol in symbols:
                        sector_map.setdefault(str(symbol), str(sector))
        else:
            sector_map.update({str(symbol): sector for symbol, sector in data.items() if sector})

    return sector_map


def sector_weights(row: dict[str, Any]) -> dict[str, float]:
    mapping = load_sector_map().get(str(row.get("symbol", "")))
    if mapping is None:
        if str(row.get("sector", "")).upper() in {"ETF", "ETN"}:
            return {"ETF/ETN": 1.0}
        public_sector = PUBLIC_SECTOR_LABELS.get(str(row.get("externalSector", "")))
        if public_sector:
            return {public_sector: 1.0}
        if (
            row.get("marketCountry") in {"KR", "US"}
            or row.get("market") in US_EXCHANGES | KR_EXCHANGES
        ):
            return {"분야 확인 필요": 1.0}
    if isinstance(mapping, dict):
        if not get_settings().weighted_analysis_enabled:
            primary = mapping.get("primary") or next(iter(mapping.get("weights", {})), "미분류")
            return {str(primary): 1.0}
        weights = mapping.get("weights", {})
    elif mapping:
        weights = {str(mapping): 1.0}
    else:
        weights = {"미분류": 1.0}
    numeric = {str(sector): float(weight) for sector, weight in weights.items() if float(weight) > 0}
    total = sum(numeric.values()) or 1.0
    return {sector: weight / total for sector, weight in numeric.items()}


def sector_for(row: dict[str, Any]) -> str:
    return max(sector_weights(row).items(), key=lambda item: item[1])[0]
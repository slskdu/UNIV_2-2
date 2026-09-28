import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings


@lru_cache
def load_sector_map() -> dict[str, Any]:
    path = Path(get_settings().sector_map_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(symbol): sector for symbol, sector in data.items() if sector}


def sector_weights(row: dict[str, Any]) -> dict[str, float]:
    mapping = load_sector_map().get(str(row.get("symbol", "")))
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
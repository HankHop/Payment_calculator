"""Load tax tables from the JSON files in paycalc/data."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ConfigError

_DIR = Path(__file__).parent / "data"


def data_year(prefix: str, year: int) -> int:
    """The year whose tables are used for `year`: the same year if present, otherwise the latest
    earlier year (later years' figures are not published yet, so earlier ones stand in)."""
    if (_DIR / f"{prefix}_{year}.json").exists():
        return year
    earlier = sorted(int(p.stem.rsplit("_", 1)[1]) for p in _DIR.glob(f"{prefix}_*.json")
                     if p.stem.rsplit("_", 1)[1].isdigit() and int(p.stem.rsplit("_", 1)[1]) < year)
    if not earlier:
        raise ConfigError(f"No {prefix} tax data for {year}: expected {prefix}_{year}.json in paycalc/data")
    return earlier[-1]


def _load(prefix: str, year: int) -> dict:
    path = _DIR / f"{prefix}_{data_year(prefix, year)}.json"
    return json.loads(path.read_text())


def federal(year: int) -> dict:
    return _load("federal", year)


def state(year: int, code: str) -> dict:
    states = _load("states", year)["states"]
    if code not in states:
        raise ConfigError(f"state: unknown code {code!r}; known: {', '.join(sorted(states))}")
    return states[code]

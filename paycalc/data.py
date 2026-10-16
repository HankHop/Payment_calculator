"""Load tax tables from the JSON files in paycalc/data."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ConfigError

_DIR = Path(__file__).parent / "data"


def _load(prefix: str, year: int) -> dict:
    path = _DIR / f"{prefix}_{year}.json"
    if not path.exists():
        raise ConfigError(f"No {prefix} tax data for {year}: expected {path.name} in paycalc/data")
    return json.loads(path.read_text())


def federal(year: int) -> dict:
    return _load("federal", year)


def state(year: int, code: str) -> dict:
    states = _load("states", year)["states"]
    if code not in states:
        raise ConfigError(f"state: unknown code {code!r}; known: {', '.join(sorted(states))}")
    return states[code]

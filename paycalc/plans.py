"""Saved plans: the main config plus extra named plans (e.g. next year) kept beside it.

Plan files live in a `plans` folder next to the main config and end in .local.json, so git ignores
them. Names are restricted to letters, digits, '-' and '_' so a name can never point outside it.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, timedelta
from pathlib import Path

from .config import ConfigError, parse
from .schedule import salary_on

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAIN = "main"


def plans_dir(main_path: Path) -> Path:
    return main_path.parent / "plans"


def valid_name(name) -> bool:
    return isinstance(name, str) and name != MAIN and bool(NAME_RE.match(name))


def plan_path(main_path: Path, name: str) -> Path:
    """Where a plan lives. Raises ConfigError for any name that is not 'main' or a safe name."""
    if name == MAIN:
        return main_path
    if not valid_name(name):
        raise ConfigError("plan: use letters, digits, '-' or '_' (up to 40 characters)")
    return plans_dir(main_path) / f"{name}.local.json"


def _year_of(path: Path):
    try:
        return json.loads(path.read_text()).get("tax_year", 2026)
    except (OSError, ValueError, AttributeError):
        return None


def list_plans(main_path: Path) -> list:
    out = [{"name": MAIN, "tax_year": _year_of(main_path) if main_path.exists() else None,
            "saved": main_path.exists()}]
    d = plans_dir(main_path)
    if d.is_dir():
        for p in sorted(d.glob("*.local.json")):
            name = p.name[: -len(".local.json")]
            if valid_name(name):
                out.append({"name": name, "tax_year": _year_of(p), "saved": True})
    return out


def _shift_year(iso: str, years: int) -> str:
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28).isoformat()


def rollover(raw: dict, to_year: int | None = None) -> dict:
    """A starting point for a later year, built from this year's config (which is not modified).

    Carries forward: the salary in force on Dec 31 (the raise is folded in), the last election, the
    last W-4 setting, deductions, employer terms and bonuses (same amounts, dates moved a year).
    Drops: raise, as-of date and what-if. Edit the result for what actually changes."""
    cfg = parse(raw)  # refuses to roll over something that does not calculate
    year = cfg.tax_year
    to = to_year or year + 1
    n = to - year
    if n < 1:
        raise ConfigError("plan year must be after this plan's tax_year")
    new = copy.deepcopy(raw)
    new["tax_year"] = to
    new.pop("as_of_date", None)
    new.pop("what_if", None)
    if new.get("age_end_of_year") is not None:
        new["age_end_of_year"] = new["age_end_of_year"] + n

    pay = new.setdefault("pay", {})
    pay["base_salary"] = round(salary_on(cfg, date(year, 12, 31)), 2)
    pay.pop("raise", None)
    if pay.get("first_pay_date"):
        fpd = date.fromisoformat(pay["first_pay_date"])
        step = {"biweekly": 14, "weekly": 7}.get(pay.get("frequency"))
        if step:  # keep the cadence: move by whole pay periods closest to the new year
            moved = fpd + timedelta(days=step * round(365.25 * n / step))
            pay["first_pay_date"] = moved.isoformat()
        else:  # semimonthly / monthly dates repeat on the same calendar days
            pay["first_pay_date"] = _shift_year(pay["first_pay_date"], n)
    bonuses = ([pay.pop("bonus")] if pay.get("bonus") else []) + list(pay.get("bonuses") or [])
    pay.pop("bonus", None)
    for b in bonuses:
        b["pay_date"] = _shift_year(b["pay_date"], n)
    if bonuses:
        pay["bonuses"] = bonuses

    c = new.setdefault("contributions", {})
    raw_el = c.pop("elections", None) or [{"pretax_percent": c.get("pretax_percent", 0),
                                           "posttax_percent": c.get("posttax_percent", 0)}]
    c.pop("pretax_percent", None)
    c.pop("posttax_percent", None)
    last = sorted(raw_el, key=lambda e: e.get("effective_date", ""))[-1]
    c["elections"] = [{"effective_date": f"{to}-01-01", "pretax_percent": last.get("pretax_percent", 0),
                       "posttax_percent": last.get("posttax_percent", 0)}]

    w = new.get("withholding") or {}
    changes = sorted(w.get("changes") or [], key=lambda x: x.get("effective_date", ""))
    if changes:
        last_w = {k: v for k, v in changes[-1].items() if k != "effective_date"}
        w["changes"] = [{"effective_date": f"{to}-01-01", **last_w}]
        new["withholding"] = w
    parse(new)  # the result must calculate too
    return new


def next_name(main_path: Path, to_year: int) -> str:
    base = str(to_year)
    name, i = base, 1
    while plan_path(main_path, name).exists():
        i += 1
        name = f"{base}-{i}"
    return name

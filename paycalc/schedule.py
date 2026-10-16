"""Pay dates and per-event gross pay for the tax year."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List

from .config import FREQUENCIES, Config


@dataclass(frozen=True)
class PayEvent:
    pay_date: date
    kind: str                 # "regular" or "bonus"
    gross: float
    deferral_comp: float      # pay the employee percentage applies to
    match_comp: float         # pay the employer match is calculated on
    nonelective_comp: float   # pay the unmatched employer contribution is calculated on


def pay_dates(year: int, frequency: str, first_pay_date: date | None) -> List[date]:
    jan1, dec31 = date(year, 1, 1), date(year, 12, 31)
    if frequency in ("weekly", "biweekly"):
        step = timedelta(days=7 if frequency == "weekly" else 14)
        d = first_pay_date
        while d - step >= jan1:
            d -= step
        while d < jan1:
            d += step
        out = []
        while d <= dec31:
            out.append(d)
            d += step
        return out
    if frequency == "semimonthly":
        out = []
        for m in range(1, 13):
            out.append(date(year, m, 15))
            out.append(date(year, m, calendar.monthrange(year, m)[1]))
        return out
    return [date(year, m, calendar.monthrange(year, m)[1]) for m in range(1, 13)]


def salary_on(cfg: Config, d: date) -> float:
    if cfg.raise_ and d >= cfg.raise_.effective_date:
        return cfg.raise_.new_salary
    return cfg.base_salary


def build_events(cfg: Config) -> List[PayEvent]:
    """Regular paychecks (salary / nominal periods) plus the bonus as its own event."""
    periods = FREQUENCIES[cfg.frequency]
    emp = cfg.contributions.employer
    events = []
    for d in pay_dates(cfg.tax_year, cfg.frequency, cfg.first_pay_date):
        gross = salary_on(cfg, d) / periods
        events.append(PayEvent(d, "regular", gross, gross, gross, gross))
    if cfg.bonus:
        b = cfg.bonus
        gross = b.amount if b.amount is not None else salary_on(cfg, b.pay_date) * b.percent_of_salary / 100
        events.append(PayEvent(
            b.pay_date, "bonus", gross,
            gross if cfg.contributions.bonus_eligible_for_employee_contributions else 0.0,
            gross if emp.bonus_eligible_for_match else 0.0,
            gross if emp.bonus_eligible_for_nonelective else 0.0,
        ))
    events.sort(key=lambda e: (e.pay_date, e.kind != "regular"))
    return events

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
    kind: str                 # "regular", "regular+bonus" (bonus on a regular payday) or "bonus" (off-cycle)
    gross: float
    deferral_comp: float      # pay the employee percentage applies to
    match_comp: float         # pay the employer match is calculated on
    nonelective_comp: float   # pay the unmatched employer contribution is calculated on
    hsa: float = 0.0
    other_pretax: float = 0.0
    other_posttax: float = 0.0
    bonus_amount: float = 0.0     # part of gross that is bonus


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
    """Regular paychecks (salary / nominal periods). A bonus paid on a regular payday is added to
    that paycheck, so one paycheck's take-home covers everything deducted from it; otherwise it
    is an off-cycle paycheck (also when pay.bonus.separate_paycheck is true)."""
    periods = FREQUENCIES[cfg.frequency]
    emp, dd, ct = cfg.contributions.employer, cfg.deductions, cfg.contributions
    bonus_gross = 0.0
    if cfg.bonus:
        b = cfg.bonus
        bonus_gross = (b.amount if b.amount is not None
                       else salary_on(cfg, b.pay_date) * b.percent_of_salary / 100)
    dates = pay_dates(cfg.tax_year, cfg.frequency, cfg.first_pay_date)
    merge = bool(cfg.bonus and not cfg.bonus.separate_paycheck and cfg.bonus.pay_date in dates)

    def eligible(flag: bool, amount: float) -> float:
        return amount if flag else 0.0

    events = []
    for d in dates:
        base = salary_on(cfg, d) / periods
        extra = bonus_gross if merge and d == cfg.bonus.pay_date else 0.0
        events.append(PayEvent(
            d, "regular+bonus" if extra else "regular", base + extra,
            base + eligible(ct.bonus_eligible_for_employee_contributions, extra),
            base + eligible(emp.bonus_eligible_for_match, extra),
            base + eligible(emp.bonus_eligible_for_nonelective, extra),
            dd.hsa_per_paycheck, dd.other_pretax_per_paycheck, dd.other_posttax_per_paycheck, extra))
    if cfg.bonus and not merge:
        events.append(PayEvent(
            cfg.bonus.pay_date, "bonus", bonus_gross,
            eligible(ct.bonus_eligible_for_employee_contributions, bonus_gross),
            eligible(emp.bonus_eligible_for_match, bonus_gross),
            eligible(emp.bonus_eligible_for_nonelective, bonus_gross), 0.0, 0.0, 0.0, bonus_gross))
    events.sort(key=lambda e: (e.pay_date, e.kind == "bonus"))
    return events

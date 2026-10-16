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
    bonus_label: str = ""         # names of the bonuses in this paycheck


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


@dataclass(frozen=True)
class ResolvedBonus:
    name: str
    pay_date: date
    gross: float
    employee: bool       # employee percentage applies to it
    match: bool
    nonelective: bool
    with_regular: bool   # added to the regular paycheck on its pay date


def resolved_bonuses(cfg: Config) -> List[ResolvedBonus]:
    ct, emp = cfg.contributions, cfg.contributions.employer
    dates = set(pay_dates(cfg.tax_year, cfg.frequency, cfg.first_pay_date))

    def pick(own, default):
        return default if own is None else own

    out = []
    for i, b in enumerate(cfg.bonuses, 1):
        gross = b.amount if b.amount is not None else salary_on(cfg, b.pay_date) * b.percent_of_salary / 100
        out.append(ResolvedBonus(
            b.name or f"bonus {i}", b.pay_date, gross,
            pick(b.eligible_for_employee_contributions, ct.bonus_eligible_for_employee_contributions),
            pick(b.eligible_for_match, emp.bonus_eligible_for_match),
            pick(b.eligible_for_nonelective, emp.bonus_eligible_for_nonelective),
            not b.separate_paycheck and b.pay_date in dates))
    return out


def build_events(cfg: Config) -> List[PayEvent]:
    """Regular paychecks (salary / nominal periods). A bonus paid on a regular payday is added to
    that paycheck, so one paycheck's take-home covers everything deducted from it; otherwise (or
    with separate_paycheck) it is an off-cycle paycheck. Each bonus has its own eligibility for
    employee contributions, match and unmatched employer contributions."""
    periods = FREQUENCIES[cfg.frequency]
    dd = cfg.deductions
    bonuses = resolved_bonuses(cfg)

    def totals(items, base):
        gross = base + sum(b.gross for b in items)
        return (gross,
                base + sum(b.gross for b in items if b.employee),
                base + sum(b.gross for b in items if b.match),
                base + sum(b.gross for b in items if b.nonelective))

    events = []
    for d in pay_dates(cfg.tax_year, cfg.frequency, cfg.first_pay_date):
        base = salary_on(cfg, d) / periods
        mine = [b for b in bonuses if b.with_regular and b.pay_date == d]
        gross, def_comp, match_comp, ne_comp = totals(mine, base)
        events.append(PayEvent(
            d, "regular+bonus" if mine else "regular", gross, def_comp, match_comp, ne_comp,
            dd.hsa_per_paycheck, dd.other_pretax_per_paycheck, dd.other_posttax_per_paycheck,
            sum(b.gross for b in mine), ", ".join(b.name for b in mine)))
    for b in bonuses:
        if not b.with_regular:
            gross, def_comp, match_comp, ne_comp = totals([b], 0.0)
            events.append(PayEvent(b.pay_date, "bonus", gross, def_comp, match_comp, ne_comp,
                                   0.0, 0.0, 0.0, b.gross, b.name))
    events.sort(key=lambda e: (e.pay_date, e.kind == "bonus"))
    return events

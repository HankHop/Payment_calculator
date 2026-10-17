"""Load and validate the user's JSON config into typed objects."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

FILING_STATUSES = ("single", "mfj", "mfs", "hoh")
FREQUENCIES = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Raise:
    effective_date: date
    new_salary: float


@dataclass(frozen=True)
class Bonus:
    pay_date: date
    amount: Optional[float]               # fixed dollars, or None
    percent_of_salary: Optional[float]    # % of base salary in effect on pay_date, or None
    separate_paycheck: bool = False       # False: paid with the regular paycheck when dates match
    name: str = ""
    # Per-bonus eligibility; None = use the global flags under contributions / employer.
    eligible_for_employee_contributions: Optional[bool] = None
    eligible_for_match: Optional[bool] = None
    eligible_for_nonelective: Optional[bool] = None


@dataclass(frozen=True)
class Employer:
    match_rate_percent: float             # employer matches this % of each employee dollar...
    match_up_to_percent_of_pay: float     # ...up to this % of eligible pay
    nonelective_percent: float            # unmatched employer contribution, % of eligible pay
    bonus_eligible_for_match: bool
    bonus_eligible_for_nonelective: bool


@dataclass(frozen=True)
class Election:
    """Contribution percentages in force for pay dates on or after effective_date."""
    effective_date: date
    pretax_percent: float
    posttax_percent: float


@dataclass(frozen=True)
class Contributions:
    elections: tuple                       # tuple[Election, ...], sorted by effective_date
    after_tax_enabled: bool                # False while posttax_type is "after_tax" and the
                                           # mega backdoor acknowledgement is not set
    posttax_type: str                      # "roth" (elective, shares the deferral limit) or
                                           # "after_tax" (non-Roth; counts only toward the 415(c) limit)
    bonus_eligible_for_employee_contributions: bool
    employer: Employer


@dataclass(frozen=True)
class Recommendation:
    target_employee_contribution: object  # elective (pretax/Roth) target: "max" or a dollar amount
    target_total_additions: object        # after-tax mode only: "max", "none" or a dollar amount
    expected_retirement_marginal_rate_percent: float
    percent_step: float
    plan_max_percent: float


@dataclass(frozen=True)
class Deductions:
    """Per-regular-paycheck payroll deductions (none are taken from the bonus paycheck)."""
    hsa_per_paycheck: float
    hsa_coverage: Optional[str]           # "self", "family" or None (skips the HSA limit check)
    other_pretax_per_paycheck: float      # Section 125 premiums: medical, dental, vision, FSA
    other_posttax_per_paycheck: float     # life insurance, dues, ESPP, etc.
    hsa_max_out: bool = False             # derive hsa_per_paycheck from the annual limit
    hsa_employer_annual: float = 0.0      # employer HSA money for the year (counts toward the limit)


@dataclass(frozen=True)
class WithholdingChange:
    """W-4 settings in force for pay dates on or after effective_date."""
    effective_date: date
    regular_federal_per_paycheck: Optional[float]   # None = estimate from brackets
    extra_federal_per_paycheck: float               # W-4 step 4(c) extra withholding
    multiple_jobs: bool = False                     # W-4 step 2(c) box: "multiple jobs / spouse works"


@dataclass(frozen=True)
class Withholding:
    supplemental_rate_percent: float = 22.0         # flat federal rate on bonus pay (37% above $1M)
    changes: tuple = ()                             # tuple[WithholdingChange, ...], sorted by date


@dataclass(frozen=True)
class WhatIf:
    """A proposed election change to estimate: replaces any election from effective_date onward."""
    effective_date: date
    pretax_percent: float
    posttax_percent: float


@dataclass(frozen=True)
class Config:
    tax_year: int
    filing_status: str
    state: str
    age_end_of_year: Optional[int]
    as_of_date: Optional[date]
    base_salary: float
    frequency: str
    first_pay_date: Optional[date]
    raise_: Optional[Raise]
    bonuses: tuple                        # tuple[Bonus, ...]
    contributions: Contributions
    recommendation: Recommendation
    deductions: Deductions
    monthly_expenses: Optional[float] = None   # optional: living costs paid from take-home
    withholding: Withholding = Withholding()
    what_if: Optional[WhatIf] = None


def _date(value, field) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{field}: expected YYYY-MM-DD, got {value!r}")


def _pct(value, field, lo=0.0, hi=100.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"{field}: expected a number between {lo} and {hi}, got {value!r}")
    return float(value)


def _bool(value, field) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field}: true or false")
    return value


def _in_year(d: date, year: int, field: str) -> date:
    if d.year != year:
        raise ConfigError(f"{field}: {d} is not in tax_year {year}")
    return d


def parse(raw: dict) -> Config:
    year = raw.get("tax_year", 2026)
    status = raw.get("filing_status")
    if status not in FILING_STATUSES:
        raise ConfigError(f"filing_status: must be one of {FILING_STATUSES}, got {status!r}")
    state = str(raw.get("state", "")).upper()
    if not state:
        raise ConfigError("state: required (two-letter code, e.g. 'TX')")

    age = raw.get("age_end_of_year")
    if age is not None and (not isinstance(age, int) or not 16 <= age <= 120):
        raise ConfigError(f"age_end_of_year: expected an integer or null, got {age!r}")

    pay = raw.get("pay") or {}
    salary = pay.get("base_salary")
    if not isinstance(salary, (int, float)) or salary <= 0:
        raise ConfigError("pay.base_salary: required, must be > 0")
    freq = pay.get("frequency")
    if freq not in FREQUENCIES:
        raise ConfigError(f"pay.frequency: must be one of {tuple(FREQUENCIES)}, got {freq!r}")
    first_pay = pay.get("first_pay_date")
    if freq in ("weekly", "biweekly"):
        if first_pay is None:
            raise ConfigError(f"pay.first_pay_date: required for {freq} pay (any pay date in the year)")
        first_pay = _date(first_pay, "pay.first_pay_date")
    else:
        first_pay = None

    raise_ = None
    if pay.get("raise"):
        r = pay["raise"]
        eff = _in_year(_date(r.get("effective_date"), "pay.raise.effective_date"), year, "pay.raise.effective_date")
        if r.get("new_salary") is not None:
            new = float(r["new_salary"])
        elif r.get("percent") is not None:
            new = salary * (1 + _pct(r["percent"], "pay.raise.percent", 0, 1000) / 100)
        else:
            raise ConfigError("pay.raise: give either new_salary or percent")
        raise_ = Raise(eff, new)

    raw_bonuses = list(pay.get("bonuses") or [])
    if pay.get("bonus"):
        raw_bonuses.insert(0, pay["bonus"])
    bonuses = []
    for i, b in enumerate(raw_bonuses):
        f = f"pay.bonuses[{i}]"
        d = _in_year(_date(b.get("pay_date"), f"{f}.pay_date"), year, f"{f}.pay_date")
        amount, pct = b.get("amount"), b.get("percent_of_salary")
        if (amount is None) == (pct is None):
            raise ConfigError(f"{f}: give exactly one of amount or percent_of_salary")
        flags = {}
        for key in ("eligible_for_employee_contributions", "eligible_for_match", "eligible_for_nonelective"):
            v = b.get(key)
            if v is not None and not isinstance(v, bool):
                raise ConfigError(f"{f}.{key}: true or false")
            flags[key] = v
        bonuses.append(Bonus(d, float(amount) if amount is not None else None,
                             _pct(pct, f"{f}.percent_of_salary", 0, 1000) if pct is not None else None,
                             bool(b.get("separate_paycheck", False)), str(b.get("name", "")), **flags))

    c = raw.get("contributions") or {}
    e = c.get("employer") or {}
    raw_elections = c.get("elections")
    if raw_elections is None:  # single set of percentages for the whole year
        raw_elections = [{"effective_date": f"{year}-01-01",
                          "pretax_percent": c.get("pretax_percent", 0),
                          "posttax_percent": c.get("posttax_percent", 0)}]
    elections = []
    for i, el in enumerate(raw_elections):
        f = f"contributions.elections[{i}]"
        pre = _pct(el.get("pretax_percent", 0), f"{f}.pretax_percent")
        post = _pct(el.get("posttax_percent", 0), f"{f}.posttax_percent")
        if pre + post > 100:
            raise ConfigError(f"{f}: pretax_percent + posttax_percent exceeds 100")
        elections.append(Election(_in_year(_date(el.get("effective_date"), f"{f}.effective_date"),
                                           year, f"{f}.effective_date"), pre, post))
    posttax_type = c.get("posttax_type", "roth")
    if posttax_type not in ("roth", "after_tax"):
        raise ConfigError("contributions.posttax_type: 'roth' or 'after_tax'")
    ack = c.get("acknowledge_mega_backdoor", False)
    if not isinstance(ack, bool):
        raise ConfigError("contributions.acknowledge_mega_backdoor: true or false")
    after_tax_enabled = posttax_type != "after_tax" or ack
    if not after_tax_enabled:  # hidden: after-tax elections are ignored entirely
        elections = [Election(e.effective_date, e.pretax_percent, 0.0) for e in elections]
    elections.sort(key=lambda x: x.effective_date)
    if len({e.effective_date for e in elections}) != len(elections):
        raise ConfigError("contributions.elections: two elections share an effective_date")
    employer = Employer(
        match_rate_percent=_pct(e.get("match_rate_percent", 0), "employer.match_rate_percent", 0, 1000),
        match_up_to_percent_of_pay=_pct(e.get("match_up_to_percent_of_pay", 0), "employer.match_up_to_percent_of_pay"),
        nonelective_percent=_pct(e.get("nonelective_percent", 0), "employer.nonelective_percent"),
        bonus_eligible_for_match=bool(e.get("bonus_eligible_for_match", False)),
        bonus_eligible_for_nonelective=bool(e.get("bonus_eligible_for_nonelective", False)),
    )
    contributions = Contributions(
        tuple(elections), after_tax_enabled, posttax_type, bool(c.get("bonus_eligible_for_employee_contributions", False)), employer)

    rec = raw.get("recommendation") or {}
    target = rec.get("target_employee_contribution", "max")
    if target != "max" and (not isinstance(target, (int, float)) or target < 0):
        raise ConfigError("recommendation.target_employee_contribution: 'max' or a dollar amount")
    total_target = rec.get("target_total_additions", "max")
    if total_target not in ("max", "none") and (not isinstance(total_target, (int, float)) or total_target < 0):
        raise ConfigError("recommendation.target_total_additions: 'max', 'none' or a dollar amount")
    as_of = raw.get("as_of_date")
    as_of = _in_year(_date(as_of, "as_of_date"), year, "as_of_date") if as_of else None
    recommendation = Recommendation(
        target_employee_contribution=target,
        target_total_additions=total_target,
        expected_retirement_marginal_rate_percent=_pct(
            rec.get("expected_retirement_marginal_rate_percent", 22),
            "recommendation.expected_retirement_marginal_rate_percent", 0, 100),
        percent_step=_pct(rec.get("percent_step", 1), "recommendation.percent_step", 0.01, 100),
        plan_max_percent=_pct(rec.get("plan_max_percent", 100), "recommendation.plan_max_percent", 0, 100),
    )

    d = raw.get("deductions") or {}
    coverage = d.get("hsa_coverage")
    if coverage not in (None, "self", "family"):
        raise ConfigError("deductions.hsa_coverage: 'self', 'family' or null")
    deductions = Deductions(
        _pct(d.get("hsa_per_paycheck", 0), "deductions.hsa_per_paycheck", 0, 1e7),
        coverage,
        _pct(d.get("other_pretax_per_paycheck", 0), "deductions.other_pretax_per_paycheck", 0, 1e7),
        _pct(d.get("other_posttax_per_paycheck", 0), "deductions.other_posttax_per_paycheck", 0, 1e7),
        _bool(d.get("hsa_max_out", False), "deductions.hsa_max_out"),
        _pct(d.get("hsa_employer_annual", 0), "deductions.hsa_employer_annual", 0, 1e7))
    if deductions.hsa_max_out and coverage is None:
        raise ConfigError("deductions.hsa_max_out needs hsa_coverage ('self' or 'family')")

    w = raw.get("withholding") or {}
    changes = []
    for i, ch in enumerate(w.get("changes") or []):
        f = f"withholding.changes[{i}]"
        reg = ch.get("regular_federal_per_paycheck")
        changes.append(WithholdingChange(
            _in_year(_date(ch.get("effective_date"), f"{f}.effective_date"), year, f"{f}.effective_date"),
            None if reg is None else _pct(reg, f"{f}.regular_federal_per_paycheck", 0, 1e7),
            _pct(ch.get("extra_federal_per_paycheck", 0), f"{f}.extra_federal_per_paycheck", 0, 1e7),
            _bool(ch.get("multiple_jobs", False), f"{f}.multiple_jobs")))
    changes.sort(key=lambda x: x.effective_date)
    if len({c_.effective_date for c_ in changes}) != len(changes):
        raise ConfigError("withholding.changes: two changes share an effective_date")
    withholding = Withholding(_pct(w.get("supplemental_rate_percent", 22), "withholding.supplemental_rate_percent"),
                              tuple(changes))

    what_if = None
    wi = raw.get("what_if")
    if wi:
        f = "what_if"
        pre = _pct(wi.get("pretax_percent", 0), f"{f}.pretax_percent")
        post = _pct(wi.get("posttax_percent", 0), f"{f}.posttax_percent") if after_tax_enabled else 0.0
        if pre + post > 100:
            raise ConfigError(f"{f}: pretax_percent + posttax_percent exceeds 100")
        what_if = WhatIf(_in_year(_date(wi.get("effective_date"), f"{f}.effective_date"), year,
                                  f"{f}.effective_date"), pre, post)

    return Config(year, status, state, age, as_of, float(salary), freq, first_pay, raise_, tuple(bonuses),
                  contributions, recommendation, deductions,
                  None if raw.get("monthly_expenses") is None
                  else _pct(raw["monthly_expenses"], "monthly_expenses", 0, 1e9),
                  withholding, what_if)


def load(path) -> Config:
    try:
        raw = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})")
    return parse(raw)

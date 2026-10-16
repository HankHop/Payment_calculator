"""Walk the year's pay events and accumulate gross pay and contributions.

Limits modeled
- Elective deferrals (pretax, plus Roth when posttax_type == "roth"): IRS limit + catch-up.
- Section 415(c) total additions (employee pretax + post-tax + employer): IRS limit + catch-up.
  The 415(c) cap is enforced on post-tax contributions when posttax_type == "after_tax".
- 401(a)(17) compensation limit on pay the percentages apply to.
- Optional take-home cap: post-tax contributions cannot exceed pay left after estimated taxes,
  HSA, premiums, other post-tax deductions and pretax 401(k) (take_home_rate = all-in tax / gross).
  An election of 100% therefore contributes whatever is left, as payroll does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence, Tuple

from .config import Config
from .schedule import PayEvent

Election = Tuple[date, float, float]  # (effective_date, pretax_percent, posttax_percent)


@dataclass(frozen=True)
class Plan:
    elections: Tuple[Election, ...]
    posttax_type: str
    match_rate: float             # fraction, e.g. 0.5 for 50%
    match_cap_percent: float
    nonelective_percent: float
    employee_limit: float         # elective deferral limit incl. catch-up
    total_cap: float              # 415(c) limit incl. catch-up
    comp_limit: float             # 401(a)(17)
    take_home_rate: Optional[float] = None

    def percents_on(self, d: date) -> Tuple[float, float]:
        pre = post = 0.0
        for eff, p, q in self.elections:
            if eff <= d:
                pre, post = p, q
        return pre, post

    @property
    def pretax_percent(self) -> float:      # percentages in force at year end
        return self.elections[-1][1] if self.elections else 0.0

    @property
    def posttax_percent(self) -> float:
        return self.elections[-1][2] if self.elections else 0.0


@dataclass
class EventRow:
    pay_date: date
    kind: str
    gross: float
    pretax: float
    posttax: float
    match: float
    nonelective: float
    hsa: float = 0.0
    other_pretax: float = 0.0
    other_posttax: float = 0.0
    posttax_requested: float = 0.0              # what the election asks for on this paycheck
    posttax_available: Optional[float] = None   # pay left for it after taxes and deductions
    label: str = ""                             # bonus name(s) paid in this paycheck


@dataclass
class YearResult:
    posttax_type: str = "roth"
    rows: List[EventRow] = field(default_factory=list)
    limit_reached_on: Optional[date] = None      # elective limit
    total_cap_reached_on: Optional[date] = None  # 415(c)

    def _sum(self, attr):
        return sum(getattr(r, attr) for r in self.rows)

    @property
    def gross(self): return self._sum("gross")
    @property
    def pretax(self): return self._sum("pretax")
    @property
    def posttax(self): return self._sum("posttax")
    @property
    def match(self): return self._sum("match")
    @property
    def nonelective(self): return self._sum("nonelective")
    @property
    def hsa(self): return self._sum("hsa")
    @property
    def other_pretax(self): return self._sum("other_pretax")
    @property
    def other_posttax(self): return self._sum("other_posttax")
    @property
    def employee(self): return self.pretax + self.posttax
    @property
    def employer(self): return self.match + self.nonelective
    @property
    def elective(self): return self.pretax + (self.posttax if self.posttax_type == "roth" else 0.0)
    @property
    def total_additions(self): return self.employee + self.employer


def _catch_up(cfg: Config, fed: dict) -> float:
    r = fed["retirement"]
    age = cfg.age_end_of_year
    if age is None:
        return 0.0
    if 60 <= age <= 63:
        return float(r["catch_up_60_to_63"])
    return float(r["catch_up_50_plus"]) if age >= 50 else 0.0


def employee_limit(cfg: Config, fed: dict) -> float:
    return float(fed["retirement"]["elective_deferral_limit"]) + _catch_up(cfg, fed)


def additions_cap(cfg: Config, fed: dict) -> float:
    """415(c) excludes catch-up contributions, so they raise the total cap."""
    return float(fed["retirement"]["annual_additions_limit_415c"]) + _catch_up(cfg, fed)


def make_plan(cfg: Config, fed: dict, pretax_percent: float, posttax_percent: float) -> Plan:
    """Constant percentages for the whole year."""
    return plan_from_elections(cfg, fed, ((date(cfg.tax_year, 1, 1), pretax_percent, posttax_percent),))


def plan_from_config(cfg: Config, fed: dict) -> Plan:
    return plan_from_elections(
        cfg, fed, tuple((e.effective_date, e.pretax_percent, e.posttax_percent)
                        for e in cfg.contributions.elections))


def plan_from_elections(cfg: Config, fed: dict, elections: Sequence[Election],
                        take_home_rate: Optional[float] = None) -> Plan:
    e = cfg.contributions.employer
    return Plan(tuple(elections), cfg.contributions.posttax_type, e.match_rate_percent / 100,
                e.match_up_to_percent_of_pay, e.nonelective_percent,
                employee_limit(cfg, fed), additions_cap(cfg, fed),
                float(fed["retirement"]["compensation_limit_401a17"]), take_home_rate)


def eligible_deferral_comp(events: Sequence[PayEvent], comp_limit: float) -> float:
    """Total pay the employee percentage applies to over the year (after the 401(a)(17) cap)."""
    return min(sum(e.deferral_comp for e in events), comp_limit)


def simulate(events: Sequence[PayEvent], plan: Plan) -> YearResult:
    """Per-paycheck contributions, in the order payroll applies them: taxes, HSA and premiums come
    out first, then the 401(k) elections take what they ask for, limited by what is left.
    Match is calculated per paycheck (no annual true-up) and, for after-tax mode, only on
    pretax deferrals."""
    after_tax = plan.posttax_type == "after_tax"
    result = YearResult(posttax_type=plan.posttax_type)
    cum_def_comp = cum_match_comp = cum_ne_comp = 0.0
    cum_elective = cum_total = 0.0

    for ev in events:
        pre_pct, post_pct = plan.percents_on(ev.pay_date)
        def_comp = max(0.0, min(ev.deferral_comp, plan.comp_limit - cum_def_comp))
        match_comp = max(0.0, min(ev.match_comp, plan.comp_limit - cum_match_comp))
        ne_comp = max(0.0, min(ev.nonelective_comp, plan.comp_limit - cum_ne_comp))
        cum_def_comp += def_comp
        cum_match_comp += match_comp
        cum_ne_comp += ne_comp

        pre = pre_pct / 100 * def_comp
        post = post_pct / 100 * def_comp
        room = max(0.0, plan.employee_limit - cum_elective)
        if after_tax:
            pre = min(pre, room)
        elif pre + post > room:
            scale = room / (pre + post) if pre + post else 0.0
            pre, post = pre * scale, post * scale

        # Take-home cap: post-tax money can only come from pay left after estimated taxes,
        # HSA, premiums, other post-tax deductions and the pretax 401(k).
        requested, available = post, None
        if plan.take_home_rate is not None:
            available = max(0.0, ev.gross * (1 - plan.take_home_rate)
                            - ev.hsa - ev.other_pretax - ev.other_posttax - pre)
            post = min(post, available)

        deferral = pre if after_tax else pre + post
        share = (match_comp / def_comp) if def_comp > 0 else 0.0
        matchable = min(deferral * min(share, 1.0), plan.match_cap_percent / 100 * match_comp)
        match = plan.match_rate * matchable
        nonelective = plan.nonelective_percent / 100 * ne_comp

        # 415(c) is a hard stop: pretax, employer, then post-tax fit in what room is left.
        total_room = max(0.0, plan.total_cap - cum_total)
        pre = min(pre, total_room)
        match = min(match, max(0.0, total_room - pre))
        nonelective = min(nonelective, max(0.0, total_room - pre - match))
        post = min(post, max(0.0, total_room - pre - match - nonelective))

        cum_elective += pre + (0.0 if after_tax else post)
        cum_total += pre + post + match + nonelective
        if result.limit_reached_on is None and cum_elective >= plan.employee_limit - 0.005 and (pre or post):
            result.limit_reached_on = ev.pay_date
        if result.total_cap_reached_on is None and cum_total >= plan.total_cap - 0.005:
            result.total_cap_reached_on = ev.pay_date

        result.rows.append(EventRow(ev.pay_date, ev.kind, ev.gross, pre, post, match, nonelective,
                                    ev.hsa, ev.other_pretax, ev.other_posttax, requested, available,
                                    ev.bonus_label))
    return result

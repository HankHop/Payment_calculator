"""Recommend pretax and post-tax contribution percentages for the rest of the year.

If config.as_of_date is set, paychecks on or before that date keep your actual elections and
the recommendation applies to later paychecks only. Otherwise it covers the whole year.

Elective target (pretax, plus Roth in "roth" mode)
  needed = IRS limit (or your dollar target) - elective contributions already made
  percent = needed / remaining eligible pay, rounded DOWN to the plan step, so the limit is
  not hit early and match is not lost late in the year.
  "roth" mode: pretax dollars are used while the combined federal + state marginal rate saved
  on the last pretax dollar is above the expected retirement marginal rate; the rest is Roth.
  "after_tax" mode: all elective dollars are pretax.

After-tax mode (non-Roth post-tax contributions)
  These count toward the 415(c) total additions limit, not the elective limit.
  percent = (415(c) limit - everything already contributed or projected: pretax, employer,
  prior after-tax) / remaining eligible pay, rounded down, then capped by what take-home pay
  can cover after pretax contributions and estimated taxes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence

from . import taxes
from .config import Config
from .schedule import PayEvent
from .simulate import (Plan, YearResult, employee_limit, additions_cap, plan_from_elections,
                       simulate)


@dataclass
class Recommendation:
    pretax_percent: float
    posttax_percent: float
    effective_from: Optional[date]
    target_dollars: float                 # elective target
    pretax_dollars_target: float          # pretax portion of the elective target (full year)
    result: YearResult
    plan: Plan
    warnings: List[str] = field(default_factory=list)


def _floor_to(value: float, step: float) -> float:
    return round(math.floor(value / step + 1e-9) * step, 6)


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, 6)


def _pretax_dollars(total_dollars: float, gross: float, cfg: Config, fed: dict, st: dict) -> float:
    """Largest pretax amount (<= total_dollars) whose last dollar still saves more than the
    expected retirement marginal rate."""
    r = cfg.recommendation.expected_retirement_marginal_rate_percent / 100

    def worthwhile(p: float) -> bool:
        rate = (taxes.federal_marginal(gross, p, cfg.filing_status, fed)
                + taxes.state_marginal(gross, p, cfg.filing_status, st))
        return rate > r

    whole = int(math.floor(total_dollars))
    if whole < 1 or not worthwhile(1):
        return 0.0
    lo, hi = 1, whole
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if worthwhile(mid):
            lo = mid
        else:
            hi = mid - 1
    return total_dollars if lo == whole else float(lo)


def _empty(plan, events, target, warnings, start):
    return Recommendation(0.0, 0.0, start, target, 0.0, simulate(events, plan), plan, warnings)


def recommend(cfg: Config, events: Sequence[PayEvent], fed: dict, st: dict) -> Recommendation:
    rc = cfg.recommendation
    step = rc.percent_step
    after_tax = cfg.contributions.posttax_type == "after_tax"
    as_of = cfg.as_of_date
    warnings: List[str] = []
    comp_limit = float(fed["retirement"]["compensation_limit_401a17"])

    locked = tuple((e.effective_date, e.pretax_percent, e.posttax_percent)
                   for e in cfg.contributions.elections if as_of and e.effective_date <= as_of)
    remaining = [ev for ev in events if as_of is None or ev.pay_date > as_of]
    start = remaining[0].pay_date if remaining else None

    def plan_with(pre: float, post: float, rate: Optional[float] = None) -> Plan:
        return plan_from_elections(cfg, fed, locked + (((start, pre, post),) if start else ()), rate)

    # Elective target
    limit = employee_limit(cfg, fed)
    target = limit
    if rc.target_employee_contribution != "max":
        if rc.target_employee_contribution > limit:
            warnings.append(f"Target ${rc.target_employee_contribution:,.0f} exceeds the IRS limit; "
                            f"using ${limit:,.0f}.")
        else:
            target = float(rc.target_employee_contribution)

    if not remaining:
        warnings.append("No paychecks remain after as_of_date; nothing to recommend.")
        return _empty(plan_with(0, 0), events, target, warnings, start)

    # Pay the percentages apply to: already paid vs remaining (401(a)(17) applied cumulatively)
    cum = ytd_comp = rem_comp = 0.0
    for ev in events:
        c = max(0.0, min(ev.deferral_comp, comp_limit - cum))
        cum += c
        if as_of is None or ev.pay_date > as_of:
            rem_comp += c
        else:
            ytd_comp += c
    if rem_comp <= 0:
        warnings.append("No remaining pay is eligible for employee contributions; nothing to recommend.")
        return _empty(plan_with(0, 0), events, target, warnings, start)

    # Year-to-date contributions: simulate with no further employee contributions
    base = simulate(events, plan_with(0.0, 0.0))
    paid = [r for r in base.rows if as_of is not None and r.pay_date <= as_of]
    ytd_pre = sum(r.pretax for r in paid)
    ytd_post = sum(r.posttax for r in paid)
    ytd_elective = ytd_pre + (0.0 if after_tax else ytd_post)

    need = max(0.0, target - ytd_elective)
    exact = need / rem_comp * 100
    elective_pct = min(_floor_to(exact, step), rc.plan_max_percent)
    if elective_pct < exact - 1e-9 and elective_pct == rc.plan_max_percent:
        warnings.append(f"Plan maximum of {elective_pct:g}% is below the {exact:.2f}% needed to reach the target.")
    elective_dollars = elective_pct / 100 * rem_comp
    gross = sum(e.gross for e in events)

    if after_tax:
        pre_pct, roth_pct = elective_pct, 0.0
        pre_target = ytd_pre + elective_dollars
    else:
        pre_target = _pretax_dollars(ytd_elective + elective_dollars, gross, cfg, fed, st)
        rem_pre = min(max(0.0, pre_target - ytd_pre), elective_dollars)
        pre_pct = min(_round_to(rem_pre / rem_comp * 100, step), elective_pct)
        roth_pct = round(elective_pct - pre_pct, 6)

    post_pct, rate = roth_pct, None
    if after_tax:
        wanted = rc.target_total_additions
        cap = additions_cap(cfg, fed)
        total_target = None if wanted == "none" else cap if wanted == "max" else float(wanted)
        if total_target is not None and wanted != "max" and total_target > cap:
            warnings.append(f"Total target ${total_target:,.0f} exceeds the 415(c) limit; using ${cap:,.0f}.")
            total_target = cap
        if total_target is not None:
            # Taxes depend only on gross and pretax, so the rate can be found first and then
            # applied to the already-paid after-tax contributions as well.
            sim0 = simulate(events, plan_with(pre_pct, 0.0))
            tax = taxes.summarize(sim0.gross, sim0.pretax, cfg.filing_status, fed, st)
            rate = tax.total / sim0.gross if sim0.gross else 0.0
            sim1 = simulate(events, plan_with(pre_pct, 0.0, rate))
            need_post = max(0.0, total_target - sim1.total_additions)
            wanted_pct = _floor_to(need_post / rem_comp * 100, step)
            # What take-home pay can cover, per remaining paycheck
            feasible = min(
                ((ev.gross * (1 - rate) - pre_pct / 100 * ev.deferral_comp) / ev.deferral_comp * 100
                 for ev in remaining if ev.deferral_comp > 0), default=0.0)
            feasible = _floor_to(max(0.0, feasible), step)
            post_pct = min(wanted_pct, feasible, rc.plan_max_percent, 100 - pre_pct)
            if post_pct < wanted_pct - 1e-9:
                warnings.append(
                    f"Reaching the target needs {wanted_pct:g}% after-tax, but take-home pay after "
                    f"pretax contributions and estimated taxes covers about {feasible:g}% "
                    f"(plan max {rc.plan_max_percent:g}%). Using {post_pct:g}%.")
        else:
            post_pct = 0.0

    plan = plan_with(pre_pct, post_pct, rate)
    result = simulate(events, plan)

    if after_tax and total_target is not None:
        gap = total_target - result.total_additions
        if gap > 1.0:
            warnings.append(f"Projected total additions fall ${gap:,.0f} short of the "
                            f"${total_target:,.0f} target (percent rounding to {step:g}% steps"
                            f"{' and take-home limit' if post_pct < wanted_pct - 1e-9 else ''}).")
    match_cap = cfg.contributions.employer.match_up_to_percent_of_pay
    if need > 0 and cfg.contributions.employer.match_rate_percent > 0 and pre_pct + (0 if after_tax else roth_pct) < match_cap:
        warnings.append(f"Elective {pre_pct + (0 if after_tax else roth_pct):g}% is below the "
                        f"{match_cap:g}% needed for the full employer match.")
    if result.total_additions > additions_cap(cfg, fed) + 0.01:
        warnings.append("Employee + employer contributions exceed the IRS 415(c) annual additions limit.")
    return Recommendation(pre_pct, post_pct, start, target, pre_target, result, plan, warnings)

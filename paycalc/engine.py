"""Tie config, schedule, simulation, taxes and recommendation together."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List

from . import data, taxes
from .config import Config
from .recommend import Recommendation, recommend
from .schedule import PayEvent, build_events
from .simulate import Plan, YearResult, additions_cap, plan_from_config, simulate


@dataclass
class Scenario:
    label: str
    plan: Plan
    result: YearResult
    tax: taxes.TaxSummary
    uncapped_posttax: float = 0.0   # post-tax the elections ask for before the take-home cap

    @property
    def net_pay(self) -> float:
        return self.result.gross - self.result.employee - self.tax.total


@dataclass
class Analysis:
    cfg: Config
    state_name: str
    events: List[PayEvent]
    current: Scenario
    recommended: Scenario
    recommendation: Recommendation
    warnings: List[str] = field(default_factory=list)


def _scenario(label, cfg, events, fed, st, plan) -> Scenario:
    """Taxes depend only on gross and pretax, so the take-home cap on after-tax contributions
    (which needs the tax rate) can be applied in a second pass without circularity."""
    result = simulate(events, plan)
    tax = taxes.summarize(result.gross, result.pretax, cfg.filing_status, fed, st)
    uncapped = result.posttax
    if plan.posttax_type == "after_tax" and plan.take_home_rate is None:
        plan = replace(plan, take_home_rate=tax.total / result.gross)
        result = simulate(events, plan)
    return Scenario(label, plan, result, tax, uncapped)


def analyze(cfg: Config) -> Analysis:
    fed = data.federal(cfg.tax_year)
    st = data.state(cfg.tax_year, cfg.state)
    events = build_events(cfg)

    current = _scenario("Current", cfg, events, fed, st, plan_from_config(cfg, fed))
    rec = recommend(cfg, events, fed, st)
    recommended = _scenario("Recommended", cfg, events, fed, st, rec.plan)

    warnings: List[str] = []
    res = current.result
    last_regular = max(e.pay_date for e in events if e.kind == "regular")
    if res.limit_reached_on and res.limit_reached_on < last_regular:
        warnings.append(
            f"Current elections hit the elective limit on {res.limit_reached_on}; later paychecks "
            f"get no pretax contribution and no match.")
    cap = additions_cap(cfg, fed)
    if res.total_cap_reached_on and res.total_cap_reached_on < last_regular:
        warnings.append(f"Current elections reach the 415(c) total limit on {res.total_cap_reached_on}; "
                        f"later pretax, employer and after-tax contributions stop.")
    if res.total_additions > cap + 0.01:
        warnings.append("Current employee + employer contributions exceed the IRS 415(c) limit.")
    if current.uncapped_posttax - res.posttax > 1.0:
        warnings.append(
            f"Current post-tax election asks for ${current.uncapped_posttax:,.0f} but take-home pay "
            f"after pretax contributions and estimated taxes only covers ${res.posttax:,.0f}. "
            f"Payroll will likely cap or reject the rest; figures here use the capped amount.")
    if st.get("note"):
        warnings.append(f"{st['name']}: {st['note']}")
    warnings.extend(rec.warnings)
    return Analysis(cfg, st["name"], events, current, recommended, rec, warnings)

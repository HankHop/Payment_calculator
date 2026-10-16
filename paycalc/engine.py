"""Tie config, schedule, simulation, taxes and recommendation together."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List

from . import data, taxes
from .config import Config
from .recommend import Recommendation, recommend
from .schedule import PayEvent, build_events
from .withholding import Withholder
from .simulate import Plan, YearResult, additions_cap, plan_from_config, simulate


@dataclass
class Scenario:
    label: str
    plan: Plan
    result: YearResult
    tax: taxes.TaxSummary
    uncapped_posttax: float = 0.0   # post-tax the elections ask for before the take-home cap
    tax_without_pretax: float = 0.0  # total tax if no pretax 401(k) were deducted

    @property
    def withheld_total(self) -> float:
        return sum(r.withheld for r in self.result.rows)

    @property
    def fed_withheld(self) -> float:
        return sum(r.fed_withheld for r in self.result.rows)

    @property
    def extra_withheld(self) -> float:
        return sum(r.extra_withheld for r in self.result.rows)

    @property
    def fed_balance(self) -> float:
        """Federal withheld minus federal liability: positive is a refund, negative is tax owed."""
        return self.fed_withheld - self.tax.federal

    @property
    def net_pay(self) -> float:
        """Take-home actually paid out over the year: after what payroll withheld."""
        r = self.result
        return r.gross - r.employee - r.hsa - r.other_pretax - r.other_posttax - self.withheld_total

    @property
    def net_pay_settled(self) -> float:
        """Take-home once the year's tax is settled (liability instead of withholding)."""
        r = self.result
        return r.gross - r.employee - r.hsa - r.other_pretax - r.other_posttax - self.tax.total


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
    """Taxes depend only on gross, pretax 401(k), HSA and premiums, so the take-home cap on
    post-tax contributions (which needs the tax rate) is applied in a second pass without
    circularity."""
    def taxed(result):
        return taxes.summarize(result.gross, result.pretax, cfg.filing_status, fed, st,
                               result.hsa, result.other_pretax)

    result = simulate(events, plan)
    tax = taxed(result)
    uncapped = result.posttax
    if plan.withholder is None:
        plan = replace(plan, withholder=Withholder(cfg, fed, st, events,
                                                   tax.state / result.gross if result.gross else 0.0))
        result = simulate(events, plan)
    no_pretax = taxes.summarize(result.gross, 0.0, cfg.filing_status, fed, st,
                                result.hsa, result.other_pretax).total
    return Scenario(label, plan, result, tax, uncapped, no_pretax)


def _hsa_limit(cfg: Config, fed: dict):
    cov = cfg.deductions.hsa_coverage
    if cov is None:
        return None
    h = fed["hsa"]
    limit = h["self_only"] if cov == "self" else h["family"]
    if cfg.age_end_of_year is not None and cfg.age_end_of_year >= 55:
        limit += h["catch_up_55"]
    return float(limit)


def analyze(cfg: Config) -> Analysis:
    fed = data.federal(cfg.tax_year)
    st = data.state(cfg.tax_year, cfg.state)
    events = build_events(cfg)

    current = _scenario("Current", cfg, events, fed, st, plan_from_config(cfg, fed))
    rec = recommend(cfg, events, fed, st)
    recommended = _scenario("Recommended", cfg, events, fed, st, rec.plan)

    warnings: List[str] = []
    if not cfg.contributions.after_tax_enabled:
        warnings.append(
            "After-tax contributions are hidden and ignored. They only make sense if your plan allows "
            "an in-plan Roth conversion or in-service rollover to a Roth IRA (mega backdoor Roth). "
            "Set contributions.acknowledge_mega_backdoor to true to include them.")
    res = current.result
    last_regular = max(e.pay_date for e in events if e.kind != "bonus")
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
            f"Current post-tax elections ask for ${current.uncapped_posttax:,.0f}, but after estimated "
            f"taxes, HSA, premiums and pretax contributions only ${res.posttax:,.0f} is left to deduct. "
            f"Figures here use the amount actually deducted (elections near 100% take whatever remains).")
    hsa_limit = _hsa_limit(cfg, fed)
    if hsa_limit is not None and res.hsa > hsa_limit + 0.01:
        warnings.append(f"HSA payroll contributions of ${res.hsa:,.0f} exceed the {cfg.tax_year} limit of "
                        f"${hsa_limit:,.0f} (employer HSA contributions also count toward it).")
    if res.hsa and st.get("taxes_hsa"):
        warnings.append(f"{st['name']} does not exclude HSA contributions from state taxable income.")
    if st.get("note"):
        warnings.append(f"{st['name']}: {st['note']}")
    warnings.extend(rec.warnings)
    return Analysis(cfg, st["name"], events, current, recommended, rec, warnings)

"""Household view: two people's paychecks against family expenses and a shared (joint) account.

Each person is a full calculator config. Month by month (by pay date) this works out what each of
them can send to the joint account after their own family expenses, and how much a starting
inheritance balance has to cover when take-home pay falls short (for example, take-home of $0 while
maxing out after-tax contributions). Taxes are the payroll withholding from each paycheck.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import Config, ConfigError
from .engine import Analysis, analyze
from .report import monthly

MAX_ITEMS = 60
SPOUSE_SCENARIOS = ("current", "recommended")


ACCOUNTS = ("you", "spouse", "joint")


@dataclass(frozen=True)
class Household:
    # (name, monthly budget, account, shared). account is "you" (your personal account), "spouse" or
    # "joint". shared: a personal-account cost that is also a family cost (it is added to the total owed
    # and credited to whoever pays it). Costs on the joint account are always shared. Only the budgeted
    # amount counts; spending above a personal budget is that person's own.
    expenses: Tuple[Tuple[str, float, str, bool], ...]
    split_you_percent: float                         # your share of the joint bills
    inheritance_balance: float                       # available at the start of start_month
    inheritance_monthly: float                       # applied against the total owed every month
    start_month: Optional[int]                       # None: month after your as-of date, else January
    spouse_scenario: str


def _num(v, field, lo, hi) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise ConfigError(f"{field}: enter a number from {lo:g} to {hi:g}")
    return float(v)


def _items(raw, field) -> Tuple[Tuple[str, float, str, bool], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise ConfigError(f"{field}: a list of up to {MAX_ITEMS} items")
    out = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            raise ConfigError(f"{field}[{i}]: expected name and monthly amount")
        name = it.get("name", "")
        if not isinstance(name, str) or len(name) > 60:
            raise ConfigError(f"{field}[{i}].name: text up to 60 characters")
        acct = it.get("account", "joint")
        if acct not in ACCOUNTS:
            raise ConfigError(f"{field}[{i}].account: 'you', 'spouse' or 'joint'")
        shared = it.get("shared", False)
        if not isinstance(shared, bool):
            raise ConfigError(f"{field}[{i}].shared: true or false")
        out.append((name, _num(it.get("monthly", 0), f"{field}[{i}].monthly", 0, 1e7), acct,
                    shared or acct == "joint"))
    return tuple(out)


def parse_household(raw) -> Household:
    if not isinstance(raw, dict):
        raise ConfigError("household: must be an object")
    start = raw.get("start_month")
    if start is not None:
        if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= 12:
            raise ConfigError("start_month: whole number 1 to 12")
    sc = raw.get("spouse_scenario", "current")
    if sc not in SPOUSE_SCENARIOS:
        raise ConfigError("spouse_scenario: 'current' or 'recommended'")
    return Household(
        _items(raw.get("expenses"), "expenses"),
        _num(raw.get("split_you_percent", 50), "split_you_percent", 0, 100),
        _num(raw.get("inheritance_balance", 0), "inheritance_balance", 0, 1e9),
        _num(raw.get("inheritance_monthly", 0), "inheritance_monthly", 0, 1e9),
        start, sc)


def _start_month(cfg: Config, hh: Household) -> int:
    if hh.start_month:
        return hh.start_month
    if cfg.as_of_date:
        return 12 if cfg.as_of_date.month == 12 else cfg.as_of_date.month + 1
    return 1


def _person_months(sc, start: int) -> dict:
    net = {m["month"]: m["net"] for m in monthly(sc)}
    n_regular = sum(1 for r in sc.result.rows if r.kind != "bonus" and r.pay_date.month >= start)
    return net, n_regular


def _run(you_sc, sp_sc, hh: Household, start: int) -> dict:
    """Per month: total family cost T = joint bills + shared costs paid from personal accounts.
    The planned inheritance offsets T; the rest is split by your share, and each person is credited
    for the shared costs they already paid. What a person's pay cannot cover of what they owe is then
    drawn from the remaining inheritance; their own personal-account costs above their pay are not."""
    you_net, you_n = _person_months(you_sc, start)
    sp_net, sp_n = _person_months(sp_sc, start)
    exp = hh.expenses
    cost = {k: sum(a for _, a, acct, _ in exp if acct == k) for k in ACCOUNTS}
    shared = {k: sum(a for _, a, acct, sh in exp if acct == k and sh) for k in ("you", "spouse")}
    joint = cost["joint"]
    total = joint + shared["you"] + shared["spouse"]
    pct = hh.split_you_percent / 100
    balance = hh.inheritance_balance
    rows = []
    tot = dict(you_from_pay=0.0, spouse_from_pay=0.0, inheritance_planned=0.0, inheritance_gap=0.0,
               unfunded=0.0, you_own_shortfall=0.0, spouse_own_shortfall=0.0)
    for m in range(start, 13):
        planned = min(balance, hh.inheritance_monthly, total)
        balance -= planned
        remaining = total - planned
        owes = {"you": remaining * pct - shared["you"], "spouse": remaining * (1 - pct) - shared["spouse"]}
        for k, o in (("you", "spouse"), ("spouse", "you")):  # someone who paid more than their share is repaid
            if owes[k] < 0:                                   # by the other, so the joint account still gets its total
                owes[o] += owes[k]
                owes[k] = 0.0
        row = dict(month=m, joint_expenses=joint, total_cost=total, inheritance_planned=planned)
        gaps = 0.0
        for who, nets in (("you", you_net), ("spouse", sp_net)):
            net = nets.get(m, 0.0)
            avail = net - cost[who]            # pay left after everything on their own account
            owed = max(0.0, owes[who])
            from_pay = min(owed, max(0.0, avail))
            gap = owed - from_pay
            own = max(0.0, -avail)
            gaps += gap
            row.update({f"{who}_net": net, f"{who}_family_expenses": cost[who], f"{who}_available": avail,
                        f"{who}_share": owed, f"{who}_to_joint": from_pay, f"{who}_shortfall": gap,
                        f"{who}_own_shortfall": own, f"{who}_left_over": max(0.0, avail - from_pay)})
            tot[f"{who}_from_pay"] += from_pay
            tot[f"{who}_own_shortfall"] += own
        draw = min(balance, gaps)
        balance -= draw
        row.update(inheritance_gap=draw, inheritance_used=planned + draw, inheritance_balance=balance,
                   unfunded=gaps - draw)
        tot["inheritance_planned"] += planned
        tot["inheritance_gap"] += draw
        tot["unfunded"] += gaps - draw
        rows.append(row)
    tot["inheritance_used"] = tot["inheritance_planned"] + tot["inheritance_gap"]
    tot["inheritance_end"] = balance
    tot["you_per_paycheck_avg"] = tot["you_from_pay"] / you_n if you_n else 0.0
    tot["spouse_per_paycheck_avg"] = tot["spouse_from_pay"] / sp_n if sp_n else 0.0
    return {"months": rows, "totals": tot}


def _round(o):
    if isinstance(o, float):
        return round(o, 2)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def analyze_household(you_cfg: Config, spouse_cfg: Config, hh: Household) -> dict:
    if you_cfg.tax_year != spouse_cfg.tax_year:
        raise ConfigError(f"both people must use the same tax_year (you {you_cfg.tax_year}, "
                          f"spouse {spouse_cfg.tax_year})")
    you: Analysis = analyze(you_cfg)
    sp: Analysis = analyze(spouse_cfg)
    sp_sc = getattr(sp, hh.spouse_scenario)
    start = _start_month(you_cfg, hh)
    warnings: List[str] = []
    warnings.append(
        "Each person's taxes are estimated on their own. Month-by-month cash uses what payroll withholds, "
        "so it is not affected, but each person's refund or amount owed is not your joint return.")
    if you_cfg.filing_status != spouse_cfg.filing_status:
        warnings.append(f"Filing statuses differ (you: {you_cfg.filing_status}, spouse: {spouse_cfg.filing_status}).")
    if hh.inheritance_balance <= 0:
        warnings.append("Inheritance balance is 0, so any shortfall shows as unfunded.")
    scenarios = []
    pairs = [("current", "Your current elections", you.current),
             ("recommended", "Your recommended elections", you.recommended)]
    if you.what_if is not None:
        pairs.append(("what_if", "Your what-if elections", you.what_if))
    for key, label, sc in pairs:
        r = _run(sc, sp_sc, hh, start)
        r.update(key=key, label=label, you_total_additions=sc.result.total_additions,
                 you_limit_reached_on=str(sc.result.limit_reached_on) if sc.result.limit_reached_on else None,
                 you_pretax_percent=sc.plan.pretax_percent, you_posttax_percent=sc.plan.posttax_percent)
        scenarios.append(r)
    if any(sc["totals"]["you_own_shortfall"] > 0.005 or sc["totals"]["spouse_own_shortfall"] > 0.005
           for sc in scenarios):
        warnings.append("In some months a person's pay does not cover the costs assigned to their own account. "
                        "That gap is theirs and is not paid from the joint account or the inheritance.")
    warnings.extend(w for w in you.warnings if "stand-in" in w)
    return _round({"meta": {"tax_year": you_cfg.tax_year, "start_month": start,
                            "split_you_percent": hh.split_you_percent,
                            "joint_expenses": sum(a for _, a, acct, _ in hh.expenses if acct == "joint"),
                            "inheritance_monthly": hh.inheritance_monthly,
                            "inheritance_start": hh.inheritance_balance,
                            "spouse_scenario": hh.spouse_scenario,
                            "spouse_total_additions": sp_sc.result.total_additions},
                   "scenarios": scenarios, "warnings": warnings})

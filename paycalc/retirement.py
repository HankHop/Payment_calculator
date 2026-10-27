"""Retirement projection in today's dollars, for a household, with income-reset scenarios.

Everything is real (after inflation): returns are real returns, salaries and spending are in
today's dollars, and IRS limits are held constant in real terms. Taxes are two effective rates
you set (on earned income, and on pretax withdrawals). This is a planning sketch, not a tax return.
"""
from __future__ import annotations

import copy
from typing import List, Optional

from .config import ConfigError

MAX_STEPS = 20
MAX_SCENARIOS = 8
ACCOUNT_TYPES = ("pretax", "roth", "taxable")


def _num(v, field, lo, hi, default=None) -> float:
    if v is None and default is not None:
        return float(default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise ConfigError(f"{field}: enter a number from {lo:g} to {hi:g}")
    return float(v)


def _age(v, field, lo=0, hi=110, default=None) -> int:
    if v is None and default is not None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not lo <= v <= hi:
        raise ConfigError(f"{field}: whole number of years from {lo} to {hi}")
    return int(v)


def _list(v, field, limit):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > limit:
        raise ConfigError(f"{field}: a list of up to {limit} items")
    return v


def _steps(raw, field) -> List[dict]:
    out = []
    for i, s in enumerate(_list(raw, field, MAX_STEPS)):
        f = f"{field}[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{f}: expected an object")
        out.append({
            "age": _age(s.get("age"), f"{f}.age"),
            "salary": _num(s.get("salary", 0), f"{f}.salary", 0, 1e8),
            "pretax_percent": _num(s.get("pretax_percent", 0), f"{f}.pretax_percent", 0, 100),
            "posttax_percent": _num(s.get("posttax_percent", 0), f"{f}.posttax_percent", 0, 100),
            "employer_percent": _num(s.get("employer_percent", 0), f"{f}.employer_percent", 0, 100),
            "other_savings": _num(s.get("other_savings", 0), f"{f}.other_savings", 0, 1e8)})
    return sorted(out, key=lambda x: x["age"])


def _spending(raw, field) -> List[dict]:
    out = []
    for i, s in enumerate(_list(raw, field, MAX_STEPS)):
        f = f"{field}[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{f}: expected an object")
        out.append({"age": _age(s.get("age"), f"{f}.age"), "annual": _num(s.get("annual", 0), f"{f}.annual", 0, 1e8)})
    return sorted(out, key=lambda x: x["age"])


def _person(raw, field, base=False) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field}: expected an object")
    stop = raw.get("stop_age")
    return {"steps": _steps(raw.get("steps"), f"{field}.steps"),
            "stop_age": None if stop is None else _age(stop, f"{field}.stop_age", 18, 110),
            "ss_claim_age": _age(raw.get("ss_claim_age"), f"{field}.ss_claim_age", 50, 80, default=67),
            "ss_annual": _num(raw.get("ss_annual", 0), f"{field}.ss_annual", 0, 1e7)}


def parse_plan(raw) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError("retirement plan: must be an object")
    you_age = _age(raw.get("you_age"), "you_age", 18, 100)
    sp_age = raw.get("spouse_age")
    sp_age = None if sp_age is None else _age(sp_age, "spouse_age", 18, 100)
    end_age = _age(raw.get("end_age"), "end_age", you_age + 1, 110, default=max(95, you_age + 1))
    target = _age(raw.get("target_retire_age"), "target_retire_age", you_age, end_age - 1, default=min(max(55, you_age), end_age - 1))
    accounts = []
    for i, a in enumerate(_list(raw.get("accounts"), "accounts", 30)):
        f = f"accounts[{i}]"
        if not isinstance(a, dict) or a.get("type") not in ACCOUNT_TYPES:
            raise ConfigError(f"{f}: needs a type of pretax, roth or taxable")
        name = a.get("name", "")
        if not isinstance(name, str) or len(name) > 60:
            raise ConfigError(f"{f}.name: text up to 60 characters")
        accounts.append({"name": name, "type": a["type"], "balance": _num(a.get("balance", 0), f"{f}.balance", 0, 1e10)})
    spending = _spending(raw.get("spending"), "spending")
    if not spending:
        raise ConfigError("spending: add at least one yearly amount")
    invest = raw.get("invest_surplus", True)
    if not isinstance(invest, bool):
        raise ConfigError("invest_surplus: true or false")
    you = _person(raw.get("you"), "you") or {"steps": [], "stop_age": None, "ss_claim_age": 67, "ss_annual": 0.0}
    spouse = _person(raw.get("spouse"), "spouse")
    if sp_age is None:
        spouse = None
    elif spouse is None:
        spouse = {"steps": [], "stop_age": None, "ss_claim_age": 67, "ss_annual": 0.0}
    scenarios = []
    for i, s in enumerate(_list(raw.get("scenarios"), "scenarios", MAX_SCENARIOS)):
        f = f"scenarios[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{f}: expected an object")
        name = s.get("name", "")
        if not isinstance(name, str) or len(name) > 60:
            raise ConfigError(f"{f}.name: text up to 60 characters")
        scenarios.append({"name": name or f"Scenario {i + 1}",
                          "you": _person(s.get("you"), f"{f}.you"), "spouse": _person(s.get("spouse"), f"{f}.spouse"),
                          "spending": _spending(s.get("spending"), f"{f}.spending")})
    return {
        "you_age": you_age, "spouse_age": sp_age, "end_age": end_age, "target_retire_age": target,
        "real_return_percent": _num(raw.get("real_return_percent"), "real_return_percent", -10, 30, default=4),
        "salary_growth_percent": _num(raw.get("salary_growth_percent"), "salary_growth_percent", -20, 20, default=0),
        "swr_percent": _num(raw.get("swr_percent"), "swr_percent", 0.5, 20, default=4),
        "tax_earned_percent": _num(raw.get("tax_earned_percent"), "tax_earned_percent", 0, 80, default=20),
        "tax_pretax_withdraw_percent": _num(raw.get("tax_pretax_withdraw_percent"), "tax_pretax_withdraw_percent", 0, 80, default=15),
        "elective_limit": _num(raw.get("elective_limit"), "elective_limit", 0, 1e7, default=24500),
        "additions_limit": _num(raw.get("additions_limit"), "additions_limit", 0, 1e7, default=72000),
        "barista_until_age": _age(raw.get("barista_until_age"), "barista_until_age", 18, 110, default=65),
        "invest_surplus": invest, "accounts": accounts, "spending": spending,
        "you": you, "spouse": spouse, "scenarios": scenarios}


def _override_steps(base: List[dict], over: List[dict]) -> List[dict]:
    if not over:
        return base
    first = min(s["age"] for s in over)
    return sorted([s for s in base if s["age"] < first] + over, key=lambda x: x["age"])


def effective(plan: dict, scen: Optional[dict]) -> dict:
    """The people and spending a scenario actually uses: its steps replace the base steps from the
    scenario's first step age onward."""
    eff = {"you": copy.deepcopy(plan["you"]), "spouse": copy.deepcopy(plan["spouse"]),
           "spending": plan["spending"]}
    if scen:
        for who in ("you", "spouse"):
            o = scen.get(who)
            if o and eff[who] is not None:
                eff[who]["steps"] = _override_steps(eff[who]["steps"], o["steps"])
                if o["stop_age"] is not None:
                    eff[who]["stop_age"] = o["stop_age"]
                if o["ss_annual"]:
                    eff[who]["ss_annual"], eff[who]["ss_claim_age"] = o["ss_annual"], o["ss_claim_age"]
        eff["spending"] = _override_steps_spend(plan["spending"], scen["spending"])
    return eff


def _override_steps_spend(base, over):
    if not over:
        return base
    first = min(s["age"] for s in over)
    return sorted([s for s in base if s["age"] < first] + over, key=lambda x: x["age"])


def _at(steps: List[dict], age: int):
    cur = None
    for s in steps:
        if s["age"] <= age:
            cur = s
    return cur


def simulate(plan: dict, eff: dict, extra_income: float = 0.0):
    """Year by year from today to end_age. Withdrawals come at the start of a year (taxable, then
    pretax grossed up for tax, then Roth); contributions earn half a year of return."""
    r = plan["real_return_percent"] / 100
    g = plan["salary_growth_percent"] / 100
    te, tw = plan["tax_earned_percent"] / 100, plan["tax_pretax_withdraw_percent"] / 100
    b = {k: sum(a["balance"] for a in plan["accounts"] if a["type"] == k) for k in ACCOUNT_TYPES}
    rows, depleted = [], None
    for t in range(plan["end_age"] - plan["you_age"]):
        ay = plan["you_age"] + t
        income = pre = post = emp = other = ss = 0.0
        for who, base_age in (("you", plan["you_age"]), ("spouse", plan["spouse_age"])):
            p = eff[who]
            if p is None or base_age is None:
                continue
            age = base_age + t
            if p["ss_annual"] and age >= p["ss_claim_age"]:
                ss += p["ss_annual"]
            if p["stop_age"] is not None and age >= p["stop_age"]:
                continue
            st = _at(p["steps"], age)
            if not st:
                continue
            sal = st["salary"] * (1 + g) ** t
            income += sal
            e_pre = min(sal * st["pretax_percent"] / 100, plan["elective_limit"])
            e_emp = min(sal * st["employer_percent"] / 100, max(0.0, plan["additions_limit"] - e_pre))
            e_post = min(sal * st["posttax_percent"] / 100, max(0.0, plan["additions_limit"] - e_pre - e_emp))
            pre += e_pre; emp += e_emp; post += e_post; other += st["other_savings"]
        sp = _at(eff["spending"], ay) or eff["spending"][0]
        spend = sp["annual"]
        extra = extra_income if plan["target_retire_age"] <= ay < plan["barista_until_age"] else 0.0
        cash = income * (1 - te) - pre - post - other + ss + extra
        need = spend - cash
        start_total = sum(b.values())
        withdrawal = short = surplus = 0.0
        if need > 0:
            take = need
            x = min(b["taxable"], take); b["taxable"] -= x; take -= x
            if take > 1e-9:
                x = min(b["pretax"], take / (1 - tw) if tw < 1 else 0.0)
                b["pretax"] -= x; take -= x * (1 - tw)
            if take > 1e-9:
                x = min(b["roth"], take); b["roth"] -= x; take -= x
            short = max(0.0, take)
            withdrawal = need - short
        elif plan["invest_surplus"]:
            surplus = -need
        if short > 0.5 and depleted is None:
            depleted = ay
        b["pretax"] = b["pretax"] * (1 + r) + (pre + emp) * (1 + r / 2)
        b["roth"] = b["roth"] * (1 + r) + post * (1 + r / 2)
        b["taxable"] = b["taxable"] * (1 + r) + (other + surplus) * (1 + r / 2)
        rows.append({"age": ay, "spouse_age": (plan["spouse_age"] + t) if plan["spouse_age"] is not None else None,
                     "start_balance": start_total, "income": income, "contributions": pre + post + emp + other,
                     "spending": spend, "social_security": ss, "withdrawal": withdrawal, "shortfall": short,
                     "balance": sum(b.values()), "pretax": b["pretax"], "roth": b["roth"], "taxable": b["taxable"]})
    return rows, depleted


def _metrics(plan: dict, eff: dict, rows: list, depleted) -> dict:
    r = plan["real_return_percent"] / 100
    target = plan["target_retire_age"]
    row_t = next(x for x in rows if x["age"] == target)
    swr = plan["swr_percent"] / 100
    fire = row_t["spending"] / swr
    coast_age = next((x["age"] for x in rows if x["age"] <= target
                      and x["start_balance"] >= fire / (1 + r) ** (target - x["age"])), None)
    barista = 0.0
    if depleted is not None:
        spend = row_t["spending"]
        _, d_full = simulate(plan, eff, spend)
        if d_full is not None:
            barista = None
        else:
            lo, hi = 0.0, spend
            for _ in range(40):
                mid = (lo + hi) / 2
                if simulate(plan, eff, mid)[1] is None:
                    hi = mid
                else:
                    lo = mid
            barista = hi
    return {"balance_at_target": row_t["start_balance"], "fire_number": fire,
            "funded_percent": row_t["start_balance"] / fire * 100 if fire else None,
            "coast_number_today": fire / (1 + r) ** (target - plan["you_age"]),
            "coast_age": coast_age, "depletion_age": depleted, "ending_balance": rows[-1]["balance"],
            "barista_income_needed": barista, "spending_at_target": row_t["spending"]}


def _round(o):
    if isinstance(o, float):
        return round(o, 2)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def analyze_plan(plan: dict) -> dict:
    out = []
    for name, scen in [("Baseline", None)] + [(s["name"], s) for s in plan["scenarios"]]:
        eff = effective(plan, scen)
        rows, dep = simulate(plan, eff)
        out.append({"name": name, "metrics": _metrics(plan, eff, rows, dep),
                    "rows": [{k: x[k] for k in ("age", "spouse_age", "balance", "income", "contributions", "spending",
                                                "social_security", "withdrawal", "shortfall", "pretax", "roth", "taxable")}
                             for x in rows]})
    start = sum(a["balance"] for a in plan["accounts"])
    warnings = ["Everything is in today's dollars: use a real (after-inflation) return. Taxes are the two effective "
                "rates you set, not a tax calculation. Early withdrawals before 59 and a half, Roth conversion "
                "rules, required distributions and taxable-account gains are not modeled."]
    if start <= 0:
        warnings.append("No starting balances entered: add your accounts to see a realistic result.")
    if all(x["annual"] <= 0 for x in plan["spending"]):
        warnings.append("Spending is 0, so every scenario looks fully funded. Enter your yearly spending.")
    if plan["swr_percent"] > 5:
        warnings.append("A withdrawal rate above 5% is aggressive for a long retirement.")
    return _round({"meta": {"you_age": plan["you_age"], "end_age": plan["end_age"], "target_retire_age": plan["target_retire_age"],
                            "starting_balance": start, "real_return_percent": plan["real_return_percent"],
                            "swr_percent": plan["swr_percent"], "spouse": plan["spouse_age"] is not None},
                   "scenarios": out, "warnings": warnings})

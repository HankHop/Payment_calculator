"""Text and JSON output."""
from __future__ import annotations

from typing import List

from .engine import Analysis, Scenario


def _m(x: float) -> str:
    return f"${x:,.2f}"


def _p(x: float) -> str:
    return f"{x:g}%"


def _rows(a: Analysis):
    cur, rec = a.current, a.recommended
    post = "After-tax" if a.cfg.contributions.posttax_type == "after_tax" else "Roth"
    show_post = a.cfg.contributions.after_tax_enabled

    def row(label, f, fmt=_m):
        return (label, fmt(f(cur)), fmt(f(rec)))

    return [
        ("PAY", "", ""),
        row("Gross pay", lambda s: s.result.gross),
        *([row("HSA (payroll)", lambda s: s.result.hsa)] if cur.result.hsa else []),
        *([row("Other pretax deductions", lambda s: s.result.other_pretax)] if cur.result.other_pretax else []),
        *([row("Other post-tax deductions", lambda s: s.result.other_posttax)] if cur.result.other_posttax else []),
        ("CONTRIBUTIONS", "", ""),
        row("Pretax percent (going forward)", lambda s: s.plan.pretax_percent, _p),
        *([row(f"{post} percent (going forward)", lambda s: s.plan.posttax_percent, _p)] if show_post else []),
        row("Pretax contributions", lambda s: s.result.pretax),
        *([row(f"{post} contributions", lambda s: s.result.posttax)] if show_post else []),
        row("Employee total", lambda s: s.result.employee),
        row("Employer match", lambda s: s.result.match),
        row("Employer unmatched", lambda s: s.result.nonelective),
        *([row("Total additions (415(c))", lambda s: s.result.total_additions)] if show_post else []),
        row("Unused elective limit", lambda s: max(0.0, s.plan.employee_limit - s.result.elective)),
        *([row("Unused 415(c) room", lambda s: max(0.0, s.plan.total_cap - s.result.total_additions))] if show_post else []),
        ("ESTIMATED TAXES", "", ""),
        row("Federal taxable income", lambda s: s.tax.federal_taxable),
        row("Federal income tax", lambda s: s.tax.federal),
        row(f"State income tax ({a.state_name})", lambda s: s.tax.state),
        row("Social Security", lambda s: s.tax.social_security),
        row("Medicare", lambda s: s.tax.medicare),
        row("Total taxes", lambda s: s.tax.total),
        row("Effective tax rate", lambda s: s.tax.total / s.result.gross * 100, lambda v: f"{v:.2f}%"),
        row("Marginal rate (fed + state)", lambda s: (s.tax.marginal_federal + s.tax.marginal_state) * 100,
            lambda v: f"{v:.2f}%"),
        ("TAKE-HOME", "", ""),
        row("Net pay for the year", lambda s: s.net_pay),
    ]


def _election_lines(sc: Scenario, label: str) -> List[str]:
    """Per paycheck: what the post-tax election asks for, what pay is left to cover it after
    taxes, HSA, premiums and pretax 401(k), what was deducted, and what stays in take-home."""
    rows = [r for r in sc.result.rows if r.posttax_requested > 0.005]
    if not rows:
        return []
    out = ["", f"POST-TAX ELECTION VS. AVAILABLE PAY - {label}",
           f"  {'date':<12}{'elected':>11}{'available':>12}{'deducted':>11}{'left over':>11}  note"]
    for r in rows:
        avail = r.posttax_available or 0.0
        if r.posttax >= r.posttax_requested - 0.005:
            note = "full election met"
        elif avail < r.posttax_requested - 0.005:
            note = f"limited by take-home, {_m(r.posttax_requested - r.posttax)} of the election not deducted"
        else:
            note = "limited by the 415(c) total limit"
        if "bonus" in r.kind:
            note += "; includes bonus"
        out.append(f"  {str(r.pay_date):<12}{r.posttax_requested:>11,.2f}{avail:>12,.2f}"
                   f"{r.posttax:>11,.2f}{max(0.0, avail - r.posttax):>11,.2f}  {note}")
    out.append(f"  {'total':<12}{sum(r.posttax_requested for r in rows):>11,.2f}"
               f"{sum(r.posttax_available or 0.0 for r in rows):>12,.2f}"
               f"{sum(r.posttax for r in rows):>11,.2f}"
               f"{sum(max(0.0, (r.posttax_available or 0.0) - r.posttax) for r in rows):>11,.2f}")
    return out


def monthly(sc: Scenario, expenses=None) -> list:
    """Per calendar month (by pay date): where each paycheck goes and what lands in the bank.
    Taxes are allocated by gross at the year's average rate; the tax saved by pretax 401(k) is
    allocated by each month's share of pretax contributions."""
    r = sc.result
    rate = sc.tax.total / r.gross if r.gross else 0.0
    saved_total = max(0.0, sc.tax_without_pretax - sc.tax.total)
    out = {}
    for x in r.rows:
        m = out.setdefault(x.pay_date.month, dict(month=x.pay_date.month, gross=0.0, taxes=0.0, other=0.0,
                                                 pretax=0.0, posttax=0.0, tax_saved=0.0))
        m["gross"] += x.gross
        m["taxes"] += rate * x.gross
        m["other"] += x.hsa + x.other_pretax + x.other_posttax
        m["pretax"] += x.pretax
        m["posttax"] += x.posttax
        m["tax_saved"] += saved_total * x.pretax / r.pretax if r.pretax else 0.0
    rows = []
    for k in sorted(out):
        m = out[k]
        m["net"] = m["gross"] - m["taxes"] - m["other"] - m["pretax"] - m["posttax"]
        m["pretax_cost"] = m["pretax"] - m["tax_saved"]       # what pretax actually takes from take-home
        m["total_cost"] = m["pretax_cost"] + m["posttax"]
        if expenses is not None:
            m["from_savings"] = max(0.0, expenses - m["net"])
        rows.append(m)
    return rows


_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _monthly_lines(sc: Scenario, label: str, expenses, show_post: bool) -> List[str]:
    rows = monthly(sc, expenses)
    cols = [("Gross", "gross"), ("Taxes", "taxes"), ("HSA/prem/other", "other"), ("Pretax 401k", "pretax")]
    if show_post:
        cols.append(("Post-tax", "posttax"))
    cols += [("Net pay", "net"), ("Pretax cost*", "pretax_cost")]
    if show_post:
        cols.append(("Total cost*", "total_cost"))
    if expenses is not None:
        cols.append(("From savings", "from_savings"))
    out = ["", f"MONTHLY CASH FLOW - {label}",
           "  " + f"{'':<6}" + "".join(f"{h:>15}" for h, _ in cols)]
    for m in rows:
        out.append("  " + f"{_MONTHS[m['month']]:<6}" + "".join(f"{m[k]:>15,.2f}" for _, k in cols))
    out.append("  " + f"{'Total':<6}" + "".join(f"{sum(m[k] for m in rows):>15,.2f}" for _, k in cols))
    out.append("  * Cost = how much your take-home drops because of the contribution. Pretax is cheaper "
               "than its face amount because it lowers taxes"
               + ("; post-tax costs its full amount." if show_post else "."))
    if expenses is not None:
        out.append(f"  From savings = monthly expenses ({_m(expenses)}) minus net pay, when net pay falls short.")
    return out


def text(a: Analysis, schedule: bool = False) -> str:
    cfg = a.cfg
    out: List[str] = []
    out.append(f"{cfg.tax_year} estimate | filing status: {cfg.filing_status} | state: {a.state_name}")
    out.append(f"Base salary {_m(cfg.base_salary)}, paid {cfg.frequency} ({sum(1 for e in a.events if e.kind != 'bonus')} paychecks)")
    if cfg.raise_:
        out.append(f"Raise to {_m(cfg.raise_.new_salary)} effective {cfg.raise_.effective_date}")
    if cfg.bonus:
        paid_with = ("with the regular paycheck" if any(e.kind == "regular+bonus" for e in a.events)
                     else "as its own paycheck")
        out.append(f"Bonus {_m(sum(e.bonus_amount for e in a.events))} paid {cfg.bonus.pay_date} ({paid_with}); "
                   f"employee contributions on bonus: {'yes' if cfg.contributions.bonus_eligible_for_employee_contributions else 'no'}, "
                   f"match: {'yes' if cfg.contributions.employer.bonus_eligible_for_match else 'no'}, "
                   f"unmatched: {'yes' if cfg.contributions.employer.bonus_eligible_for_nonelective else 'no'}")
    pt = "after-tax" if cfg.contributions.posttax_type == "after_tax" else "Roth"
    show_post = cfg.contributions.after_tax_enabled
    for el in cfg.contributions.elections:
        extra = f", {pt} {_p(el.posttax_percent)}" if show_post else ""
        out.append(f"Election from {el.effective_date}: pretax {_p(el.pretax_percent)}{extra}")
    if cfg.as_of_date:
        out.append(f"As of {cfg.as_of_date}: recommendation applies to later paychecks only")
    out.append("")

    rows = _rows(a)
    w0 = max(len(r[0]) for r in rows) + 2
    w1 = max(len(r[1]) for r in rows + [("", "Current", "")]) + 2
    out.append(f"{'':<{w0}}{'Current':>{w1}}{'Recommended':>{w1 + 2}}")
    for label, c, r in rows:
        if not c and not r:
            out.append("")
            out.append(label)
        else:
            out.append(f"  {label:<{w0 - 2}}{c:>{w1}}{r:>{w1 + 2}}")
    if show_post:
        out.extend(_election_lines(a.current, "Current elections"))
        out.extend(_election_lines(a.recommended, "Recommended elections"))
    out.extend(_monthly_lines(a.current, "Current", cfg.monthly_expenses, show_post))
    out.extend(_monthly_lines(a.recommended, "Recommended", cfg.monthly_expenses, show_post))
    out.append("")

    rec = a.recommendation
    since = f" from the paycheck on {rec.effective_from}" if rec.effective_from else ""
    out.append(f"Recommendation{since}: {_p(rec.pretax_percent)} pretax"
               + (f" + {_p(rec.posttax_percent)} {pt}" if show_post else "") +
               f" of eligible pay (elective target {_m(rec.target_dollars)}"
               + (f"; retirement marginal rate assumed {cfg.recommendation.expected_retirement_marginal_rate_percent:g}%"
                  if cfg.contributions.posttax_type == "roth" else "") + ").")
    if rec.result.limit_reached_on:
        out.append(f"Elective limit reached on {rec.result.limit_reached_on}.")

    if a.warnings:
        out.append("")
        out.append("Notes:")
        out.extend(f"  - {w}" for w in a.warnings)

    if schedule:
        for sc in (a.current, a.recommended):
            out.append("")
            out.append(f"{sc.label} - by pay date")
            post_h = f"{'post-tax':>11}" if show_post else ""
            out.append(f"  {'date':<12}{'type':<9}{'gross':>12}{'pretax':>11}{post_h}{'match':>10}{'unmatched':>11}")
            for r in sc.result.rows:
                post_c = f"{r.posttax:>11,.2f}" if show_post else ""
                out.append(f"  {str(r.pay_date):<12}{r.kind:<9}{r.gross:>12,.2f}{r.pretax:>11,.2f}"
                           f"{post_c}{r.match:>10,.2f}{r.nonelective:>11,.2f}")
    return "\n".join(out)


def as_dict(a: Analysis) -> dict:
    def sc(s: Scenario):
        r, t = s.result, s.tax
        return {
            "pretax_percent": s.plan.pretax_percent, "posttax_percent": s.plan.posttax_percent,
            "total_additions": round(r.total_additions, 2),
            "gross": round(r.gross, 2), "pretax": round(r.pretax, 2), "posttax": round(r.posttax, 2),
            "employer_match": round(r.match, 2), "employer_unmatched": round(r.nonelective, 2),
            "federal_taxable_income": round(t.federal_taxable, 2),
            "federal_tax": round(t.federal, 2), "state_tax": round(t.state, 2),
            "social_security": round(t.social_security, 2), "medicare": round(t.medicare, 2),
            "total_taxes": round(t.total, 2), "net_pay": round(s.net_pay, 2),
            "limit_reached_on": str(r.limit_reached_on) if r.limit_reached_on else None,
            "monthly": [{k: (round(v, 2) if k != "month" else v) for k, v in m.items()}
                        for m in monthly(s, a.cfg.monthly_expenses)],
            "posttax_by_paycheck": [
                {"date": str(x.pay_date), "kind": x.kind, "elected": round(x.posttax_requested, 2),
                 "available": round(x.posttax_available or 0.0, 2), "deducted": round(x.posttax, 2),
                 "left_over": round(max(0.0, (x.posttax_available or 0.0) - x.posttax), 2)}
                for x in r.rows if x.posttax_requested > 0.005],
        }
    return {"current": sc(a.current), "recommended": sc(a.recommended), "warnings": a.warnings}

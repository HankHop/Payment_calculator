"""Text and JSON output."""
from __future__ import annotations

from typing import List

from .engine import Analysis, Scenario
from .schedule import resolved_bonuses


def _m(x: float) -> str:
    return f"${x:,.2f}"


def _p(x: float) -> str:
    return f"{x:g}%"


def _rows(a: Analysis):
    cur, rec = a.current, a.recommended
    post = "After-tax" if a.cfg.contributions.posttax_type == "after_tax" else "Roth"
    show_post = a.cfg.contributions.after_tax_enabled

    wi = a.what_if

    def row(label, f, fmt=_m):
        return (label, fmt(f(cur)), fmt(f(rec)), fmt(f(wi)) if wi else "")

    return [
        ("PAY", "", "", ""),
        row("Gross pay", lambda s: s.result.gross),
        *([row("HSA (payroll)", lambda s: s.result.hsa)] if cur.result.hsa else []),
        *([row("Other pretax deductions", lambda s: s.result.other_pretax)] if cur.result.other_pretax else []),
        *([row("Other post-tax deductions", lambda s: s.result.other_posttax)] if cur.result.other_posttax else []),
        ("CONTRIBUTIONS", "", "", ""),
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
        ("ESTIMATED TAXES", "", "", ""),
        row("Federal taxable income", lambda s: s.tax.federal_taxable),
        row("Federal income tax", lambda s: s.tax.federal),
        row(f"State income tax ({a.state_name})", lambda s: s.tax.state),
        row("Social Security", lambda s: s.tax.social_security),
        row("Medicare", lambda s: s.tax.medicare),
        row("Total taxes", lambda s: s.tax.total),
        row("Effective tax rate", lambda s: s.tax.total / s.result.gross * 100, lambda v: f"{v:.2f}%"),
        row("Marginal rate (fed + state)", lambda s: (s.tax.marginal_federal + s.tax.marginal_state) * 100,
            lambda v: f"{v:.2f}%"),
        ("WITHHOLDING (what payroll takes from each paycheck)", "", "", ""),
        row("Federal income tax withheld", lambda s: s.fed_withheld),
        *([row("  of which W-4 extra withholding", lambda s: s.extra_withheld)] if cur.extra_withheld else []),
        row("Projected federal refund (+) / owed (-)", lambda s: s.fed_balance),
        row("All taxes withheld (fed, FICA, state)", lambda s: s.withheld_total),
        ("TAKE-HOME", "", "", ""),
        row("Net pay for the year (paychecks)", lambda s: s.net_pay),
        row("Net pay once the year's tax is settled", lambda s: s.net_pay_settled),
    ]


def election_table(sc: Scenario) -> list:
    """Per paycheck: what the post-tax election asks for, what pay is left to cover it after
    taxes, HSA, premiums and pretax 401(k), what was deducted, what stays in take-home, and why."""
    out = []
    for r in sc.result.rows:
        if r.posttax_requested <= 0.005:
            continue
        avail = r.posttax_available or 0.0
        short = _m(r.posttax_requested - r.posttax)
        if r.posttax >= r.posttax_requested - 0.005:
            status, note = "full", "full election met"
        elif r.posttax < min(r.posttax_requested, avail) - 0.005:
            status, note = "limit", f"limited by the 415(c) total limit, {short} of the election not deducted"
        else:
            status, note = "takehome", f"limited by take-home, {short} of the election not deducted"
        if "bonus" in r.kind:
            note += f"; includes {r.label or 'bonus'}"
        out.append(dict(date=str(r.pay_date), kind=r.kind, elected=r.posttax_requested, available=avail,
                        deducted=r.posttax, left_over=max(0.0, avail - r.posttax), status=status, note=note))
    return out


def _election_lines(sc: Scenario, label: str) -> List[str]:
    rows = election_table(sc)
    if not rows:
        return []
    out = ["", f"POST-TAX ELECTION VS. AVAILABLE PAY - {label}",
           f"  {'date':<12}{'elected':>11}{'available':>12}{'deducted':>11}{'left over':>11}  note"]
    for r in rows:
        out.append(f"  {r['date']:<12}{r['elected']:>11,.2f}{r['available']:>12,.2f}"
                   f"{r['deducted']:>11,.2f}{r['left_over']:>11,.2f}  {r['note']}")
    out.append(f"  {'total':<12}{sum(r['elected'] for r in rows):>11,.2f}"
               f"{sum(r['available'] for r in rows):>12,.2f}{sum(r['deducted'] for r in rows):>11,.2f}"
               f"{sum(r['left_over'] for r in rows):>11,.2f}")
    return out


def monthly(sc: Scenario, expenses=None) -> list:
    """Per calendar month (by pay date): where each paycheck goes and what lands in the bank.
    Taxes are what payroll withholds from each paycheck (bonus paychecks included); the tax saved by
    pretax 401(k) is allocated by each month's share of pretax contributions."""
    r = sc.result
    saved_total = max(0.0, sc.tax_without_pretax - sc.tax.total)
    out = {}
    for x in r.rows:
        m = out.setdefault(x.pay_date.month, dict(month=x.pay_date.month, gross=0.0, taxes=0.0, other=0.0,
                                                 pretax=0.0, posttax=0.0, tax_saved=0.0))
        m["gross"] += x.gross
        m["taxes"] += x.withheld
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
    for b in resolved_bonuses(cfg):
        where = "with the regular paycheck" if b.with_regular else "as its own paycheck"
        out.append(f"Bonus ({b.name}) {_m(b.gross)} paid {b.pay_date} ({where}); "
                   f"employee contributions: {'yes' if b.employee else 'no'}, "
                   f"match: {'yes' if b.match else 'no'}, unmatched: {'yes' if b.nonelective else 'no'}")
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
    w1 = max(len(r[1]) for r in rows + [("", "Current", "", "")]) + 2
    wi_head = f"{'What-if':>{w1 + 2}}" if a.what_if else ""
    out.append(f"{'':<{w0}}{'Current':>{w1}}{'Recommended':>{w1 + 2}}{wi_head}")
    for label, c, r, wi in rows:
        if not c and not r:
            out.append("")
            out.append(label)
        else:
            out.append(f"  {label:<{w0 - 2}}{c:>{w1}}{r:>{w1 + 2}}" + (f"{wi:>{w1 + 2}}" if a.what_if else ""))
    if show_post:
        out.extend(_election_lines(a.current, "Current elections"))
        out.extend(_election_lines(a.recommended, "Recommended elections"))
        if a.what_if:
            out.extend(_election_lines(a.what_if, "What-if elections"))
    out.extend(_monthly_lines(a.current, "Current", cfg.monthly_expenses, show_post))
    out.extend(_monthly_lines(a.recommended, "Recommended", cfg.monthly_expenses, show_post))
    if a.what_if:
        out.extend(_monthly_lines(a.what_if, "What-if", cfg.monthly_expenses, show_post))
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
        for sc in (a.current, a.recommended, *([a.what_if] if a.what_if else [])):
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
    cfg = a.cfg
    show_post = cfg.contributions.after_tax_enabled

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
            "total_taxes": round(t.total, 2), "net_pay": round(s.net_pay, 2), "net_pay_settled": round(s.net_pay_settled, 2),
            "limit_reached_on": str(r.limit_reached_on) if r.limit_reached_on else None,
            "monthly": [{k: (round(v, 2) if k != "month" else v) for k, v in m.items()}
                        for m in monthly(s, cfg.monthly_expenses)],
            "posttax_by_paycheck": [
                {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
                for row in election_table(s)] if show_post else [],
            "paychecks": [
                {"date": str(x.pay_date), "kind": x.kind, "label": x.label, "gross": round(x.gross, 2),
                 "pretax": round(x.pretax, 2), "posttax": round(x.posttax, 2),
                 "match": round(x.match, 2), "unmatched": round(x.nonelective, 2),
                 "hsa": round(x.hsa, 2), "other_pretax": round(x.other_pretax, 2),
                 "other_posttax": round(x.other_posttax, 2),
                 "withheld": round(x.withheld, 2), "fed_withheld": round(x.fed_withheld, 2),
                 "extra_withheld": round(x.extra_withheld, 2)}
                for x in r.rows],
            "fed_withheld": round(s.fed_withheld, 2), "extra_withheld": round(s.extra_withheld, 2),
            "withheld_total": round(s.withheld_total, 2), "fed_balance": round(s.fed_balance, 2),
        }

    rec = a.recommendation
    return {
        "meta": {
            "tax_year": cfg.tax_year, "state": a.state_name, "filing_status": cfg.filing_status,
            "paychecks": sum(1 for e in a.events if e.kind != "bonus"),
            "after_tax_enabled": show_post, "posttax_type": cfg.contributions.posttax_type,
            "as_of_date": str(cfg.as_of_date) if cfg.as_of_date else None,
            "monthly_expenses": cfg.monthly_expenses,
            "raise": ({"effective_date": str(cfg.raise_.effective_date), "new_salary": round(cfg.raise_.new_salary, 2)}
                      if cfg.raise_ else None),
            "bonuses": [{"name": b.name, "date": str(b.pay_date), "gross": round(b.gross, 2),
                         "employee": b.employee, "match": b.match, "unmatched": b.nonelective,
                         "with_regular": b.with_regular} for b in resolved_bonuses(cfg)],
            "what_if": ({"effective_date": str(cfg.what_if.effective_date), "pretax": cfg.what_if.pretax_percent,
                         "posttax": cfg.what_if.posttax_percent if show_post else None} if cfg.what_if else None),
            "elections": [{"effective_date": str(e.effective_date), "pretax": e.pretax_percent,
                           "posttax": e.posttax_percent if show_post else None}
                          for e in cfg.contributions.elections],
        },
        "summary": [{"label": l, "current": c, "recommended": r, "what_if": wi, "section": not c and not r}
                    for l, c, r, wi in _rows(a)],
        "current": sc(a.current),
        "recommended": sc(a.recommended),
        "what_if": sc(a.what_if) if a.what_if else None,
        "recommendation": {
            "pretax_percent": rec.pretax_percent,
            "posttax_percent": rec.posttax_percent if show_post else None,
            "effective_from": str(rec.effective_from) if rec.effective_from else None,
            "elective_target": round(rec.target_dollars, 2),
            "elective_limit_reached_on": str(rec.result.limit_reached_on) if rec.result.limit_reached_on else None,
        },
        "warnings": a.warnings,
    }

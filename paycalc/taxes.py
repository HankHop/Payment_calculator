"""Annual federal income tax, FICA and state income tax for one wage earner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

Brackets = Sequence[Sequence[float]]  # [[threshold, rate], ...] ascending, rate applies above threshold


def bracket_tax(taxable: float, brackets: Brackets) -> float:
    if taxable <= 0:
        return 0.0
    total = 0.0
    for i, (lo, rate) in enumerate(brackets):
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        if taxable <= lo:
            break
        total += (min(taxable, hi) - lo) * rate
    return total


def marginal_rate(taxable: float, brackets: Brackets) -> float:
    """Rate on the next dollar at this income level (bracket with lo <= taxable < hi)."""
    if taxable < 0:
        return 0.0
    rate = 0.0
    for lo, r in brackets:
        if taxable >= lo:
            rate = r
        else:
            break
    return rate


# ---- federal ---------------------------------------------------------------

def federal_taxable(gross: float, pretax: float, status: str, fed: dict) -> float:
    return max(0.0, gross - pretax - fed["standard_deduction"][status])


def federal_income_tax(gross: float, pretax: float, status: str, fed: dict) -> float:
    return bracket_tax(federal_taxable(gross, pretax, status, fed), fed["brackets"][status])


def federal_marginal(gross: float, pretax: float, status: str, fed: dict) -> float:
    return marginal_rate(gross - pretax - fed["standard_deduction"][status], fed["brackets"][status])


# ---- FICA ------------------------------------------------------------------

def fica(wages: float, status: str, fed: dict) -> dict:
    f = fed["fica"]
    ss = min(wages, f["ss_wage_base"]) * f["ss_rate"]
    medicare = wages * f["medicare_rate"]
    addl = max(0.0, wages - f["addl_medicare_threshold"][status]) * f["addl_medicare_rate"]
    return {"social_security": ss, "medicare": medicare + addl}


# ---- state -----------------------------------------------------------------

def _pick(table: dict, status: str) -> list:
    key = "mfj" if status == "mfj" else "single"
    return table.get(key) or table["all"]


def _idx(status: str) -> int:
    return 1 if status == "mfj" else 0


def state_taxable(gross: float, pretax: float, status: str, st: dict) -> float:
    if st["type"] == "none":
        return 0.0
    i = _idx(status)
    wages = gross if st.get("taxes_401k_deferrals") else gross - pretax
    deduction = (st.get("std") or [0, 0])[i]
    if st.get("exempt_type") == "deduction":
        deduction += (st.get("exempt") or [0, 0])[i]
    return max(0.0, wages - deduction)


def state_income_tax(gross: float, pretax: float, status: str, st: dict) -> float:
    if st["type"] == "none":
        return 0.0
    tax = bracket_tax(state_taxable(gross, pretax, status, st), _pick(st["brackets"], status))
    if st.get("exempt_type") == "credit":
        tax = max(0.0, tax - (st.get("exempt") or [0, 0])[_idx(status)])
    return tax


def state_marginal(gross: float, pretax: float, status: str, st: dict) -> float:
    """Rate saved on the next pretax dollar. Zero for states that tax 401(k) deferrals."""
    if st["type"] == "none" or st.get("taxes_401k_deferrals"):
        return 0.0
    i = _idx(status)
    deduction = (st.get("std") or [0, 0])[i]
    if st.get("exempt_type") == "deduction":
        deduction += (st.get("exempt") or [0, 0])[i]
    return marginal_rate(gross - pretax - deduction, _pick(st["brackets"], status))


# ---- combined ---------------------------------------------------------------

@dataclass(frozen=True)
class TaxSummary:
    gross: float
    pretax: float
    federal_taxable: float
    federal: float
    state: float
    social_security: float
    medicare: float
    marginal_federal: float
    marginal_state: float

    @property
    def total(self) -> float:
        return self.federal + self.state + self.social_security + self.medicare


def summarize(gross: float, pretax: float, status: str, fed: dict, st: dict) -> TaxSummary:
    f = fica(gross, status, fed)
    return TaxSummary(
        gross=gross,
        pretax=pretax,
        federal_taxable=federal_taxable(gross, pretax, status, fed),
        federal=federal_income_tax(gross, pretax, status, fed),
        state=state_income_tax(gross, pretax, status, st),
        social_security=f["social_security"],
        medicare=f["medicare"],
        marginal_federal=federal_marginal(gross, pretax, status, fed),
        marginal_state=state_marginal(gross, pretax, status, st),
    )

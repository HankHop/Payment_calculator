"""What payroll withholds from each paycheck (cash), as opposed to the year's tax liability.

- Social Security and Medicare: exact, on pay after Section 125 deductions (HSA, premiums).
  Social Security stops at the wage base; Additional Medicare starts above 200,000 of wages.
- Federal income tax on bonus pay: flat supplemental rate (22%; 37% on supplemental wages above 1,000,000).
- Federal income tax on regular pay: a fixed amount if the user gave one (W-4 as seen on a stub), otherwise
  estimated by annualizing the paycheck and using the filing status brackets and standard deduction.
  With the W-4 "multiple jobs" box checked, the IRS tables work as if the pay were half of one of two equal
  jobs: tax = tax(2 x wages) / 2. (Checked against a real MFJ stub: the estimate matches to the cent.)
- W-4 extra withholding is added to every regular paycheck.
- State income tax: the year's estimated state tax spread over pay at its effective rate.
"""
from __future__ import annotations

from .config import FREQUENCIES, Config
from .schedule import PayEvent
from .taxes import bracket_tax, exclusions

SUPPLEMENTAL_HIGH_RATE = 0.37
SUPPLEMENTAL_THRESHOLD = 1_000_000.0
ADDL_MEDICARE_WITHHOLDING_THRESHOLD = 200_000.0


class Withholder:
    def __init__(self, cfg: Config, fed: dict, st: dict, events, state_rate: float = 0.0):
        self.cfg, self.fed, self.st = cfg, fed, st
        self.state_rate = state_rate
        self.periods = FREQUENCIES[cfg.frequency]
        w = cfg.withholding
        self.supp_rate = w.supplemental_rate_percent / 100
        self.changes = w.changes
        f = fed["fica"]
        self._fica, self._supp_low = [], []
        cum_wages = cum_supp = 0.0
        for ev in events:
            wages = max(0.0, ev.gross - ev.hsa - ev.other_pretax)
            ss = max(0.0, min(wages, f["ss_wage_base"] - cum_wages)) * f["ss_rate"]
            addl_base = max(0.0, cum_wages + wages - max(cum_wages, ADDL_MEDICARE_WITHHOLDING_THRESHOLD))
            med = wages * f["medicare_rate"] + addl_base * f["addl_medicare_rate"]
            self._fica.append((ss, med))
            cum_wages += wages
            low = max(0.0, min(ev.bonus_amount, SUPPLEMENTAL_THRESHOLD - cum_supp))
            self._supp_low.append(low)
            cum_supp += ev.bonus_amount

    def _change_on(self, d):
        cur = None
        for c in self.changes:
            if c.effective_date <= d:
                cur = c
        return cur

    def parts(self, i: int, ev: PayEvent, pre: float) -> dict:
        """Taxes withheld on paycheck i given the pretax 401(k) taken from it."""
        ss, med = self._fica[i]
        # pretax 401(k) attributable to the bonus part (bonus eligibility), the rest is regular pay
        reg_gross = ev.gross - ev.bonus_amount
        elig_bonus = max(0.0, ev.deferral_comp - reg_gross) if ev.deferral_comp > 0 else 0.0
        pre_bonus = pre * elig_bonus / ev.deferral_comp if ev.deferral_comp > 0 else 0.0
        pre_reg = pre - pre_bonus

        supp_base = max(0.0, ev.bonus_amount - pre_bonus)
        low = min(self._supp_low[i], supp_base)
        supplemental = low * self.supp_rate + (supp_base - low) * SUPPLEMENTAL_HIGH_RATE

        regular = extra = 0.0
        if ev.kind != "bonus":
            ch = self._change_on(ev.pay_date)
            if ch is not None and ch.regular_federal_per_paycheck is not None:
                regular = ch.regular_federal_per_paycheck
            else:
                wages = (reg_gross - ev.hsa - ev.other_pretax - pre_reg) * self.periods
                k = 2.0 if ch is not None and ch.multiple_jobs else 1.0
                taxable = k * wages - self.fed["standard_deduction"][self.cfg.filing_status]
                regular = bracket_tax(taxable, self.fed["brackets"][self.cfg.filing_status]) / k / self.periods
            extra = ch.extra_federal_per_paycheck if ch else 0.0

        sec125, state_excl = exclusions(ev.hsa, ev.other_pretax, self.st)
        state = self.state_rate * max(0.0, ev.gross - state_excl)
        return {"federal": regular + supplemental + extra, "extra": extra, "social_security": ss,
                "medicare": med, "state": state}

    def total(self, i: int, ev: PayEvent, pre: float) -> float:
        p = self.parts(i, ev, pre)
        return p["federal"] + p["social_security"] + p["medicare"] + p["state"]

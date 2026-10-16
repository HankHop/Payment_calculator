import unittest
from datetime import date

from paycalc import data
from paycalc.config import parse
from paycalc.engine import analyze
from paycalc.schedule import build_events
from paycalc.withholding import Withholder

FED = data.federal(2026)


def cfg(**over):
    raw = {
        "tax_year": 2026, "filing_status": "mfj", "state": "TX",
        "pay": {"base_salary": 131846.40, "frequency": "semimonthly",
                "bonuses": [{"name": "teamGM", "pay_date": "2026-02-28", "amount": 19539.63,
                             "eligible_for_employee_contributions": False,
                             "eligible_for_match": False, "eligible_for_nonelective": False}]},
        "contributions": {"elections": [{"effective_date": "2026-01-01", "pretax_percent": 19, "posttax_percent": 0}],
                          "employer": {}},
        "deductions": {"hsa_per_paycheck": 302.08, "other_pretax_per_paycheck": 91.87},
        "withholding": {"changes": [{"effective_date": "2026-01-01", "multiple_jobs": True}]},
    }
    raw.update(over)
    return parse(raw)


def rows(c):
    return {str(r.pay_date): r for r in analyze(c).current.result.rows}


class WithholdingTest(unittest.TestCase):
    def test_bonus_paycheck_matches_stub_fica_and_supplemental(self):
        r = rows(cfg())["2026-02-28"]
        # stub: SS 1,524.75 and Medicare 356.59 on gross - HSA - health care
        wages = r.gross - r.hsa - r.other_pretax
        self.assertAlmostEqual(wages * 0.062, 1524.75 - 0.0, delta=15)   # health care differs on the stub
        # flat 22% on the bonus is inside the federal figure
        self.assertGreater(r.fed_withheld, 19539.63 * 0.22)
        self.assertLess(r.fed_withheld, 19539.63 * 0.22 + 600)

    def test_regular_estimate_matches_stub_mfj_multiple_jobs(self):
        # 2/15 stub: federal taxable wages 4,153.52 -> 545.86 withheld (MFJ, multiple jobs box)
        c = cfg()
        w = Withholder(c, FED, data.state(2026, "TX"), build_events(c))
        ev = build_events(c)[0]
        from dataclasses import replace
        ev = replace(ev, gross=4153.52, hsa=0.0, other_pretax=0.0, bonus_amount=0.0, deferral_comp=4153.52)
        self.assertAlmostEqual(w.parts(0, ev, 0.0)["federal"], 545.86, places=2)

    def test_checkbox_off_withholds_less_for_mfj(self):
        on = rows(cfg())["2026-01-15"].fed_withheld
        off = rows(cfg(withholding={}))["2026-01-15"].fed_withheld
        self.assertLess(off, on)

    def test_extra_withholding_added_per_regular_paycheck_from_date(self):
        c = cfg(withholding={"changes": [
            {"effective_date": "2026-01-01", "multiple_jobs": True},
            {"effective_date": "2026-06-01", "multiple_jobs": True, "extra_federal_per_paycheck": 250}]})
        rs = rows(c)
        self.assertEqual(rs["2026-05-15"].extra_withheld, 0.0)
        self.assertEqual(rs["2026-06-15"].extra_withheld, 250.0)
        base = rows(cfg())["2026-06-15"].fed_withheld
        self.assertAlmostEqual(rs["2026-06-15"].fed_withheld, base + 250.0, places=2)
        a = analyze(c)
        self.assertAlmostEqual(a.current.extra_withheld, 250.0 * sum(1 for d in rs if d >= "2026-06-01"), places=2)

    def test_fixed_regular_withholding_overrides_estimate(self):
        c = cfg(withholding={"changes": [{"effective_date": "2026-01-01", "regular_federal_per_paycheck": 600}]})
        self.assertEqual(rows(c)["2026-01-15"].fed_withheld, 600.0)

    def test_refund_is_withheld_minus_liability(self):
        a = analyze(cfg())
        self.assertAlmostEqual(a.current.fed_balance, a.current.fed_withheld - a.current.tax.federal)

    def test_bad_withholding_input_rejected(self):
        from paycalc.config import ConfigError
        with self.assertRaises(ConfigError):
            cfg(withholding={"changes": [{"effective_date": "2026-01-01", "multiple_jobs": "yes"}]})
        with self.assertRaises(ConfigError):
            cfg(withholding={"changes": [{"effective_date": "2025-01-01"}]})

    def test_supplemental_rate_over_one_million_is_37_percent(self):
        c = cfg(pay={"base_salary": 100000, "frequency": "monthly",
                     "bonuses": [{"name": "big", "pay_date": "2026-03-31", "amount": 1500000}]})
        ev = [e for e in build_events(c) if e.pay_date == date(2026, 3, 31)][0]
        w = Withholder(c, FED, data.state(2026, "TX"), build_events(c))
        i = build_events(c).index(ev)
        fed = w.parts(i, ev, 0.0)["federal"]
        expected_supp = 1_000_000 * 0.22 + 500_000 * 0.37
        self.assertGreater(fed, expected_supp)
        self.assertLess(fed, expected_supp + 3000)


if __name__ == "__main__":
    unittest.main()

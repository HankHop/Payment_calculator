import unittest
from datetime import date

from paycalc import gui, report
from paycalc.config import ConfigError, parse
from paycalc.engine import analyze

BASE = {
    "tax_year": 2026, "filing_status": "mfj", "state": "TX", "as_of_date": "2026-10-31",
    "pay": {"base_salary": 139757.28, "frequency": "semimonthly"},
    "contributions": {
        "posttax_type": "after_tax", "acknowledge_mega_backdoor": True,
        "elections": [{"effective_date": "2026-01-01", "pretax_percent": 18, "posttax_percent": 0},
                      {"effective_date": "2026-08-15", "pretax_percent": 18, "posttax_percent": 82}],
        "employer": {"match_rate_percent": 100, "match_up_to_percent_of_pay": 6, "nonelective_percent": 4}},
    "withholding": {"changes": [{"effective_date": "2026-01-01", "multiple_jobs": True}]},
}


def raw(**wi):
    import copy
    r = copy.deepcopy(BASE)
    if wi:
        r["what_if"] = wi
    return r


class WhatIfTest(unittest.TestCase):
    def test_absent_by_default(self):
        a = analyze(parse(raw()))
        self.assertIsNone(a.what_if)
        self.assertIsNone(gui.run_analysis(raw())["what_if"])

    def test_change_applies_from_its_date_only(self):
        a = analyze(parse(raw(effective_date="2026-11-15", pretax_percent=17, posttax_percent=82)))
        rows = {str(r.pay_date): r for r in a.what_if.result.rows}
        self.assertAlmostEqual(rows["2026-10-31"].pretax, 0.18 * rows["2026-10-31"].gross, places=2)
        self.assertAlmostEqual(rows["2026-11-15"].pretax, 0.17 * rows["2026-11-15"].gross, places=2)

    def test_full_match_and_limit_with_17_percent(self):
        a = analyze(parse(raw(effective_date="2026-11-15", pretax_percent=17, posttax_percent=82)))
        r = a.what_if.result
        self.assertAlmostEqual(r.pretax, 24500, delta=1)
        late = [x for x in r.rows if x.pay_date >= date(2026, 10, 31)]
        self.assertTrue(all(abs(x.match - late[0].match) < 0.01 for x in late))

    def test_warns_when_limit_reached_early(self):
        a = analyze(parse(raw(effective_date="2026-10-31", pretax_percent=40, posttax_percent=0)))
        self.assertTrue(any(w.startswith("What-if: the elective limit is reached") for w in a.warnings))

    def test_replaces_later_elections(self):
        r = raw(effective_date="2026-09-01", pretax_percent=10, posttax_percent=0)
        r["contributions"]["elections"].append({"effective_date": "2026-10-31", "pretax_percent": 50, "posttax_percent": 0})
        a = analyze(parse(r))
        rows = {str(x.pay_date): x for x in a.what_if.result.rows}
        self.assertAlmostEqual(rows["2026-11-15"].pretax, 0.10 * rows["2026-11-15"].gross, places=2)

    def test_output_has_column_and_scenario(self):
        d = gui.run_analysis(raw(effective_date="2026-11-15", pretax_percent=17, posttax_percent=82))
        self.assertEqual(d["meta"]["what_if"]["pretax"], 17)
        self.assertIn("what_if", d["summary"][1])
        self.assertTrue(d["what_if"]["paychecks"])
        text = report.text(analyze(parse(raw(effective_date="2026-11-15", pretax_percent=17, posttax_percent=82))))
        self.assertIn("What-if", text)

    def test_validation(self):
        with self.assertRaises(ConfigError):
            parse(raw(effective_date="nope", pretax_percent=17))
        with self.assertRaises(ConfigError):
            parse(raw(effective_date="2026-11-15", pretax_percent=60, posttax_percent=60))
        with self.assertRaises(ConfigError):
            parse(raw(effective_date="2025-11-15", pretax_percent=10))


if __name__ == "__main__":
    unittest.main()

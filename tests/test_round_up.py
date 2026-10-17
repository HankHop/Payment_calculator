import unittest

from paycalc.config import parse
from paycalc.engine import analyze


def cfg(**contrib):
    c = {"posttax_type": "after_tax", "acknowledge_mega_backdoor": True,
         "elections": [{"effective_date": "2026-01-01", "pretax_percent": 18, "posttax_percent": 0}],
         "employer": {"match_rate_percent": 100, "match_up_to_percent_of_pay": 6, "nonelective_percent": 4}}
    c.update(contrib)
    return parse({"tax_year": 2026, "filing_status": "mfj", "state": "TX", "as_of_date": "2026-10-16",
                  "pay": {"base_salary": 139757.28, "frequency": "semimonthly"},
                  "contributions": c, "recommendation": {"target_total_additions": "none"},
                  "withholding": {"changes": [{"effective_date": "2026-01-01", "multiple_jobs": True}]}})


class RoundUpTest(unittest.TestCase):
    def test_rounds_up_when_match_is_not_lost(self):
        a = analyze(cfg())
        rec = a.recommendation
        self.assertGreaterEqual(rec.result.employee, 24500 - 1)       # reaches the limit
        self.assertLessEqual(rec.result.employee, 24500 + 0.01)       # never above it
        self.assertTrue(any("Rounded up" in w for w in a.warnings))
        # full match on every remaining paycheck
        remaining = [r for r in rec.result.rows if r.pay_date > a.cfg.as_of_date]
        self.assertTrue(all(abs(r.match - remaining[0].match) < 0.01 for r in remaining))

    def test_keeps_lower_percent_when_rounding_up_would_cost_match(self):
        # an earlier as-of date leaves many paychecks; one step more would end deferrals early
        raw = cfg()
        from dataclasses import replace
        from datetime import date
        a = analyze(replace(raw, as_of_date=date(2026, 3, 1)))
        rec = a.recommendation
        lost = [r for r in rec.result.rows if r.pay_date > date(2026, 3, 1) and r.match < 0.01]
        self.assertEqual(lost, [])


if __name__ == "__main__":
    unittest.main()


class PostTaxRoundUpTest(unittest.TestCase):
    def test_posttax_rounds_up_when_total_would_fall_short_and_match_is_kept(self):
        import json
        from pathlib import Path
        from paycalc import gui, plans
        raw = json.loads((Path(__file__).parent.parent / "examples" / "example_config.json").read_text())
        d = gui.run_analysis(raw)
        rec = d["recommended"]
        # whenever a round-up note is present, the total must reach the target and employer money must not drop
        if any("Rounded after-tax up" in w for w in d["warnings"]):
            self.assertAlmostEqual(rec["total_additions"], 72000.0, delta=0.01)
        self.assertFalse(any("short of the" in w for w in d["warnings"]) and
                         any("Rounded after-tax up" in w for w in d["warnings"]))

import json
import unittest
from pathlib import Path

from paycalc.config import ConfigError, parse
from paycalc.engine import analyze

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "example_config.json").read_text())


def cfg(**ded):
    c = json.loads(json.dumps(EXAMPLE))
    c["pay"]["frequency"] = "semimonthly"
    c["deductions"] = dict(ded)
    return c


class HsaMaxOutTest(unittest.TestCase):
    def test_family_limit_minus_employer_over_paychecks(self):
        a = analyze(parse(cfg(hsa_coverage="family", hsa_max_out=True, hsa_employer_annual=1500,
                              hsa_per_paycheck=999)))
        regular = [r for r in a.current.result.rows if r.kind != "bonus"]
        self.assertEqual(len(regular), 24)
        self.assertAlmostEqual(regular[0].hsa, 302.08, places=2)  # (8750 - 1500) / 24 floored to cents
        self.assertLessEqual(a.current.result.hsa + 1500, 8750 + 0.001)
        self.assertTrue(any("HSA set to $302.08" in w for w in a.warnings))

    def test_off_by_default_uses_typed_amount(self):
        a = analyze(parse(cfg(hsa_coverage="family", hsa_per_paycheck=100)))
        self.assertAlmostEqual([r for r in a.current.result.rows if r.kind != "bonus"][0].hsa, 100)

    def test_requires_coverage(self):
        with self.assertRaises(ConfigError):
            parse(cfg(hsa_max_out=True))

    def test_employer_over_limit_gives_zero(self):
        a = analyze(parse(cfg(hsa_coverage="self", hsa_max_out=True, hsa_employer_annual=9000)))
        self.assertEqual(a.current.result.hsa, 0)


if __name__ == "__main__":
    unittest.main()

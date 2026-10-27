import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from paycalc import gui, retirement as R
from paycalc.config import ConfigError


def plan(**kw):
    base = {"you_age": 40, "end_age": 90, "target_retire_age": 60, "real_return_percent": 0,
            "tax_earned_percent": 0, "tax_pretax_withdraw_percent": 0,
            "accounts": [{"name": "a", "type": "taxable", "balance": 100000}],
            "spending": [{"age": 40, "annual": 50000}],
            "you": {"stop_age": 60, "steps": [{"age": 40, "salary": 100000, "pretax_percent": 10}]}}
    base.update(kw)
    return base


def run(raw):
    return R.analyze_plan(R.parse_plan(raw))


class SimulationTest(unittest.TestCase):
    def test_zero_return_arithmetic(self):
        # Working: earns 100k, saves 10k, spends 50k -> 40k surplus/yr into taxable. 20 years -> 100k + 20*40k.
        p = R.parse_plan(plan())
        rows, dep = R.simulate(p, R.effective(p, None))
        row59 = [r for r in rows if r["age"] == 59][0]
        self.assertAlmostEqual(row59["balance"], 100000 + 20 * 40000 + 20 * 10000)  # surplus + 10k/yr into pretax
        # Retired at 60 with 1.1M at 0% return, spending 50k a year: lasts 22 years -> runs out at 82
        self.assertEqual(dep, 82)

    def test_depletion_none_when_funded(self):
        self.assertIsNone(run(plan(accounts=[{"name": "a", "type": "taxable", "balance": 5_000_000}]))
                          ["scenarios"][0]["metrics"]["depletion_age"])

    def test_pretax_withdrawals_are_grossed_up(self):
        p = R.parse_plan(plan(tax_pretax_withdraw_percent=20, you={"stop_age": 40, "steps": []},
                              accounts=[{"name": "k", "type": "pretax", "balance": 1_000_000}], end_age=45,
                              target_retire_age=41))
        rows, _ = R.simulate(p, R.effective(p, None))
        self.assertAlmostEqual(rows[0]["balance"], 1_000_000 - 50000 / 0.8)

    def test_scenario_replaces_steps_from_its_age(self):
        raw = plan(scenarios=[{"name": "layoff", "you": {"steps": [{"age": 50, "salary": 40000, "pretax_percent": 5}]}}])
        p = R.parse_plan(raw)
        eff = R.effective(p, p["scenarios"][0])
        self.assertEqual([(s["age"], s["salary"]) for s in eff["you"]["steps"]], [(40, 100000.0), (50, 40000.0)])
        base, lay = run(raw)["scenarios"]
        self.assertEqual(base["name"], "Baseline")
        self.assertLess(lay["metrics"]["balance_at_target"], base["metrics"]["balance_at_target"])
        self.assertEqual(p["you"]["steps"][0]["salary"], 100000.0)  # base plan untouched

    def test_limits_cap_contributions(self):
        p = R.parse_plan(plan(you={"stop_age": 60, "steps": [{"age": 40, "salary": 1_000_000, "pretax_percent": 50,
                                                              "posttax_percent": 50, "employer_percent": 5}]}))
        rows, _ = R.simulate(p, R.effective(p, None))
        self.assertLessEqual(rows[0]["contributions"], 72000 + 1e-6)

    def test_barista_income_makes_money_last(self):
        raw = plan(accounts=[{"name": "a", "type": "taxable", "balance": 400000}], real_return_percent=0, barista_until_age=70)
        m = run(raw)["scenarios"][0]["metrics"]
        self.assertEqual(m["depletion_age"] is not None, True)
        need = m["barista_income_needed"]
        self.assertGreater(need, 0)
        p = R.parse_plan(raw)
        self.assertIsNone(R.simulate(p, R.effective(p, None), need + 1)[1])
        self.assertIsNotNone(R.simulate(p, R.effective(p, None), max(0, need - 500))[1])

    def test_coast_age_found(self):
        m = run(plan(real_return_percent=5, accounts=[{"name": "a", "type": "taxable", "balance": 700000}]))["scenarios"][0]["metrics"]
        self.assertEqual(m["coast_age"], 40)  # already enough to coast: 1.25M needed / 1.05^20 = 471k

    def test_spouse_optional(self):
        self.assertFalse(run(plan())["meta"]["spouse"])
        self.assertTrue(run(plan(spouse_age=38, spouse={"stop_age": 60, "steps": [{"age": 38, "salary": 50000}]}))["meta"]["spouse"])


class ValidationTest(unittest.TestCase):
    def test_bad_inputs(self):
        bad = [{}, plan(you_age=10), plan(end_age=30), plan(spending=[]), plan(real_return_percent=99),
               plan(accounts=[{"name": "x", "type": "gold", "balance": 1}]),
               plan(accounts=[{"name": "x", "type": "roth", "balance": -1}]),
               plan(you={"steps": [{"age": 40, "salary": -5}]}), plan(scenarios="x"),
               plan(scenarios=[{"name": "x" * 61}]), plan(invest_surplus="yes"),
               plan(you={"steps": [{"age": 40.5, "salary": 1}]}), plan(swr_percent=0)]
        for b in bad:
            with self.assertRaises(ConfigError, msg=str(b)[:80]):
                R.parse_plan(b)

    def test_size_limits(self):
        with self.assertRaises(ConfigError):
            R.parse_plan(plan(scenarios=[{"name": str(i)} for i in range(9)]))


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.main = Path(cls.tmp.name) / "config.local.json"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(cls.main))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        d = json.dumps(body).encode() if body is not None else None
        if d is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=d, headers=h)
        r = c.getresponse(); raw = r.read(); c.close()
        return r.status, raw

    def test_analyze_save_load(self):
        self.assertEqual(json.loads(self.req("GET", "/api/retirement")[1])["plan"], None)
        st, raw = self.req("POST", "/api/retirement/analyze", plan())
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["scenarios"][0]["name"], "Baseline")
        self.assertEqual(self.req("POST", "/api/retirement/save", plan())[0], 200)
        self.assertEqual(json.loads(self.req("GET", "/api/retirement")[1])["plan"]["you_age"], 40)
        self.assertTrue((self.main.parent / "retirement.local.json").exists())

    def test_bad_input_and_origin(self):
        self.assertEqual(self.req("POST", "/api/retirement/analyze", plan(spending=[]))[0], 400)
        self.assertEqual(self.req("POST", "/api/retirement/save", {"you_age": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/retirement/save", plan(), {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/retirement", None, {"Host": "evil.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()

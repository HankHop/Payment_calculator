import copy
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from paycalc import gui, household as H
from paycalc.config import ConfigError, parse

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "example_config.json").read_text())


def hh(**kw):
    base = {"expenses": [], "inheritance_balance": 0, "split_you_percent": 50, "start_month": 1}
    base.update(kw)
    return H.parse_household(base)


def run(h, you=None, spouse=None):
    return H.analyze_household(parse(you or copy.deepcopy(EXAMPLE)), parse(spouse or copy.deepcopy(EXAMPLE)), h)


class ModelTest(unittest.TestCase):
    def test_joint_bills_split_and_funded_from_pay(self):
        r = run(hh(expenses=[{"name": "mortgage", "monthly": 1000, "account": "joint"}], split_you_percent=60))
        m = r["scenarios"][-1]["months"][0]  # recommended: positive take-home all year
        self.assertAlmostEqual(m["you_share"], 600)
        self.assertAlmostEqual(m["spouse_share"], 400)
        self.assertAlmostEqual(m["you_to_joint"] + m["spouse_to_joint"], 1000)
        self.assertEqual(m["inheritance_used"], 0)

    def test_inheritance_covers_joint_gap_and_runs_out(self):
        you = copy.deepcopy(EXAMPLE)
        you["contributions"]["elections"] = [{"effective_date": "2026-01-09", "pretax_percent": 10, "posttax_percent": 90}]
        h = hh(expenses=[{"name": "bills", "monthly": 4000, "account": "joint"}], inheritance_balance=5000)
        sc = run(h, you=you)["scenarios"][0]
        zero = [m for m in sc["months"] if m["you_net"] < 1]
        self.assertTrue(zero)
        self.assertAlmostEqual(sc["totals"]["inheritance_used"], 5000)
        self.assertGreater(sc["totals"]["unfunded"], 0)
        self.assertAlmostEqual(sc["months"][-1]["inheritance_balance"], 0)

    def test_personal_overage_is_not_covered_by_joint_or_inheritance(self):
        h = hh(expenses=[{"name": "daughter clothes", "monthly": 1e6, "account": "spouse"}], inheritance_balance=99999)
        sc = run(h)["scenarios"][-1]
        self.assertEqual(sc["totals"]["inheritance_used"], 0)
        self.assertEqual(sc["totals"]["unfunded"], 0)
        self.assertGreater(sc["totals"]["spouse_own_shortfall"], 0)
        self.assertTrue(any("not paid from the joint account" in w for w in run(h)["warnings"]))

    def test_personal_costs_reduce_only_that_persons_availability(self):
        h = hh(expenses=[{"name": "car", "monthly": 500, "account": "you"},
                         {"name": "clothes", "monthly": 300, "account": "spouse"}])
        m = run(h)["scenarios"][-1]["months"][0]
        self.assertAlmostEqual(m["you_available"], m["you_net"] - 500, places=1)
        self.assertAlmostEqual(m["spouse_available"], m["spouse_net"] - 300, places=1)

    def test_shared_personal_cost_added_to_total_and_credited_to_payer(self):
        h = hh(expenses=[{"name": "bills", "monthly": 10000, "account": "joint"},
                         {"name": "baby clothes", "monthly": 100, "account": "spouse", "shared": True}],
               inheritance_balance=100000, inheritance_monthly=4000)
        m = run(h)["scenarios"][-1]["months"][0]
        self.assertAlmostEqual(m["total_cost"], 10100)
        self.assertAlmostEqual(m["inheritance_planned"], 4000)
        self.assertAlmostEqual(m["spouse_share"], 2950)   # half of 6,100 less the 100 she paid
        self.assertAlmostEqual(m["you_share"], 3050)
        self.assertAlmostEqual(m["you_share"] + m["spouse_share"], 10000 - 4000)  # joint account gets bills minus offset

    def test_unshared_personal_cost_does_not_change_the_total(self):
        h = hh(expenses=[{"name": "bills", "monthly": 1000, "account": "joint"},
                         {"name": "hobby", "monthly": 100, "account": "spouse"}])
        m = run(h)["scenarios"][-1]["months"][0]
        self.assertAlmostEqual(m["total_cost"], 1000)
        self.assertAlmostEqual(m["spouse_share"], 500)

    def test_planned_inheritance_limited_by_balance(self):
        h = hh(expenses=[{"name": "bills", "monthly": 1000, "account": "joint"}],
               inheritance_balance=1500, inheritance_monthly=1000, start_month=11)
        months = run(h)["scenarios"][-1]["months"]
        self.assertAlmostEqual(months[0]["inheritance_planned"], 1000)
        self.assertAlmostEqual(months[1]["inheritance_planned"], 500)

    def test_tax_year_mismatch_rejected(self):
        sp = copy.deepcopy(EXAMPLE)
        sp["tax_year"] = 2027
        sp["contributions"]["elections"] = [{"effective_date": "2027-01-01", "pretax_percent": 5, "posttax_percent": 0}]
        sp["pay"]["first_pay_date"] = "2027-01-08"
        with self.assertRaises(ConfigError):
            run(hh(), spouse=sp)

    def test_validation(self):
        bad = [{"expenses": [{"name": "x", "monthly": -1}]}, {"expenses": [{"name": "x", "monthly": 5, "account": "bank"}]},
               {"expenses": "nope"}, {"split_you_percent": 101}, {"inheritance_balance": -1}, {"start_month": 13},
               {"spouse_scenario": "x"}, {"inheritance_monthly": -5},
               {"expenses": [{"name": "x", "monthly": 5, "account": "you", "shared": "yes"}]}, {"expenses": [{"name": "x" * 61, "monthly": 1}]}, {"expenses": [1]}]
        for b in bad:
            with self.assertRaises(ConfigError, msg=str(b)):
                H.parse_household(b)

    def test_start_month_defaults_to_month_after_as_of(self):
        you = copy.deepcopy(EXAMPLE)
        you["as_of_date"] = "2026-10-15"
        h = H.parse_household({"expenses": []})
        self.assertEqual(run(h, you=you)["meta"]["start_month"], 11)


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

    def test_save_load_analyze(self):
        household = {"expenses": [{"name": "x", "monthly": 100, "account": "joint"}]}
        self.assertEqual(self.req("POST", "/api/household/save", {"household": household, "spouse": EXAMPLE})[0], 200)
        st, raw = self.req("GET", "/api/household")
        got = json.loads(raw)
        self.assertEqual(got["household"], household)
        self.assertEqual(got["spouse"]["state"], EXAMPLE["state"])
        st, raw = self.req("POST", "/api/household/analyze", {"you": EXAMPLE, "spouse": EXAMPLE, "household": household})
        self.assertEqual(st, 200)
        self.assertIn("scenarios", json.loads(raw))
        self.assertTrue((self.main.parent / "household.local.json").exists())
        self.assertFalse(self.main.exists())  # your own inputs are not touched

    def test_bad_input_rejected_without_leaks(self):
        st, raw = self.req("POST", "/api/household/analyze", {"you": EXAMPLE, "spouse": EXAMPLE,
                                                              "household": {"split_you_percent": 500}})
        self.assertEqual(st, 400)
        self.assertEqual(self.req("POST", "/api/household/analyze", {"you": EXAMPLE})[0], 400)
        self.assertEqual(self.req("POST", "/api/household/save", {"household": {}, "spouse": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/household/save", {"household": {"expenses": 5}})[0], 400)

    def test_same_origin_and_host_enforced(self):
        body = {"household": {}, "spouse": None}
        self.assertEqual(self.req("POST", "/api/household/save", body, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/household/save", body, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/household", None, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/household?path=/tmp/x")[0], 404)


if __name__ == "__main__":
    unittest.main()

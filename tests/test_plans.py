import json
import tempfile
import unittest
from pathlib import Path

from paycalc import data, plans
from paycalc.config import ConfigError, parse
from paycalc.engine import analyze

import http.client
import threading
from http.server import ThreadingHTTPServer

from paycalc import gui

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "example_config.json").read_text())


def sample():
    c = json.loads(json.dumps(EXAMPLE))
    c["as_of_date"] = "2026-06-30"
    c["pay"]["raise"] = {"effective_date": "2026-07-01", "new_salary": 120000}
    return c


class RolloverTest(unittest.TestCase):
    def test_original_not_modified_and_result_calculates(self):
        raw = sample()
        before = json.dumps(raw, sort_keys=True)
        new = plans.rollover(raw)
        self.assertEqual(json.dumps(raw, sort_keys=True), before)
        self.assertEqual(new["tax_year"], 2027)
        self.assertNotIn("as_of_date", new)
        self.assertNotIn("what_if", new)
        self.assertNotIn("raise", new["pay"])
        self.assertEqual(new["pay"]["base_salary"], 120000)
        self.assertEqual(len(new["contributions"]["elections"]), 1)
        self.assertEqual(new["contributions"]["elections"][0]["effective_date"], "2027-01-01")
        self.assertEqual(analyze(parse(new)).cfg.tax_year, 2027)

    def test_bonus_dates_move_and_amounts_stay(self):
        raw = sample()
        raw["pay"]["bonuses"] = [{"name": "b", "pay_date": "2026-02-28", "amount": 5000}]
        new = plans.rollover(raw)
        self.assertEqual(new["pay"]["bonuses"][0]["pay_date"], "2027-02-28")
        self.assertEqual(new["pay"]["bonuses"][0]["amount"], 5000)

    def test_rejects_non_future_year(self):
        with self.assertRaises(ConfigError):
            plans.rollover(sample(), 2026)

    def test_names(self):
        for bad in ("", "main", "../x", "a/b", "a.b", "x" * 41, "a b", None, 5):
            self.assertFalse(plans.valid_name(bad), bad)
        for ok in ("2027", "next-year", "a_b"):
            self.assertTrue(plans.valid_name(ok))
        with self.assertRaises(ConfigError):
            plans.plan_path(Path("/x/c.json"), "../evil")


class DataFallbackTest(unittest.TestCase):
    def test_later_year_uses_latest_and_warns(self):
        self.assertEqual(data.data_year("federal", 2027), 2026)
        self.assertEqual(data.data_year("federal", 2026), 2026)
        with self.assertRaises(ConfigError):
            data.data_year("federal", 2020)
        res = analyze(parse(plans.rollover(sample())))
        self.assertTrue(any("stand-in" in w for w in res.warnings))
        res26 = analyze(parse(sample()))
        self.assertFalse(any("stand-in" in w for w in res26.warnings))


class PlanRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.main = Path(cls.tmp.name) / "config.local.json"
        cls.main.write_text(json.dumps(sample()))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(cls.main))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        data_ = json.dumps(body).encode() if body is not None else None
        if data_ is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data_, headers=h)
        r = c.getresponse(); raw = r.read(); c.close()
        return r.status, raw

    def test_rollover_creates_plan_and_leaves_main_untouched(self):
        before = self.main.read_bytes()
        st, raw = self.req("POST", "/api/rollover", {"year": 2027, "config": sample()})
        self.assertEqual(st, 200)
        name = json.loads(raw)["name"]
        self.assertEqual(name, "2027")
        self.assertEqual(self.main.read_bytes(), before)
        self.assertTrue((self.main.parent / "plans" / "2027.local.json").exists())
        names = [p["name"] for p in json.loads(self.req("GET", "/api/plans")[1])["plans"]]
        self.assertIn("2027", names)
        st, raw = self.req("GET", "/api/config?plan=2027")
        self.assertEqual(json.loads(raw)["config"]["tax_year"], 2027)
        # save to the plan, main still unchanged
        cfg = json.loads(raw)["config"]; cfg["pay"]["base_salary"] = 150000
        self.assertEqual(self.req("POST", "/api/save?plan=2027", cfg)[0], 200)
        self.assertEqual(self.main.read_bytes(), before)
        # a second rollover for the same year gets a new name, never overwrites
        st, raw = self.req("POST", "/api/rollover", {"year": 2027, "config": sample()})
        self.assertEqual(json.loads(raw)["name"], "2027-2")
        # explicit existing name refused
        self.assertEqual(self.req("POST", "/api/rollover", {"year": 2027, "name": "2027", "config": sample()})[0], 400)
        self.assertEqual(self.req("POST", "/api/rollover", {"year": 2027, "name": "main", "config": sample()})[0], 400)

    def test_bad_plan_names_and_params_rejected(self):
        for q in ("?plan=../../etc/passwd", "?plan=a%2Fb", "?plan=%2e%2e", "?plan=a&plan=b", "?path=/tmp/x"):
            self.assertEqual(self.req("GET", "/api/config" + q)[0], 404, q)
            self.assertEqual(self.req("POST", "/api/save" + q, sample())[0], 404, q)
        self.assertEqual(self.req("POST", "/api/rollover", {"year": "x", "config": sample()})[0], 400)
        self.assertEqual(self.req("POST", "/api/rollover", {"year": 2027})[0], 400)

    def test_rollover_requires_same_origin(self):
        st, _ = self.req("POST", "/api/rollover", {"year": 2027, "config": sample()},
                         {"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _ = self.req("POST", "/api/rollover", {"year": 2027, "config": sample()}, {"Host": "evil.example"})
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()

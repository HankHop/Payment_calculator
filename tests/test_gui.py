import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from paycalc import gui

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "example_config.json").read_text())


class GuiServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg_path = Path(cls.tmp.name) / "saved.json"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(cls.cfg_path))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, raw

    def test_page_is_served(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Paycheck Calculator", raw)

    def test_meta_lists_all_states(self):
        status, raw = self.request("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(raw)["states"]), 51)

    def test_config_falls_back_to_example_then_saved(self):
        self.cfg_path.unlink(missing_ok=True)
        status, raw = self.request("GET", "/api/config")
        self.assertEqual(json.loads(raw)["source"], "example")
        self.assertEqual(self.request("POST", "/api/save", EXAMPLE)[0], 200)
        self.assertEqual(json.loads(self.request("GET", "/api/config")[1])["source"], "saved")

    def test_analyze_returns_results(self):
        status, raw = self.request("POST", "/api/analyze", EXAMPLE)
        self.assertEqual(status, 200)
        d = json.loads(raw)
        for key in ("meta", "summary", "current", "recommended", "recommendation", "warnings"):
            self.assertIn(key, d)
        self.assertEqual(len(d["current"]["monthly"]), 12)

    def test_invalid_config_is_a_400_with_message(self):
        bad = dict(EXAMPLE, filing_status="nope")
        status, raw = self.request("POST", "/api/analyze", bad)
        self.assertEqual(status, 400)
        self.assertIn("filing_status", json.loads(raw)["error"])

    def test_wrong_types_do_not_crash_the_server(self):
        for body in ({"filing_status": "single", "state": "TX", "pay": {"base_salary": "abc", "frequency": "monthly"}},
                     {"filing_status": "single", "state": "TX", "pay": []},
                     {"filing_status": "single", "state": "TX", "pay": {"base_salary": 1, "frequency": "monthly"},
                      "contributions": {"elections": "x"}}):
            self.assertEqual(self.request("POST", "/api/analyze", body)[0], 400, body)
        self.assertEqual(self.request("POST", "/api/analyze", b"not json")[0], 400)
        self.assertEqual(self.request("POST", "/api/analyze", [1, 2])[0], 400)

    def test_save_refuses_invalid_config(self):
        before = self.cfg_path.read_text() if self.cfg_path.exists() else None
        self.assertEqual(self.request("POST", "/api/save", {"filing_status": "x"})[0], 400)
        after = self.cfg_path.read_text() if self.cfg_path.exists() else None
        self.assertEqual(before, after)

    def test_rejects_non_local_host_header(self):
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/analyze", EXAMPLE, headers={"Host": "evil.example"})[0], 403)

    def test_rejects_non_json_post(self):
        status, _ = self.request("POST", "/api/analyze", b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_unknown_paths(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("POST", "/api/nope", {})[0], 404)


if __name__ == "__main__":
    unittest.main()

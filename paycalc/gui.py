"""Local web GUI: python -m paycalc.gui

Serves one page on 127.0.0.1 only. Inputs are saved to a git-ignored file in the current
directory (default config.local.json); nothing is sent anywhere else.
"""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import data, report
from .config import ConfigError, parse
from .engine import analyze

PAGE = Path(__file__).parent / "web" / "index.html"
EXAMPLE = Path(__file__).parent.parent / "examples" / "example_config.json"
MAX_BODY = 1_000_000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
# The page is one file with inline script/style, so those two keep 'unsafe-inline'; everything else is locked down.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


def _fallback_config() -> dict:
    return {
        "tax_year": 2026, "filing_status": "single", "state": "TX",
        "pay": {"base_salary": 100000, "frequency": "biweekly", "first_pay_date": "2026-01-09"},
        "contributions": {"elections": [{"effective_date": "2026-01-09", "pretax_percent": 6, "posttax_percent": 0}],
                          "employer": {}},
    }


def run_analysis(raw: dict) -> dict:
    return report.as_dict(analyze(parse(raw)))


def make_handler(config_path: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "paycalc-gui"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode())

        def _local_only(self) -> bool:
            # Blocks DNS-rebinding: only answer requests addressed to localhost.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in LOCAL_HOSTS

        def _same_origin(self) -> bool:
            # CSRF defence in depth: reject requests a browser marks as cross-site, and any Origin
            # that is not this server.
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            host = self.headers.get("Host") or ""
            return origin in (f"http://{host}",)

        def do_GET(self):
            if not self._local_only():
                return self._json(403, {"error": "forbidden"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/config":
                example = json.loads(EXAMPLE.read_text()) if EXAMPLE.exists() else _fallback_config()
                if config_path.exists():
                    cfg, source = json.loads(config_path.read_text()), "saved"
                else:
                    cfg, source = example, "example"
                return self._json(200, {"config": cfg, "example": example, "source": source,
                                        "path": str(config_path)})
            if self.path == "/api/meta":
                states = data._load("states", 2026)["states"]
                return self._json(200, {"states": [{"code": k, "name": v["name"]}
                                                   for k, v in sorted(states.items(), key=lambda kv: kv[1]["name"])]})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_only() or not self._same_origin():
                return self._json(403, {"error": "forbidden"})
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(415, {"error": "expected application/json"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._json(413, {"error": "bad request size"})
            try:
                raw = json.loads(self.rfile.read(length))
                if not isinstance(raw, dict):
                    raise ConfigError("config must be a JSON object")
                if self.path == "/api/analyze":
                    return self._json(200, run_analysis(raw))
                if self.path == "/api/save":
                    parse(raw)  # refuse to save something that cannot be calculated
                    tmp = config_path.with_name(config_path.name + ".tmp")
                    tmp.write_text(json.dumps(raw, indent=2) + "\n")
                    tmp.replace(config_path)
                    return self._json(200, {"saved": str(config_path)})
                return self._json(404, {"error": "not found"})
            except (ConfigError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": str(exc)})
            except (KeyError, TypeError, ValueError, AttributeError):
                return self._json(400, {"error": "invalid input: a field has the wrong type or is missing"})
            except Exception:
                return self._json(500, {"error": "internal error"})

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paycalc.gui", description="Local web GUI for the paycheck calculator.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default="config.local.json",
                    help="where your inputs are saved (git-ignored by default)")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = ap.parse_args(argv)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Path(args.config)))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Paycheck calculator running at {url}  (Ctrl+C to stop)")
    print(f"Saved inputs: {Path(args.config).resolve()}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

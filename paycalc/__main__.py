import argparse
import json
import sys

from . import report
from .config import ConfigError, load
from .engine import analyze


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paycalc", description="Estimate taxes and recommend 401(k) percentages.")
    ap.add_argument("config", help="path to a config JSON file (see examples/example_config.json)")
    ap.add_argument("--schedule", action="store_true", help="also print contributions for every pay date")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    args = ap.parse_args(argv)
    try:
        analysis = analyze(load(args.config))
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.as_dict(analysis), indent=2))
    else:
        print(report.text(analysis, schedule=args.schedule))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Measurement from the command line, for a daily or weekly schedule (cron or launchd).

  python scripts/measure.py sync            pull host logs and store new hits
  python scripts/measure.py check           ask questions for every live page that is due (weekly)
  python scripts/measure.py check --slug S  check one page now
  python scripts/measure.py report          print a one-line summary per live page
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import citations, evidence, golive, results  # noqa: E402

DATA = Path(os.environ.get("UNWALL_DATA", ROOT / "data"))


def live_jobs() -> list[dict]:
    jobs = [json.loads(p.read_text()) for p in sorted((DATA / "jobs").glob("*/job.json"))]
    return [j for j in jobs if (j.get("launch") or {}).get("state") == "live" and not j.get("sample")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    check = sub.add_parser("check")
    check.add_argument("--slug")
    sub.add_parser("report")
    args = parser.parse_args()

    if args.command == "sync":
        host = golive.host_config(DATA)
        if not host:
            print("No public host is connected. Run scripts/setup_host.py first.")
            return 2
        state = evidence.sync(DATA, host)
        print(f"Read {state['requests']} requests from {state['deployments']} deployments; {state['new_hits']} new hits.")
        for error in state["errors"]:
            print(f"  warning: {error}")
        return 1 if state["errors"] and not state["requests"] else 0

    if args.command == "check":
        jobs = [j for j in live_jobs() if j["slug"] == args.slug] if args.slug else [j for j in live_jobs() if citations.due(j, DATA)]
        if not jobs:
            print("Nothing to check." if not args.slug else f"No live page with slug {args.slug}.")
            return 0
        for job in jobs:
            outcome = citations.run(job, DATA, "manual" if args.slug else "weekly")
            print(f"{job['slug']}: {outcome['answers']} answers, {outcome['skipped']} skipped, {outcome['errors']} errors")
        return 0

    for job in live_jobs():
        s = results.summary(job, DATA)
        latest = s["latest"]
        cited = f"cited in {latest['page_cited']}/{latest['asked']} answers ({latest['label']})" if latest else "no checks yet"
        print(f"{job['slug']}: {s['fetches']['trusted']} AI fetches, {cited}, {s['clicks']} clicks to the post")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Summarize a page's evidence: AI fetches, citations against the baseline, and clicks to the post."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import citations, evidence


def check_label(kind: str, at: str, baseline_at: str | None) -> str:
    if kind == "baseline":
        return "Baseline"
    if baseline_at:
        days = (datetime.fromisoformat(at) - datetime.fromisoformat(baseline_at)).days
        return f"Week {max(1, round(days / 7))}" if kind == "weekly" else f"Day {days}"
    return "Check"


def summarize_checks(runs: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in runs:
        grouped[record["run_id"]].append(record)
    ordered = sorted(grouped.values(), key=lambda rs: rs[0]["at"])
    baseline_at = next((rs[0]["at"] for rs in ordered if rs[0]["kind"] == "baseline"), None)
    checks = []
    for records in ordered:
        engines: dict[str, dict] = {}
        for r in records:
            e = engines.setdefault(r["engine"], {"asked": 0, "searched": 0, "page_cited": 0, "post_cited": 0,
                                                 "named": 0, "skipped": 0, "errors": 0, "note": None})
            if r["status"] == "skipped":
                e["skipped"] += 1
                e["note"] = r["error"]
                continue
            if r["status"] == "error":
                e["errors"] += 1
                e["note"] = r["error"]
                continue
            e["asked"] += 1
            for key in ("searched", "page_cited", "post_cited", "named"):
                e[key] += bool(r.get(key))
        first = records[0]
        checks.append({"run_id": first["run_id"], "kind": first["kind"], "at": first["at"],
                       "label": check_label(first["kind"], first["at"], baseline_at),
                       "questions": len({r["question"] for r in records}), "engines": engines})
    return checks


def summary(job: dict, data: Path) -> dict:
    slug = job["slug"]
    hits = evidence.hits_for(data, slug)
    fetches = [h for h in hits if h["kind"] == "ai_fetch"]
    by_agent: dict[str, dict] = {}
    for h in fetches:
        row = by_agent.setdefault(h["agent"], {"total": 0, "confirmed": 0, "unconfirmable": 0})
        row["total"] += 1
        row["confirmed"] += h["confirmed"] is True
        row["unconfirmable"] += h["confirmed"] is None
    trusted = sum(r["confirmed"] + r["unconfirmable"] for r in by_agent.values())
    checks = summarize_checks(citations.runs_for(data, slug))
    latest = next((c for c in reversed(checks) if any(e["asked"] for e in c["engines"].values())), None)
    baseline = next((c for c in checks if c["kind"] == "baseline"), None)

    def cited(check: dict | None) -> tuple[int, int]:
        if not check:
            return 0, 0
        asked = sum(e["asked"] for e in check["engines"].values())
        return sum(e["page_cited"] for e in check["engines"].values()), asked

    page_cited, asked = cited(latest)
    clicks = sum(h["kind"] == "click" for h in hits)
    referrals = Counter(h["source"] for h in hits if h["kind"] == "ai_referral")
    return {
        "slug": slug,
        "live": (job.get("launch") or {}).get("state") == "live",
        "sample": bool(job.get("sample")),
        "checking": slug in citations.ACTIVE,
        "questions": citations.questions(job),
        "fetches": {"total": len(fetches), "trusted": trusted, "by_agent": by_agent},
        "crawlers": dict(Counter(h["agent"] for h in hits if h["kind"] == "crawler")),
        "referrals": dict(referrals),
        "clicks": clicks,
        "visits": sum(h["kind"] == "visit" for h in hits),
        "checks": checks,
        "latest": {"label": latest["label"], "page_cited": page_cited, "asked": asked,
                   "post_cited": sum(e["post_cited"] for e in latest["engines"].values()),
                   "named": sum(e["named"] for e in latest["engines"].values())} if latest else None,
        "baseline": {"page_cited": cited(baseline)[0], "asked": cited(baseline)[1]} if baseline else None,
        "last_sync": evidence.last_sync(data),
    }

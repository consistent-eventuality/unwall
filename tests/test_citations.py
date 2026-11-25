"""Citation checks: matching, recording, scheduling, and the app wiring. All engines are fakes."""
from __future__ import annotations

import time

from conftest import make_job, settle
from server import citations, results

TARGET = {"slug": "suede", "page_url": "https://pages.example/p/suede/", "post_url": "https://www.instagram.com/reel/ABC/",
          "creator": "Sam Rivera"}


def answer(cited=(), consulted=(), text=""):
    return {"text": text, "cited": list(cited), "consulted": list(consulted)}


def test_score_matches_pages_posts_and_names():
    s = citations.score(answer(cited=["https://pages.example/p/suede/?utm_source=openai"],
                               consulted=["https://instagram.com/reel/ABC"], text="Per Sam Rivera, brush it."), TARGET)
    assert s == {"searched": True, "page_cited": True, "page_found": True, "post_cited": False, "post_found": True, "named": True}
    none = citations.score(answer(text="Brush it."), TARGET)
    assert not any(none.values())
    # A different page on the same host is not a match.
    assert not citations.score(answer(cited=["https://pages.example/p/suede-2/"]), TARGET)["page_cited"]


def test_questions_dedupe_and_cap():
    job = {"draft": {"question": "How do you clean suede?", "related_questions":
                     ["how do you clean suede?", "  Can you steam suede? ", "", "a?", "b?", "c?", "d?"]}}
    assert citations.questions(job) == ["How do you clean suede?", "Can you steam suede?", "a?", "b?", "c?"]


def test_run_records_every_engine_even_when_one_fails(tmp_path):
    job = {"slug": "suede", "launch": {"state": "live", "url": TARGET["page_url"]},
           "meta": {"source_url": TARGET["post_url"], "creator": "Sam Rivera"},
           "draft": {"question": "How do you clean suede?", "related_questions": ["Can you steam suede?"]}}

    def broken(q):
        raise RuntimeError("rate limited")

    def skipped(q):
        raise citations.Skipped("PERPLEXITY_API_KEY isn't set.")

    engines = {"ChatGPT": lambda q: answer(cited=[TARGET["page_url"]]), "Perplexity": skipped, "Claude": broken}
    outcome = citations.run(job, tmp_path, "baseline", engines)
    assert outcome == {"run_id": outcome["run_id"], "answers": 2, "skipped": 2, "errors": 2}
    records = citations.runs_for(tmp_path, "suede")
    assert len(records) == 6 and {r["status"] for r in records} == {"ok", "skipped", "error"}
    check = results.summarize_checks(records)[0]
    assert check["label"] == "Baseline" and check["questions"] == 2
    assert check["engines"]["ChatGPT"]["page_cited"] == 2 and check["engines"]["Claude"]["errors"] == 2
    assert check["engines"]["Perplexity"]["note"] == "PERPLEXITY_API_KEY isn't set."


def test_due_only_for_live_real_pages_after_six_days(tmp_path):
    job = {"slug": "suede", "sample": None, "launch": {"state": "live", "url": TARGET["page_url"]},
           "meta": {"source_url": TARGET["post_url"], "creator": "Sam"}, "draft": {"question": "Q?"}}
    assert citations.due(job, tmp_path)
    citations.run(job, tmp_path, "baseline", {"ChatGPT": lambda q: answer()})
    assert not citations.due(job, tmp_path)
    assert citations.due(job, tmp_path, now=time.time() + 7 * 86400)
    assert not citations.due({**job, "sample": "demo"}, tmp_path)
    assert not citations.due({**job, "launch": {"state": "failed"}}, tmp_path)


def test_week_labels():
    assert results.check_label("baseline", "2026-11-01T00:00:00+00:00", None) == "Baseline"
    assert results.check_label("weekly", "2026-11-15T00:00:00+00:00", "2026-11-01T00:00:00+00:00") == "Week 2"
    assert results.check_label("manual", "2026-11-04T00:00:00+00:00", "2026-11-01T00:00:00+00:00") == "Day 3"


def test_baseline_runs_after_go_live_and_results_endpoint(world, monkeypatch):
    client, data = world["client"], world["data"]
    import server.app as app_module
    asked = []

    def fake(q):
        asked.append(q)
        return answer(consulted=["https://example.org/a"], text="Brush it.")

    monkeypatch.setenv("UNWALL_BASELINE", "1")
    monkeypatch.setattr(app_module, "CHECK_ENGINES", {"ChatGPT": fake})
    real, sample = "a" * 12, "b" * 12
    make_job(data, real)
    make_job(data, sample, sample="demo", question="Sample?")
    assert client.get(f"/api/jobs/{real}/results").status_code == 409  # Not live yet.

    client.post(f"/api/jobs/{real}/publish", json={"consent": True})
    settle(client, real)
    for _ in range(100):
        if not citations.ACTIVE and asked:
            break
        time.sleep(0.05)
    assert asked == ["How do you clean suede?"]
    summary = client.get(f"/api/jobs/{real}/results").json()
    assert summary["checks"][0]["label"] == "Baseline" and summary["latest"]["asked"] == 1
    assert summary["baseline"] == {"page_cited": 0, "asked": 1}

    # A manual check adds a second entry; a second request while running is refused.
    assert client.post(f"/api/jobs/{real}/check").status_code == 200
    for _ in range(100):
        if not citations.ACTIVE:
            break
        time.sleep(0.05)
    assert len(client.get(f"/api/jobs/{real}/results").json()["checks"]) == 2
    report = client.get(f"/report/{real}")
    assert report.status_code == 200 and "Baseline" in report.text and 'content="noindex"' in report.text
    assert "How do you clean suede?" in report.text

    # Samples go live but never trigger a baseline or a check.
    client.post(f"/api/jobs/{sample}/publish", json={"consent": True})
    settle(client, sample)
    time.sleep(0.2)
    assert asked == ["How do you clean suede?"] * 2
    assert client.post(f"/api/jobs/{sample}/check").status_code == 409

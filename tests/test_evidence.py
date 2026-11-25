"""Host evidence: parsing access logs, confirming bots, de-duplicating, and the click redirect."""
from __future__ import annotations

import json
import time

from server import evidence, golive

RANGES = {k: v for k, v in {
    "openai_user": ["20.0.0.0/24"],
    "perplexity_bot": ["30.0.0.1/32"],
    "google": ["2001:4860:4801:10::/64"],
}.items()}


def ranges():
    import ipaddress
    return {k: [ipaddress.ip_network(p) for p in v] for k, v in RANGES.items()}


def caddy(uri, ua="Mozilla/5.0", ip="9.9.9.9", referer=None, status=200, method="GET", ts=1764000000.5, port="51000"):
    headers = {"User-Agent": [ua], "X-Forwarded-For": [f"{ip}, 10.0.0.1"]}
    if referer:
        headers["Referer"] = [referer]
    return {"level": "info", "ts": ts, "logger": "http.log.access.log0", "msg": "handled request", "status": status,
            "request": {"remote_ip": "10.0.0.1", "remote_port": port, "method": method, "uri": uri, "headers": headers}}


def railway_line(entry):
    """Railway's --json wraps each stdout line in an object with a message string."""
    return json.dumps({"timestamp": "2026-11-24T10:00:00Z", "message": json.dumps(entry), "severity": "info"})


def test_classifies_user_fetches_and_confirms_ip():
    hit = evidence.classify(caddy("/p/suede/", ua="Mozilla/5.0; compatible; ChatGPT-User/1.0; +https://openai.com/bot", ip="20.0.0.7"), ranges())
    assert (hit["kind"], hit["agent"], hit["confirmed"], hit["slug"]) == ("ai_fetch", "ChatGPT", True, "suede")
    spoofed = evidence.classify(caddy("/p/suede/", ua="ChatGPT-User/1.0", ip="66.66.66.66"), ranges())
    assert spoofed["kind"] == "ai_fetch" and spoofed["confirmed"] is False
    claude = evidence.classify(caddy("/p/suede/page.md", ua="Claude-User/1.0"), ranges())
    assert claude["kind"] == "ai_fetch" and claude["confirmed"] is None  # No published ranges.


def test_classifies_crawlers_referrals_clicks_and_ignores_noise():
    r = ranges()
    assert evidence.classify(caddy("/p/suede/", ua="Mozilla/5.0 (compatible; PerplexityBot/1.0)", ip="30.0.0.1"), r)["confirmed"] is True
    google = evidence.classify(caddy("/p/suede/", ua="Googlebot/2.1", ip="2001:4860:4801:10::5"), r)
    assert google["kind"] == "crawler" and google["confirmed"] is True
    ref = evidence.classify(caddy("/p/suede/", referer="https://chatgpt.com/"), r)
    assert (ref["kind"], ref["source"]) == ("ai_referral", "ChatGPT")
    utm = evidence.classify(caddy("/p/suede/?utm_source=perplexity.ai"), r)
    assert (utm["kind"], utm["source"]) == ("ai_referral", "Perplexity")
    assert evidence.classify(caddy("/go/suede", status=302), r)["kind"] == "click"
    assert evidence.classify(caddy("/p/suede/"), r)["kind"] == "visit"
    assert evidence.classify(caddy("/p/suede/thumb.jpg"), r) is None
    assert evidence.classify(caddy("/p/suede/", status=404), r) is None
    assert evidence.classify(caddy("/", ua="Googlebot"), r) is None
    assert evidence.classify(caddy("/p/suede/", method="POST"), r) is None


def test_reads_railway_wrapped_and_raw_lines():
    lines = [railway_line(caddy("/p/a/")), json.dumps(caddy("/p/b/")), "Caddy starting", "{not json",
             json.dumps({"message": "plain text log"})]
    assert [e["request"]["uri"] for e in evidence.access_entries(lines)] == ["/p/a/", "/p/b/"]


def test_deployment_ids_handles_shapes():
    assert evidence.deployment_ids('[{"id":"d1"},{"id":"d2"}]') == ["d1", "d2"]
    assert evidence.deployment_ids('{"deployments":[{"node":{"id":"d3"}}]}') == ["d3"]
    assert evidence.deployment_ids("not json") == []


def test_sync_dedupes_across_deployments_and_never_stores_ips(tmp_path):
    (tmp_path / "measure").mkdir()
    (tmp_path / "measure" / "ranges.json").write_text(json.dumps({"fetched_at": time.time(), "lists": RANGES}))
    fetch = railway_line(caddy("/p/suede/", ua="ChatGPT-User/1.0", ip="20.0.0.9"))
    click = railway_line(caddy("/go/suede", status=302, port="51001"))
    logs = {"d1": [fetch, click], "d2": [fetch]}  # The same request seen twice must count once.

    def run(args, host):
        if args[:2] == ["deployment", "list"]:
            return json.dumps([{"id": "d1"}, {"id": "d2"}, {"id": "d3"}])
        if args[1] == "d3":
            raise RuntimeError("logs expired")
        return "\n".join(logs[args[1]])

    state = evidence.sync(tmp_path, {"service": "s", "project": "p"}, run=run)
    assert state["new_hits"] == 2 and state["deployments"] == 3 and len(state["errors"]) == 1
    assert evidence.sync(tmp_path, {"service": "s", "project": "p"}, run=run)["new_hits"] == 0
    stored = (tmp_path / "measure" / "hits.jsonl").read_text()
    assert "20.0.0.9" not in stored and "9.9.9.9" not in stored
    kinds = sorted(h["kind"] for h in evidence.hits_for(tmp_path, "suede"))
    assert kinds == ["ai_fetch", "click"]


def test_range_refresh_keeps_old_lists_on_failure(tmp_path):
    (tmp_path / "measure").mkdir()
    (tmp_path / "measure" / "ranges.json").write_text(json.dumps({"fetched_at": 0, "lists": {"bing": ["1.2.3.0/24"]}}))

    def fetch(url):
        if "bing" in url:
            raise OSError("offline")
        return {"prefixes": [{"ipv4Prefix": "5.5.5.0/24"}, {"ipv6Prefix": "2001:db8::/32"}]}

    loaded = evidence.load_ranges(tmp_path, fetch=fetch)
    assert str(loaded["bing"][0]) == "1.2.3.0/24"
    assert len(loaded["openai_user"]) == 2


def test_caddyfile_logs_and_redirects_only_safe_links():
    text = golive.caddyfile(["sample"], {"good": "https://instagram.com/reel/abc", "bad": 'https://x.com/"}\nimport evil'})
    assert "format json" in text and "output stdout" in text
    assert '\tredir /go/good "https://instagram.com/reel/abc" 302\n' in text
    assert "evil" not in text and "/go/bad" not in text
    assert '/p/sample/* X-Robots-Tag "noindex"' in text

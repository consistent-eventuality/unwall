"""Turn the public host's access logs into evidence: who fetched each page, and who clicked through.

Visitor IPs are used only to confirm a bot against its operator's published ranges, then dropped.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlparse

import httpx

# Checked in order, so user-triggered fetchers win over the crawlers whose names they contain.
AGENTS = [
    ("chatgpt-user", "ChatGPT", "ai_fetch", "openai_user"),
    ("claude-user", "Claude", "ai_fetch", None),
    ("perplexity-user", "Perplexity", "ai_fetch", "perplexity_user"),
    ("oai-searchbot", "ChatGPT search", "crawler", "openai_search"),
    ("claude-searchbot", "Claude search", "crawler", None),
    ("perplexitybot", "Perplexity", "crawler", "perplexity_bot"),
    ("gptbot", "OpenAI training", "crawler", "openai_gptbot"),
    ("claudebot", "Anthropic training", "crawler", None),
    ("googlebot", "Google", "crawler", "google"),
    ("bingbot", "Bing", "crawler", "bing"),
    ("applebot", "Apple", "crawler", None),
    ("duckduckbot", "DuckDuckGo", "crawler", None),
    ("yandexbot", "Yandex", "crawler", None),
    ("exabot", "Exa", "crawler", None),  # Matches ExaSearchBot too; Exa signs requests instead of publishing IPs.
    ("exasearchbot", "Exa", "crawler", None),
    ("amazonbot", "Amazon", "crawler", None),
]

# Published IP ranges. Anthropic publishes none, so Claude visits stay unconfirmed.
RANGE_URLS = {
    "openai_user": "https://openai.com/chatgpt-user.json",
    "openai_search": "https://openai.com/searchbot.json",
    "openai_gptbot": "https://openai.com/gptbot.json",
    "perplexity_user": "https://www.perplexity.ai/perplexity-user.json",
    "perplexity_bot": "https://www.perplexity.ai/perplexitybot.json",
    "google": "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
    "bing": "https://www.bing.com/toolbox/bingbot.json",
}

REFERRERS = {
    "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT", "perplexity.ai": "Perplexity",
    "claude.ai": "Claude", "gemini.google.com": "Gemini", "copilot.microsoft.com": "Copilot",
    "openai": "ChatGPT",  # The OpenAI API tags cited links with utm_source=openai.
}

PAGE_FILES = {"", "index.html", "page.md", "claims.json"}
STORE_LOCK = threading.Lock()


def measure_dir(data: Path) -> Path:
    path = data / "measure"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- IP ranges ----------

def load_ranges(data: Path, fetch: Callable[[str], dict] | None = None, max_age: float = 86400) -> dict:
    """Return {key: [networks]}, refreshing the cached lists once a day and keeping old ones on failure."""
    cache = measure_dir(data) / "ranges.json"
    stored = json.loads(cache.read_text()) if cache.exists() else {"fetched_at": 0, "lists": {}}
    if time.time() - stored["fetched_at"] > max_age:
        fetch = fetch or (lambda url: httpx.get(url, timeout=15, follow_redirects=True).raise_for_status().json())
        for key, url in RANGE_URLS.items():
            try:
                body = fetch(url)
                stored["lists"][key] = [p.get("ipv4Prefix") or p.get("ipv6Prefix") for p in body["prefixes"]]
            except Exception:  # A list that can't be fetched leaves its bots unconfirmed, not wrong.
                continue
        stored["fetched_at"] = time.time()
        cache.write_text(json.dumps(stored))
    return {key: [ipaddress.ip_network(p, strict=False) for p in prefixes if p]
            for key, prefixes in stored["lists"].items()}


def confirmed(ip: str | None, key: str | None, ranges: dict) -> bool | None:
    """True or False when the operator publishes ranges we have; None when it can't be checked."""
    if not key or key not in ranges:
        return None
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in ranges[key])


# ---------- Parsing ----------

def access_entries(lines: Iterable[str]) -> Iterable[dict]:
    """Yield Caddy access-log entries from Railway's JSON log lines, or from raw Caddy lines."""
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            outer = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates = [outer]
        message = outer.get("message")
        if isinstance(message, str) and message.lstrip().startswith("{"):
            try:
                candidates.insert(0, json.loads(message))
            except json.JSONDecodeError:
                pass
        for entry in candidates:
            if isinstance(entry.get("request"), dict) and "uri" in entry["request"]:
                yield entry
                break


def header(request: dict, name: str) -> str:
    for key, value in (request.get("headers") or {}).items():
        if key.lower() == name.lower():
            return value[0] if isinstance(value, list) and value else str(value or "")
    return ""


def client_ip(request: dict) -> str | None:
    """The visitor's address as seen by Railway's edge, not the proxy's."""
    real = header(request, "X-Real-Ip")
    if real:
        return real.strip()
    forwarded = header(request, "X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.get("client_ip") or request.get("remote_ip")


def referral_source(referer: str, query: dict) -> str | None:
    hosts = [urlparse(referer).netloc.lower()] if referer else []
    hosts += [value.lower() for value in query.get("utm_source", [])]
    for host in hosts:
        host = host.removeprefix("www.")
        for known, name in REFERRERS.items():
            if host == known or host.endswith("." + known):
                return name
    return None


def classify(entry: dict, ranges: dict) -> dict | None:
    """Turn one access-log entry into a hit, or None when it isn't evidence about a page."""
    request = entry["request"]
    status = int(entry.get("status") or 0)
    if not 200 <= status < 400 or request.get("method", "GET") not in {"GET", "HEAD"}:
        return None
    parsed = urlparse(request["uri"])
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "go":
        slug, area, rest = parts[1], "go", ""
    elif len(parts) >= 2 and parts[0] == "p":
        slug, area, rest = parts[1], "page", "/".join(parts[2:])
    else:
        return None
    if area == "page" and rest not in PAGE_FILES:
        return None  # Thumbnails and other assets say nothing about use.

    agent_ua = header(request, "User-Agent")
    ip = client_ip(request)
    ua = agent_ua.lower()
    agent = next((a for a in AGENTS if a[0] in ua), None)
    hit = {"slug": slug, "path": parsed.path, "status": status, "agent": None, "confirmed": None, "source": None}
    if agent:
        hit.update(kind=agent[2], agent=agent[1], confirmed=confirmed(ip, agent[3], ranges))
    elif area == "go":
        hit["kind"] = "click"
    else:
        source = referral_source(header(request, "Referer"), parse_qs(parsed.query))
        hit.update(kind="ai_referral" if source else "visit", source=source)
    when = entry.get("ts")
    hit["ts"] = (datetime.fromtimestamp(float(when), timezone.utc).isoformat(timespec="seconds")
                 if isinstance(when, (int, float)) else str(when or ""))
    # A stable id for de-duplication. The IP goes into a one-way hash and is not stored.
    seed = f"{when}|{request.get('uri')}|{ip}|{agent_ua}|{request.get('remote_port')}"
    hit["id"] = hashlib.sha256(seed.encode()).hexdigest()[:20]
    return hit


# ---------- Store ----------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_hits(data: Path, hits: list[dict]) -> int:
    path = measure_dir(data) / "hits.jsonl"
    with STORE_LOCK:
        seen = {hit["id"] for hit in read_jsonl(path)}
        fresh = []
        for hit in hits:
            if hit["id"] not in seen:
                seen.add(hit["id"])
                fresh.append(hit)
        if fresh:
            with path.open("a") as out:
                out.writelines(json.dumps(hit) + "\n" for hit in fresh)
    return len(fresh)


def hits_for(data: Path, slug: str) -> list[dict]:
    return [hit for hit in read_jsonl(measure_dir(data) / "hits.jsonl") if hit["slug"] == slug]


# ---------- Sync from Railway ----------

def railway(args: list[str], host: dict) -> str:
    scope = ["--service", host["service"], "--project", host["project"], "--environment", host.get("environment", "production")]
    result = subprocess.run(["railway", *args, *scope], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:500] or "railway command failed")
    return result.stdout


def deployment_ids(raw: str) -> list[str]:
    """Pull deployment ids out of `railway deployment list --json`, whatever its exact nesting."""
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = body if isinstance(body, list) else next((v for v in body.values() if isinstance(v, list)), []) if isinstance(body, dict) else []
    ids = []
    for item in items:
        node = item.get("node", item) if isinstance(item, dict) else {}
        if isinstance(node.get("id"), str):
            ids.append(node["id"])
    return ids


def sync(data: Path, host: dict, run: Callable[[list[str], dict], str] = railway, lines: int = 5000) -> dict:
    """Fetch access logs from recent deployments and store new hits. Safe to run repeatedly."""
    ranges = load_ranges(data)
    errors = []
    try:
        ids = deployment_ids(run(["deployment", "list", "--json", "--limit", "30"], host))
    except Exception as exc:
        ids, errors = [], [f"Couldn't list deployments: {exc}"]
    targets = ids or [None]  # None means the latest deployment.
    hits, scanned = [], 0
    for deployment in targets:
        args = ["logs", *([deployment] if deployment else []), "--deployment", "--json", "--lines", str(lines)]
        try:
            raw = run(args, host)
        except Exception as exc:
            errors.append(f"Couldn't read logs for {deployment or 'latest deployment'}: {exc}")
            continue
        for entry in access_entries(raw.splitlines()):
            scanned += 1
            hit = classify(entry, ranges)
            if hit:
                hits.append(hit)
    added = append_hits(data, hits)
    state = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "deployments": len(targets),
             "requests": scanned, "new_hits": added, "errors": errors}
    (measure_dir(data) / "last_sync.json").write_text(json.dumps(state))
    return state


def last_sync(data: Path) -> dict | None:
    path = measure_dir(data) / "last_sync.json"
    return json.loads(path.read_text()) if path.exists() else None

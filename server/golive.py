"""Deploy approved pages to a static public host, prove they are live, then submit them.

Only rendered pages leave this machine. The public host has no API, uploads, or keys.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from . import render

INDEXNOW_URL = os.environ.get("UNWALL_INDEXNOW_URL", "https://api.indexnow.org/indexnow")
VERIFY_SECONDS = int(os.environ.get("UNWALL_VERIFY_SECONDS", "240"))
DEPLOY_LOCK = threading.Lock()  # Each deploy ships the whole site, so they must not overlap.

DOCKERFILE = """FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY site /srv
"""


class LaunchError(Exception):
    """A failure with a message that is safe and useful to show the creator."""


def host_config(data: Path) -> dict | None:
    path = data / "host.json"
    if not path.exists():
        return None
    host = json.loads(path.read_text())
    return host if all(host.get(k) for k in ("public_url", "project", "service")) else None


def indexnow_key(data: Path) -> str:
    path = data / "indexnow.key"
    if not path.exists():
        path.write_text(secrets.token_hex(16))
    return path.read_text().strip()


def caddyfile(noindex: list[str], redirects: dict[str, str]) -> str:
    rules = "".join(f'\theader /p/{slug}/* X-Robots-Tag "noindex"\n' for slug in noindex)
    # "Watch the original" goes through /go/<slug> so each click lands in the access log.
    # Links were checked by render.safe_link, so they can't break out of the quotes.
    rules += "".join(f'\tredir /go/{slug} "{url}" 302\n' for slug, url in sorted(redirects.items())
                     if render.safe_link(url))
    return ("{\n\tadmin off\n\tauto_https off\n}\n\n:{$PORT:8080} {\n\tlog {\n\t\toutput stdout\n\t\tformat json\n\t}\n"
            "\troot * /srv\n\tfile_server\n"
            '\theader X-Content-Type-Options nosniff\n\theader Cache-Control "public, max-age=60"\n'
            '\theader /p/*/page.md Content-Type "text/markdown; charset=utf-8"\n'
            '\theader /go/* X-Robots-Tag "noindex"\n'
            f"{rules}}}\n")


def home_page(pages: Path) -> str:
    links = []
    for page in sorted(pages.glob("*/index.html")):
        if render.is_noindex(page):
            continue
        claims = json.loads((page.parent / "claims.json").read_text())
        links.append(f'<li><a href="/p/{page.parent.name}/">{escape(claims["question"])}</a> by {escape(claims["creator"])}</li>')
    body = f"<ul>{''.join(links)}</ul>" if links else "<p>No public pages yet.</p>"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Source pages</title></head><body><main><h1>Source pages</h1>{body}</main></body></html>")


def build(job: dict, job_dir: Path, live: Path, url: str, key: str, out: Path) -> Path:
    site = out / "site"
    pages = site / "p"
    if live.exists():
        shutil.copytree(live, pages)
    pages.mkdir(parents=True, exist_ok=True)
    render.publish(job, job_dir, pages, url)
    (site / "index.html").write_text(home_page(pages), encoding="utf-8")
    (site / "robots.txt").write_text(render.robots(url), encoding="utf-8")
    (site / "sitemap.xml").write_text(render.sitemap(pages, url), encoding="utf-8")
    (site / f"{key}.txt").write_text(key, encoding="utf-8")
    noindex = [p.parent.name for p in sorted(pages.glob("*/index.html")) if render.is_noindex(p)]
    redirects = {p.parent.name: json.loads(p.read_text())["source_url"] for p in sorted(pages.glob("*/claims.json"))}
    (out / "Caddyfile").write_text(caddyfile(noindex, redirects), encoding="utf-8")
    (out / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    return site


def deploy(out: Path, host: dict) -> None:
    command = ["railway", "up", str(out), "--path-as-root", "--ci", "--service", host["service"],
               "--project", host["project"], "--environment", host.get("environment", "production")]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise LaunchError("The Railway CLI isn't installed on this computer.") from exc
    except subprocess.TimeoutExpired as exc:
        raise LaunchError("The host took too long to deploy. Try again.") from exc
    if result.returncode:
        print(result.stdout[-3000:], result.stderr[-3000:], flush=True)
        raise LaunchError("The host rejected this deploy. Check the server log, then try again.")


def verify(site: Path, slug: str, base: str, key: str) -> None:
    """Wait until the public host serves these exact files, so 'live' is never a guess."""
    paths = [f"p/{slug}/index.html", f"p/{slug}/page.md", f"p/{slug}/claims.json", "robots.txt", "sitemap.xml", f"{key}.txt"]
    public = {p: f"{base}/{p.removesuffix('index.html')}" for p in paths}
    deadline = time.monotonic() + VERIFY_SECONDS
    waiting = paths
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        while True:
            still = []
            for path in waiting:
                try:
                    response = client.get(public[path], params={"v": secrets.token_hex(4)} if path.endswith("index.html") else None)
                    if response.status_code != 200 or response.content != (site / path).read_bytes():
                        still.append(path)
                except httpx.HTTPError:
                    still.append(path)
            waiting = still
            if not waiting:
                return
            if time.monotonic() > deadline:
                print(f"Not live after {VERIFY_SECONDS}s: {waiting}", flush=True)
                raise LaunchError("The deploy finished, but the public page didn't appear in time. Try again.")
            time.sleep(min(4, max(0.2, VERIFY_SECONDS / 60)))


def submit_indexnow(url: str, key: str) -> dict:
    host = urlparse(url).netloc
    scheme = urlparse(url).scheme
    body = {"host": host.split(":")[0], "key": key, "keyLocation": f"{scheme}://{host}/{key}.txt", "urlList": [url]}
    reasons = {400: "Rejected as badly formatted.", 403: "The ownership key wasn't accepted.",
               422: "The URL didn't match this site.", 429: "Rate limited. Try again later."}
    detail = "Couldn't reach IndexNow."
    for attempt in range(3):
        try:
            response = httpx.post(INDEXNOW_URL, json=body, timeout=15,
                                  headers={"Content-Type": "application/json; charset=utf-8"})
        except httpx.HTTPError:
            response = None
        if response is not None:
            if response.status_code == 200:
                return {"status": "received", "detail": "Received. Crawling and indexing are up to each engine."}
            if response.status_code == 202:
                return {"status": "received", "detail": "Received. The engine is still checking the ownership key."}
            detail = reasons.get(response.status_code, f"IndexNow returned {response.status_code}.")
            if response.status_code in {400, 422, 429}:
                break
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    return {"status": "failed", "detail": detail}


def targets(submission: dict | None, sample: bool) -> list[dict]:
    if sample:
        note = "Skipped. Sample pages are marked noindex."
        return [{"name": name, "status": "skipped", "detail": note}
                for name in ("Bing and IndexNow engines", "Google", "ChatGPT", "Claude", "Perplexity")]
    crawl = "Crawler allowed. There's no public submission route, so it finds pages by crawling."
    return [
        {"name": "Bing and IndexNow engines", **(submission or {"status": "failed", "detail": "Not submitted."})},
        {"name": "Google", "status": "discoverable", "detail": "Listed in the sitemap. Direct submission needs a verified Search Console site."},
        {"name": "ChatGPT", "status": "discoverable", "detail": crawl},
        {"name": "Claude", "status": "discoverable", "detail": crawl},
        {"name": "Perplexity", "status": "discoverable", "detail": crawl},
    ]


def launch(job: dict, job_dir: Path, data: Path, host: dict, step: Callable[[str], None]) -> dict:
    base = host["public_url"].rstrip("/")
    live = data / "live"
    key = indexnow_key(data)
    step("waiting")
    with DEPLOY_LOCK:
        with tempfile.TemporaryDirectory(prefix="unwall-") as temp:
            out = Path(temp)
            step("building")
            site = build(job, job_dir, live, base, key, out)
            step("deploying")
            deploy(out, host)
            step("verifying")
            verify(site, job["slug"], base, key)
            # Record the page only after the public host proves it has it.
            target = live / job["slug"]
            if target.exists():
                shutil.rmtree(target)
            live.mkdir(parents=True, exist_ok=True)
            shutil.copytree(site / "p" / job["slug"], target)
    url = f"{base}/p/{job['slug']}/"
    sample = bool(job.get("sample"))
    step("submitting")
    submission = None if sample else submit_indexnow(url, key)
    return {"url": url, "targets": targets(submission, sample), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

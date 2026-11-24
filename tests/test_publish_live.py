"""Publish live, end to end, against a local stand-in host and a fake IndexNow endpoint.

No network calls leave this machine and no AI models are called.
"""
from __future__ import annotations

import http.server
import json
import os
import stat
import threading
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A fake `railway` CLI: "deploying" copies the bundle into the stand-in host's folder.
    fake = bin_dir / "railway"
    fake.write_text(f'#!/bin/sh\n[ -f "{tmp_path}/fail_deploy" ] && exit 1\n'
                    f'rm -rf "{public}"/*; cp -R "$2"/site/. "{public}"/; cp "$2"/Caddyfile "{tmp_path}/Caddyfile"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    submissions: list[dict] = []
    codes: list[int] = []

    class Host(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(public), **kwargs)

        def log_message(self, *args):
            pass

        def do_POST(self):
            submissions.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(codes.pop(0) if codes else 200)
            self.end_headers()

    host = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=host.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{host.server_port}"
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    (data / "host.json").write_text(json.dumps({"public_url": base, "project": "p", "service": "s"}))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("UNWALL_DATA", str(data))
    monkeypatch.setenv("UNWALL_INDEXNOW_URL", f"{base}/indexnow")
    monkeypatch.setenv("UNWALL_VERIFY_SECONDS", "3")
    monkeypatch.setenv("UNWALL_BASE_URL", "http://127.0.0.1:8799")
    from fastapi.testclient import TestClient
    import importlib
    import server.golive
    import server.app
    importlib.reload(server.golive)
    app_module = importlib.reload(server.app)
    client = TestClient(app_module.app, base_url="http://127.0.0.1:8799")
    yield {"client": client, "base": base, "data": data, "tmp": tmp_path, "public": public,
           "submissions": submissions, "codes": codes}
    host.shutdown()


def make_job(data: Path, job_id: str, sample: str | None = None, question: str = "How do you clean suede?") -> None:
    folder = data / "jobs" / job_id
    (folder / "frames").mkdir(parents=True)
    Image.new("RGB", (20, 20)).save(folder / "frames" / "00.jpg")
    (folder / "video.mp4").write_bytes(b"x")
    claim = {"text": "Use a brush", "start_s": 1, "end_s": 2, "source": "speech", "evidence": "brush"}
    (folder / "job.json").write_text(json.dumps({
        "id": job_id, "status": "review", "error": None, "video": "video.mp4", "filename": "v.mp4", "sample": sample,
        "duration": 10, "transcript": [], "frames": [{"t": 0, "file": "00.jpg"}], "slug": None, "url": None, "approved_at": None,
        "draft": {"title": "T", "question": question, "answer": "Use a brush.", "claims": [claim],
                  "related_questions": [], "topics": [], "watch_reason": "", "needs_review": []},
        "meta": {"creator": "Sam", "credential": "", "platform": "Instagram", "followers": "", "profile_url": "",
                 "source_url": "https://instagram.com/reel/x", "views": "", "views_count": "", "stats_date": "",
                 "posted_date": "2026-11-01"},
    }))


def settle(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["launch"]["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("launch never finished")


def test_publish_live(world):
    client, base, data, sent = world["client"], world["base"], world["data"], world["submissions"]
    real, sample, second = "a" * 12, "b" * 12, "c" * 12
    make_job(data, real)
    make_job(data, sample, sample="demo", question="Sample question?")
    make_job(data, second, question="Second real page?")

    assert client.post(f"/api/jobs/{real}/publish", json={"consent": False}).status_code == 422

    # A failed deploy is reported as failed and never recorded as live.
    (world["tmp"] / "fail_deploy").touch()
    client.post(f"/api/jobs/{real}/publish", json={"consent": True})
    job = settle(client, real)
    assert job["launch"]["state"] == "failed" and not sent
    assert not (data / "live" / job["slug"]).exists()

    (world["tmp"] / "fail_deploy").unlink()
    assert client.post(f"/api/jobs/{real}/launch").status_code == 200
    job = settle(client, real)
    assert job["launch"]["state"] == "live"
    url = job["launch"]["url"]
    html = httpx.get(url).text
    assert f'rel="canonical" href="{url}"' in html
    assert "8799" not in html and 'content="noindex"' not in html

    # The studio's preview tabs show the public copy, not the local one.
    assert f"Canonical page: {url}" in client.get(f"/api/jobs/{real}/output/page.md").text

    key = (data / "indexnow.key").read_text()
    assert httpx.get(f"{base}/{key}.txt").text == key
    assert url in httpx.get(f"{base}/sitemap.xml").text
    assert f"Sitemap: {base}/sitemap.xml" in httpx.get(f"{base}/robots.txt").text
    assert sent[-1] == {"host": "127.0.0.1", "key": key, "keyLocation": f"{base}/{key}.txt", "urlList": [url]}

    # Only static pages are public: no API, videos, or job files.
    assert httpx.get(f"{base}/api/jobs/{real}").status_code == 404
    assert not list(world["public"].rglob("*.mp4")) and not list(world["public"].rglob("job.json"))

    # Sample pages go live as noindex and are never submitted.
    count = len(sent)
    client.post(f"/api/jobs/{sample}/publish", json={"consent": True})
    demo = settle(client, sample)
    assert demo["launch"]["state"] == "live" and len(sent) == count
    assert all(t["status"] == "skipped" for t in demo["launch"]["targets"])
    assert 'content="noindex"' in httpx.get(demo["launch"]["url"]).text
    assert f'/p/{demo["slug"]}/* X-Robots-Tag' in (world["tmp"] / "Caddyfile").read_text()
    assert demo["slug"] not in httpx.get(f"{base}/sitemap.xml").text
    assert demo["slug"] not in httpx.get(f"{base}/").text
    assert httpx.get(url).status_code == 200  # Earlier pages survive later deploys.

    # A rejected key is shown as a failed submission; the page itself stays live.
    world["codes"].extend([403, 403, 403])
    client.post(f"/api/jobs/{second}/publish", json={"consent": True})
    other = settle(client, second)
    assert other["launch"]["state"] == "live" and other["launch"]["targets"][0]["status"] == "failed"
    world["codes"].append(202)
    client.post(f"/api/jobs/{second}/launch")
    other = settle(client, second)
    assert other["launch"]["targets"][0]["status"] == "received"
    assert all(t["status"] == "discoverable" for t in other["launch"]["targets"][1:])

    # An edit that hasn't been re-approved never ships with someone else's deploy.
    client.patch(f"/api/jobs/{real}", json={"draft": {**job["draft"], "answer": "UNAPPROVED EDIT"}})
    client.post(f"/api/jobs/{second}/launch")
    settle(client, second)
    assert "UNAPPROVED EDIT" not in httpx.get(url).text

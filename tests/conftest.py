"""Shared test setup: a local stand-in public host, a fake railway CLI, and a fake IndexNow."""
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
    monkeypatch.setenv("UNWALL_BASELINE", "0")  # Tests never call the real, paid engines.
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


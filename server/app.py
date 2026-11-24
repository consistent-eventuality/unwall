"""Local API and static host. Jobs are JSON files on disk; there is no database."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import traceback
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import golive, pipeline, render

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("UNWALL_DATA", ROOT / "data"))
JOBS = DATA / "jobs"
SITE = DATA / "site"
SAMPLES = ROOT / "samples"
WEB = ROOT / "web" / "dist"
BASE_URL = os.environ.get("UNWALL_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
MAX_UPLOAD = 500 * 1024 * 1024
ALLOWED = {".mp4", ".mov", ".m4v", ".webm"}
SAMPLE_INFO = {
    "narrated": {"title": "Why white midsoles turn yellow", "kind": "Spoken", "note": "Knowledge lives in the voiceover"},
    "onscreen": {"title": "Suede rescue in 4 moves", "kind": "On-screen text", "note": "No speech at all"},
    "demo": {"title": "Deep clean knit sneakers", "kind": "Demonstration", "note": "Voice and visuals each hold facts"},
}

JOBS.mkdir(parents=True, exist_ok=True)
SITE.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
app = FastAPI(title="Unwall")
TRUSTED_HOSTS = {"127.0.0.1:8787", "localhost:8787", "127.0.0.1:5173", "localhost:5173", urlparse(BASE_URL).netloc}

# A job interrupted by a restart has no worker left, so it must not spin forever.
for stale in JOBS.glob("*/job.json"):
    record = json.loads(stale.read_text())
    if record["status"] in {"queued", "reading", "listening", "watching", "writing"}:
        record.update(status="failed", error="Processing was interrupted. Try this video again.")
        stale.write_text(json.dumps(record, ensure_ascii=False))
    elif (record.get("launch") or {}).get("state") == "running":
        record["launch"].update(state="failed", error="Going live was interrupted. Try again.")
        stale.write_text(json.dumps(record, ensure_ascii=False))


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Stop other websites from triggering paid processing on this local server."""
    if request.headers.get("host") not in TRUSTED_HOSTS:
        return PlainTextResponse("Untrusted host", status_code=403)
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD"} and origin and urlparse(origin).netloc not in TRUSTED_HOSTS:
        return PlainTextResponse("Cross-site request blocked", status_code=403)
    return await call_next(request)


class Patch(BaseModel):
    draft: dict | None = None
    meta: dict | None = None


class PublishBody(BaseModel):
    consent: bool = False


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise HTTPException(404, "Job not found")
    path = JOBS / job_id
    if not (path / "job.json").exists():
        raise HTTPException(404, "Job not found")
    return path


def load(job_id: str) -> dict:
    with lock:
        return json.loads((job_dir(job_id) / "job.json").read_text())


def save(job: dict) -> None:
    with lock:
        path = JOBS / job["id"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False))
        temp.replace(path)


def worker(job_id: str) -> None:
    folder = JOBS / job_id
    job = load(job_id)

    def stage(name: str, fields: dict) -> None:
        job.update(fields)
        job["status"] = name
        save(job)

    try:
        pipeline.run(folder / job["video"], folder, stage)
    except pipeline.PipelineError as exc:
        job.update(status="failed", error=str(exc))
        save(job)
    except Exception:
        traceback.print_exc()
        job.update(status="failed", error="Processing failed. Check the server log, then try again.")
        save(job)


def start(video_name: str, original: str, sample: str | None, folder: Path, job_id: str) -> dict:
    today = date.today()
    job = {
        "id": job_id, "status": "queued", "error": None, "video": video_name, "filename": original, "sample": sample,
        "duration": 0, "transcript": [], "frames": [], "draft": None, "slug": None, "url": None, "approved_at": None,
        "meta": {
            "creator": "Mara Voss" if sample else "", "credential": "Sneaker restorer (sample creator)" if sample else "",
            "platform": "Instagram", "followers": "412K" if sample else "", "profile_url": "https://example.com/mara-voss" if sample else "",
            "source_url": f"https://example.com/videos/{sample}" if sample else "", "views": "2.1M" if sample else "",
            "views_count": "2100000" if sample else "", "stats_date": today.strftime("%B %Y"), "posted_date": today.isoformat(),
        },
    }
    (folder / "job.json").write_text(json.dumps(job))
    threading.Thread(target=worker, args=(job_id,), daemon=True).start()
    return job


@app.get("/api/samples")
def samples() -> list[dict]:
    return [{"id": key, **value} for key, value in SAMPLE_INFO.items() if (SAMPLES / f"{key}.mp4").exists()]


@app.get("/api/samples/{sample}/poster")
def sample_video(sample: str):
    if sample not in SAMPLE_INFO:
        raise HTTPException(404)
    return ranged(SAMPLES / f"{sample}.mp4", None)


@app.post("/api/jobs/sample/{sample}", status_code=201)
def create_sample(sample: str) -> dict:
    source = SAMPLES / f"{sample}.mp4"
    if sample not in SAMPLE_INFO or not source.exists():
        raise HTTPException(404, "Sample not found")
    job_id = uuid.uuid4().hex[:12]
    folder = JOBS / job_id
    folder.mkdir()
    shutil.copyfile(source, folder / "video.mp4")
    return start("video.mp4", f"{sample}.mp4", sample, folder, job_id)


@app.post("/api/jobs", status_code=201)
def create_job(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "Use an MP4, MOV, M4V, or WebM video.")
    job_id = uuid.uuid4().hex[:12]
    folder = JOBS / job_id
    folder.mkdir()
    name = f"video{suffix}"
    written = 0
    with (folder / name).open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD:
                out.close()
                shutil.rmtree(folder)
                raise HTTPException(413, "Videos can be up to 500 MB.")
            out.write(chunk)
    return start(name, Path(file.filename).name, None, folder, job_id)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return load(job_id)


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, body: Patch) -> dict:
    job = load(job_id)
    if job["status"] not in {"review", "published"}:
        raise HTTPException(409, "This video isn't ready to edit.")
    if launching(job):
        raise HTTPException(409, "This page is going live. Edit it when that finishes.")
    if body.draft is not None:
        try:
            draft = pipeline.Draft.model_validate(body.draft)
        except ValidationError as exc:
            raise HTTPException(422, "That edit isn't valid.") from exc
        job["draft"] = pipeline.clamp(draft, job["duration"]).model_dump()
    if body.meta is not None:
        allowed = set(job["meta"])
        job["meta"].update({k: str(v).strip() for k, v in body.meta.items() if k in allowed})
    if job["status"] == "published":
        job["status"] = "review"  # An edit needs a fresh approval before it goes live again.
    save(job)
    return job


def is_web_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def launching(job: dict) -> bool:
    return (job.get("launch") or {}).get("state") == "running"


def launch_worker(job_id: str, host: dict) -> None:
    def update(**fields) -> None:
        current = load(job_id)
        current["launch"].update(fields)
        save(current)

    try:
        result = golive.launch(load(job_id), JOBS / job_id, DATA, host, lambda name: update(step=name))
        update(state="live", step="done", error=None, **result)
    except golive.LaunchError as exc:
        update(state="failed", error=str(exc))
    except Exception:
        traceback.print_exc()
        update(state="failed", error="Going live failed. Check the server log, then try again.")


def start_launch(job: dict) -> None:
    """Mark the launch running, then deploy in the background."""
    host = golive.host_config(DATA)
    previous = job.get("launch") or {}
    if not host:
        job["launch"] = {"state": "not_configured", "step": None, "error": None, "url": None, "targets": []}
        save(job)
        return
    job["launch"] = {"state": "running", "step": "waiting", "error": None, "url": previous.get("url"), "targets": []}
    save(job)
    threading.Thread(target=launch_worker, args=(job["id"], host), daemon=True).start()


@app.post("/api/jobs/{job_id}/publish")
def publish(job_id: str, body: PublishBody) -> dict:
    job = load(job_id)
    if job["status"] not in {"review", "published"} or not job["draft"]:
        raise HTTPException(409, "This video isn't ready to publish.")
    if launching(job):
        raise HTTPException(409, "This page is already going live.")
    meta, draft = job["meta"], job["draft"]
    problems = []
    if not body.consent:
        problems.append("Confirm you have the right to publish this content.")
    if not meta["creator"]:
        problems.append("Add the creator's name.")
    if not is_web_url(meta["source_url"]):
        problems.append("Add a valid link to the original video.")
    if meta.get("profile_url") and not is_web_url(meta["profile_url"]):
        problems.append("Use a valid profile link.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["posted_date"]):
        problems.append("Use a valid posted date.")
    if not draft["question"].strip() or not draft["answer"].strip() or not draft["claims"]:
        problems.append("Keep a question, an answer, and at least one claim.")
    if problems:
        raise HTTPException(422, " ".join(problems))

    if not job["slug"]:
        base = render.slugify(draft["question"])
        slug, n = base, 2
        while (SITE / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        job["slug"] = slug
    job["approved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    job["url"] = render.publish(job, JOBS / job_id, SITE, BASE_URL)
    job["status"] = "published"
    start_launch(job)
    return load(job_id)


@app.post("/api/jobs/{job_id}/launch")
def retry_launch(job_id: str) -> dict:
    job = load(job_id)
    if job["status"] != "published":
        raise HTTPException(409, "Approve this page first.")
    if launching(job):
        raise HTTPException(409, "This page is already going live.")
    start_launch(job)
    return load(job_id)


@app.get("/api/jobs/{job_id}/output/{name}")
def output(job_id: str, name: str):
    job = load(job_id)
    if name not in {"page.md", "claims.json"} or not job.get("slug"):
        raise HTTPException(404)
    # Once live, show the copy the public host actually serves, with its public links.
    live = DATA / "live" / job["slug"] / name
    return PlainTextResponse((live if live.exists() else SITE / job["slug"] / name).read_text())


@app.get("/api/jobs/{job_id}/frames/{name}")
def frame(job_id: str, name: str):
    if not re.fullmatch(r"\d{2}\.jpg", name):
        raise HTTPException(404)
    path = job_dir(job_id) / "frames" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


def ranged(path: Path, header: str | None):
    size = path.stat().st_size
    media = {".mov": "video/quicktime", ".webm": "video/webm"}.get(path.suffix.lower(), "video/mp4")
    start_at, end_at, status = 0, size - 1, 200
    if header and (match := re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())) and any(match.groups()):
        first, last = match.groups()
        if first:
            start_at, end_at = int(first), min(int(last), size - 1) if last else size - 1
        else:
            start_at = max(0, size - int(last))
        if start_at > end_at or start_at >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    def stream():
        with path.open("rb") as handle:
            handle.seek(start_at)
            left = end_at - start_at + 1
            while left > 0 and (chunk := handle.read(min(256 * 1024, left))):
                left -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end_at - start_at + 1)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start_at}-{end_at}/{size}"
    return StreamingResponse(stream(), status_code=status, media_type=media, headers=headers)


@app.get("/api/jobs/{job_id}/video")
def video(job_id: str, request: Request):
    job = load(job_id)
    return ranged(JOBS / job_id / job["video"], request.headers.get("range"))


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return render.robots(BASE_URL)


@app.get("/sitemap.xml")
def sitemap() -> Response:
    return Response(render.sitemap(SITE, BASE_URL), media_type="application/xml")


app.mount("/p", StaticFiles(directory=SITE, html=True), name="pages")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    candidate = (WEB / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(WEB.resolve()):
        return FileResponse(candidate)
    index = WEB / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(404, "Run `npm run build` in web/ first.")
